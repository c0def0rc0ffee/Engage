"""
<summary>
Dialog-driven slot management UI for Engage.
</summary>
<remarks>
Everything is built from Kodi's native dialogs (select, input, yesno) so it
works equally well with a remote, keyboard, or touch. (Dialog().numeric is
deliberately avoided, it renders a blank window on some skins.)
</remarks>
"""

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import functional_lists
from resources.lib import slots as store
from resources.lib.favourites_ui import _get_favourites, _maybe_filter_video

DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def _log(msg, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to kodi.log under the 'Engage manager:' prefix.
    </summary>
    <param name="msg">Text to log.</param>
    <param name="level">Kodi log level, LOGINFO by default.</param>
    """
    xbmc.log('Engage manager: {}'.format(msg), level)


def _version():
    """
    <summary>
    'v1.10.0', or '' if Kodi won't tell us (the heading still reads fine).
    </summary>
    """
    try:
        version = xbmcaddon.Addon('service.engage').getAddonInfo('version')
        return 'v{}'.format(version) if version else ''
    except Exception:
        return ''


def _notify(message, icon=xbmcgui.NOTIFICATION_INFO, duration=4000):
    """
    <summary>
    Pop a Kodi toast notification titled Engage.
    </summary>
    <param name="message">Text of the toast.</param>
    <param name="icon">An xbmcgui.NOTIFICATION_* constant, info by default.</param>
    <param name="duration">How long it stays up, in milliseconds, 4000 by default.</param>
    """
    xbmcgui.Dialog().notification('Engage', message, icon, duration)


def _pick_favourite():
    """
    <summary>
    Select dialog over current (video-filtered) favourites. Returns title or None.
    </summary>
    """
    favs = _maybe_filter_video(_get_favourites())
    if not favs:
        _notify('No favourites found.', xbmcgui.NOTIFICATION_WARNING)
        return None
    titles = [f.get('title', '(untitled)') for f in favs]
    idx = xbmcgui.Dialog().select('Pick favourite', titles)
    return titles[idx] if idx >= 0 else None


def _pick_time(current_hour, current_minute):
    """
    <summary>
    HH:MM time selector via two select lists. Returns (hour, minute) or None.
    </summary>
    <remarks>
    Hour list 00-23, then full minute list 00-59, no steps, any exact minute.
    (Kodi's numeric time dialog renders a blank window on some skins, so we
    avoid it and use select lists, which work everywhere.)
    </remarks>
    """
    hours = ['{:02d}'.format(h) for h in range(24)]
    h_idx = xbmcgui.Dialog().select('Start time, hour (HH)', hours, preselect=current_hour)
    if h_idx < 0:
        return None

    minutes = ['{:02d}:{:02d}'.format(h_idx, m) for m in range(60)]
    m_idx = xbmcgui.Dialog().select('Start time, minute (MM)', minutes, preselect=current_minute)
    if m_idx < 0:
        return None

    return h_idx, m_idx


def _pick_warning(current):
    """
    <summary>
    Warning-minutes selector via a select list (0-30 min). Returns int or None.
    Avoids Dialog().numeric, which renders blank on some skins.
    </summary>
    """
    values = list(range(0, 31))
    labels = ['{} min'.format(v) for v in values]
    labels[0] = 'No warning (0 min)'
    pre = current if current in values else 5
    idx = xbmcgui.Dialog().select('Warning minutes before start', labels, preselect=pre)
    if idx < 0:
        return None
    return values[idx]


def _pick_timeout_action(current):
    """
    <summary>
    What the slot does if the banner's countdown runs out untouched.
    Returns an action id, or None if the user backed out.
    </summary>
    """
    labels = [store.TIMEOUT_ACTION_LABELS[a] for a in store.TIMEOUT_ACTIONS]
    try:
        pre = store.TIMEOUT_ACTIONS.index(current)
    except ValueError:
        pre = store.TIMEOUT_ACTIONS.index(store.DEFAULT_TIMEOUT_ACTION)
    idx = xbmcgui.Dialog().select('When the countdown ends', labels, preselect=pre)
    if idx < 0:
        return None
    return store.TIMEOUT_ACTIONS[idx]


def _pick_banner_style(current):
    """
    <summary>
    Which banner layout the slot's prompt uses: the master default, standard
    or thin. Returns a style id, or None if the user backed out.
    </summary>
    <param name="current">The slot's current choice, as store.banner_style_of returns it.</param>
    """
    labels = [store.BANNER_STYLE_LABELS[b] for b in store.SLOT_BANNER_STYLES]
    try:
        pre = store.SLOT_BANNER_STYLES.index(current)
    except ValueError:
        pre = store.SLOT_BANNER_STYLES.index(store.DEFAULT_SLOT_BANNER_STYLE)
    idx = xbmcgui.Dialog().select('Banner style', labels, preselect=pre)
    if idx < 0:
        return None
    return store.SLOT_BANNER_STYLES[idx]


def _slot_line(slot):
    """
    <summary>
    One-line summary for the slot list.
    </summary>
    """
    if slot.get('kind') == 'list':
        name = '{} [list, {}]'.format(
            slot.get('label') or slot.get('list_name') or '(no list)',
            functional_lists.mode_text(slot))
    elif slot.get('kind') == 'sequence':
        count = len(store.items_of(slot))
        noun = 'episodes' if slot.get('seq_type') == 'binge' else 'films'
        name = '{} [{} {}]'.format(slot.get('label') or 'Sequence', count, noun)
    else:
        name = slot.get('label') or slot.get('favourite') or '(no favourite)'
    when = _schedule_text(slot)
    line = '{}, {} {:02d}:{:02d}'.format(name, when, slot.get('hour', 0), slot.get('minute', 0))
    if not slot.get('enabled'):
        line += '  [disabled]'
    return line


def manage_slots():
    """
    <summary>
    Manage slots: the full screen window, or the plain list when that cannot open.
    </summary>
    <remarks>
    SLOTS-WINDOW, 22/10/2026: the window (slots_window.py) sorts and shows every
    slot; the plain select list below stays as the fallback, so a skin that cannot
    draw the window still leaves the slots manageable.
    </remarks>
    """
    from resources.lib import slots_window
    if slots_window.open_slots_window():
        return
    _manage_slots_list()


def add_slot_menu():
    """
    <summary>
    The four ways to add a slot, as one menu, for the window's Add slot button.
    </summary>
    <remarks>
    The same four entries and functions as the plain list's add lines. Backing out
    adds nothing.
    </remarks>
    """
    choices = [
        ('Single show or film (a favourite)...', _add_slot),
        ('Film sequence...', _add_sequence),
        ('Binge order (crossover)...', _add_binge),
        ('From a Functional skin list...', _add_list_slot),
    ]
    idx = xbmcgui.Dialog().select('Add a slot', [label for label, _ in choices])
    if idx >= 0:
        choices[idx][1]()


def _manage_slots_list():
    """
    <summary>
    Top-level slot list as a plain select dialog. Loops until the user backs out.
    </summary>
    <remarks>
    The original Manage slots screen, kept as manage_slots' fallback.
    </remarks>
    """
    while True:
        slots = store.load_slots()
        items = [_slot_line(s) for s in slots]
        items.append('+ Add new slot (single show/movie)...')
        items.append('+ Add movie sequence...')
        items.append('+ Add binge order (crossover)...')
        items.append('+ Add from a Functional skin list...')
        items.append('[COLOR cyan]Backup / Restore...[/COLOR]')
        heading = ' '.join(p for p in ('Engage', _version()) if p)
        idx = xbmcgui.Dialog().select('{}, Slots'.format(heading), items)
        if idx < 0:
            return
        if idx == len(slots):
            _add_slot()
        elif idx == len(slots) + 1:
            _add_sequence()
        elif idx == len(slots) + 2:
            _add_binge()
        elif idx == len(slots) + 3:
            _add_list_slot()
        elif idx == len(slots) + 4:
            _backup_restore_menu()
        else:
            _edit_slot(slots[idx]['id'])


def _backup_restore_menu():
    """
    <summary>
    Backup to / restore from a JSON file the user chooses.
    </summary>
    """
    options = ['Backup now (save to file)...', 'Restore from backup file...']
    idx = xbmcgui.Dialog().select('Engage, Backup / Restore', options)
    if idx == 0:
        _do_backup()
    elif idx == 1:
        _do_restore()


def _do_backup():
    """
    <summary>
    Ask for a folder, then write every slot and the general settings to a timestamped
    engage-backup JSON file there.
    </summary>
    <remarks>
    The outcome is reported twice, as a toast and as an OK dialog naming the full path.
    A failed write shows the error in the dialog instead of raising. os and datetime are
    imported here because nothing else in the module needs them.
    </remarks>
    """
    import os
    from datetime import datetime

    # Choose destination folder
    folder = xbmcgui.Dialog().browse(3, 'Choose backup folder', 'files', '', False, False, '')
    if not folder:
        return

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    filename = 'engage-backup-{}.json'.format(stamp)
    full = os.path.join(folder, filename)
    try:
        count = store.write_backup(full)
        _notify('Backed up {} slot(s)'.format(count), duration=6000)
        xbmcgui.Dialog().ok('Engage', 'Backup saved to:[CR]{}'.format(full))
    except (OSError, IOError) as e:
        _log('backup failed: {}'.format(e), xbmc.LOGERROR)
        xbmcgui.Dialog().ok('Engage', 'Backup failed:[CR]{}'.format(e))


def _do_restore():
    # Pick a backup file
    """
    <summary>
    Ask for a backup JSON file and replace the current slots and general settings with
    its contents.
    </summary>
    <remarks>
    When slots already exist the user must confirm the replacement first. A file that
    is not an Engage backup, or cannot be read, is reported in an OK dialog and nothing
    is changed.
    </remarks>
    """
    path = xbmcgui.Dialog().browse(1, 'Select Engage backup file', 'files', '.json', False, False, '')
    if not path:
        return

    slots = store.load_slots()
    if slots:
        if not xbmcgui.Dialog().yesno(
                'Engage',
                'Restoring will REPLACE your current {} slot(s).[CR]Continue?'.format(len(slots))):
            return

    try:
        count = store.restore_backup(path)
        _notify('Restored {} slot(s)'.format(count), duration=6000)
        xbmcgui.Dialog().ok('Engage', 'Restored {} slot(s) from backup.'.format(count))
    except (ValueError, OSError, IOError) as e:
        _log('restore failed: {}'.format(e), xbmc.LOGERROR)
        xbmcgui.Dialog().ok('Engage', 'Restore failed:[CR]{}'.format(e))


def _add_slot():
    """
    <summary>
    Guided add: favourite -> day -> time. Everything else gets defaults.
    </summary>
    """
    fav = _pick_favourite()
    if not fav:
        return

    slot = store.new_slot(favourite=fav)
    schedule = _pick_schedule(slot)
    if not schedule:
        return
    slot.update(schedule)

    t = _pick_time(19, 0)
    if t is None:
        return

    slots = store.load_slots()
    slot['id'] = store.next_id(slots)
    slot['hour'], slot['minute'] = t
    slots.append(slot)
    store.save_slots(slots)
    _log('added slot id={} fav="{}" repeats {}'.format(slot['id'], fav, _schedule_text(slot)))
    _notify('Added: {}, {} {:02d}:{:02d}'.format(fav, _schedule_text(slot), t[0], t[1]))


# --- library queries -------------------------------------------------------

def _library_movies(query=''):
    """
    <summary>
    Return library movies as [{'type','id','title','year'}], optionally
    filtered by a title substring.
    </summary>
    """
    import json
    params = {'properties': ['title', 'year'],
              'sort': {'method': 'title', 'order': 'ascending'}}
    if query:
        params['filter'] = {'field': 'title', 'operator': 'contains', 'value': query}
    data = json.loads(xbmc.executeJSONRPC(json.dumps({
        'jsonrpc': '2.0', 'method': 'VideoLibrary.GetMovies', 'params': params, 'id': 1})))
    movies = data.get('result', {}).get('movies', []) or []
    return [{'type': 'movie', 'id': m.get('movieid'), 'title': m.get('title', '?'),
             'year': m.get('year', 0)} for m in movies if m.get('movieid')]


def _library_tvshows():
    """
    <summary>
    Return library TV shows as [{'tvshowid','title','year'}], title-sorted.
    </summary>
    """
    import json
    data = json.loads(xbmc.executeJSONRPC(json.dumps({
        'jsonrpc': '2.0', 'method': 'VideoLibrary.GetTVShows',
        'params': {'properties': ['title', 'year'],
                   'sort': {'method': 'title', 'order': 'ascending'}},
        'id': 1})))
    shows = data.get('result', {}).get('tvshows', []) or []
    return [{'tvshowid': s.get('tvshowid'), 'title': s.get('title', '?'),
             'year': s.get('year', 0)} for s in shows if s.get('tvshowid')]


def _show_seasons(tvshowid):
    """
    <summary>
    Return the season numbers present for a show, ascending.
    </summary>
    """
    import json
    data = json.loads(xbmc.executeJSONRPC(json.dumps({
        'jsonrpc': '2.0', 'method': 'VideoLibrary.GetSeasons',
        'params': {'tvshowid': tvshowid, 'properties': ['season'],
                   'sort': {'method': 'season', 'order': 'ascending'}},
        'id': 1})))
    seasons = data.get('result', {}).get('seasons', []) or []
    nums = sorted({s.get('season', 0) for s in seasons})
    return nums


def _season_episodes(tvshowid, season):
    """
    <summary>
    Return one season's episodes as ordered item dicts (by episode number).
    </summary>
    """
    import json
    data = json.loads(xbmc.executeJSONRPC(json.dumps({
        'jsonrpc': '2.0', 'method': 'VideoLibrary.GetEpisodes',
        'params': {'tvshowid': tvshowid, 'season': season,
                   'properties': ['title', 'season', 'episode', 'firstaired', 'showtitle'],
                   'sort': {'method': 'episode', 'order': 'ascending'}},
        'id': 1})))
    eps = data.get('result', {}).get('episodes', []) or []
    return [{'type': 'episode', 'id': e.get('episodeid'),
             'show': e.get('showtitle', ''), 'season': e.get('season', 0),
             'episode': e.get('episode', 0), 'title': e.get('title', ''),
             'firstaired': e.get('firstaired', '')}
            for e in eps if e.get('episodeid')]


# --- shared item helpers ---------------------------------------------------

def _item_line(it):
    """
    <summary>
    One-line label for a sequence item (movie or episode).
    </summary>
    """
    if it.get('type') == 'episode':
        return '{} · S{:02d}E{:02d}, {}'.format(
            it.get('show', '?'), it.get('season', 0), it.get('episode', 0),
            it.get('title', ''))
    return '{} ({})'.format(it.get('title', '?'), it.get('year') or '?')


def _item_entry_menu(items, i):
    """
    <summary>
    Move up / move down / move to position / remove a single item.
    </summary>
    """
    options = ['Move up', 'Move down', 'Move to position...', '[COLOR red]Remove[/COLOR]']
    choice = xbmcgui.Dialog().select(_item_line(items[i]), options)
    if choice == 0 and i > 0:
        items[i - 1], items[i] = items[i], items[i - 1]
    elif choice == 1 and i < len(items) - 1:
        items[i + 1], items[i] = items[i], items[i + 1]
    elif choice == 2:
        pos = xbmcgui.Dialog().input('Move to position (1-{})'.format(len(items)),
                                     str(i + 1), type=xbmcgui.INPUT_NUMERIC)
        try:
            new_i = max(0, min(len(items) - 1, int(pos) - 1))
            items.insert(new_i, items.pop(i))
        except (ValueError, TypeError):
            pass
    elif choice == 3:
        items.pop(i)


# --- movie sequence editor -------------------------------------------------

def _pick_movies_to_add(existing_ids):
    """
    <summary>
    Search the library and multi-select movies to add (excluding ones already
    in the list). Returns item dicts in year order.
    </summary>
    """
    query = xbmcgui.Dialog().input('Search movies (leave blank for all)',
                                   type=xbmcgui.INPUT_ALPHANUM)
    movies = [m for m in _library_movies(query) if m['id'] not in existing_ids]
    if not movies:
        _notify('No matching movies found.', xbmcgui.NOTIFICATION_WARNING)
        return []
    movies.sort(key=lambda m: (m['year'], m['title']))
    labels = ['{} ({})'.format(m['title'], m['year'] or '?') for m in movies]
    sel = xbmcgui.Dialog().multiselect('Select movies to add', labels)
    return [movies[i] for i in sel] if sel else []


def _edit_movie_list(items):
    """
    <summary>
    Interactive editor for an ordered movie sequence. Mutates and returns it.
    </summary>
    """
    while True:
        rows = ['{}. {}'.format(i + 1, _item_line(it)) for i, it in enumerate(items)]
        rows.append('[COLOR cyan]+ Add movies...[/COLOR]')
        rows.append('Sort by year')
        rows.append('[COLOR cyan]Done[/COLOR]')
        idx = xbmcgui.Dialog().select('Movie list ({} films)'.format(len(items)), rows)
        if idx < 0 or idx == len(rows) - 1:
            return items
        if idx == len(items):
            added = _pick_movies_to_add({it['id'] for it in items})
            items.extend(added)
            if added:
                _notify('Added {} film(s)'.format(len(added)))
        elif idx == len(items) + 1:
            items.sort(key=lambda it: (it.get('year', 0), it.get('title', '')))
        else:
            _item_entry_menu(items, idx)


# --- binge (crossover) editor ----------------------------------------------

def _add_episode_range(items):
    """
    <summary>
    Add a contiguous run of episodes: pick show -> season -> start -> end.
    Appends them (in episode order) to the end of the list. Returns count added.
    </summary>
    """
    shows = _library_tvshows()
    if not shows:
        _notify('No TV shows in library.', xbmcgui.NOTIFICATION_WARNING)
        return 0
    show_labels = ['{} ({})'.format(s['title'], s['year'] or '?') for s in shows]
    si = xbmcgui.Dialog().select('Pick show', show_labels)
    if si < 0:
        return 0
    show = shows[si]

    seasons = _show_seasons(show['tvshowid'])
    if not seasons:
        _notify('No seasons found for {}.'.format(show['title']), xbmcgui.NOTIFICATION_WARNING)
        return 0
    season_labels = ['Specials' if n == 0 else 'Season {}'.format(n) for n in seasons]
    sei = xbmcgui.Dialog().select('{}, pick season'.format(show['title']), season_labels)
    if sei < 0:
        return 0
    season = seasons[sei]

    eps = _season_episodes(show['tvshowid'], season)
    if not eps:
        _notify('No episodes found.', xbmcgui.NOTIFICATION_WARNING)
        return 0
    ep_labels = ['S{:02d}E{:02d}, {}'.format(e['season'], e['episode'], e['title']) for e in eps]

    start = xbmcgui.Dialog().select('Start episode', ep_labels)
    if start < 0:
        return 0
    # End list only offers episodes at/after the start.
    end_rel = xbmcgui.Dialog().select('End episode', ep_labels[start:])
    if end_rel < 0:
        return 0
    chosen = eps[start:start + end_rel + 1]
    items.extend(chosen)
    return len(chosen)


def _edit_binge_list(items):
    """
    <summary>
    Interactive editor for a crossover/binge episode list, built from manual
    show/season episode ranges in the order you add them.
    </summary>
    """
    while True:
        rows = ['{}. {}'.format(i + 1, _item_line(it)) for i, it in enumerate(items)]
        rows.append('[COLOR cyan]+ Add episode range...[/COLOR]')
        rows.append('[COLOR cyan]Done[/COLOR]')
        idx = xbmcgui.Dialog().select('Binge order ({} episodes)'.format(len(items)), rows)
        if idx < 0 or idx == len(rows) - 1:
            return items
        if idx == len(items):
            n = _add_episode_range(items)
            if n:
                _notify('Added {} episode(s)'.format(n), duration=5000)
        else:
            _item_entry_menu(items, idx)


# --- add flows -------------------------------------------------------------

def _add_sequence():
    """
    <summary>
    Guided add for a movie-sequence slot: name -> build list -> day -> time.
    </summary>
    """
    label = xbmcgui.Dialog().input('Sequence name (e.g. Marvel Movies)',
                                   type=xbmcgui.INPUT_ALPHANUM)
    if not label:
        return
    items = _edit_movie_list([])
    if not items:
        _notify('No movies added, sequence not created.', xbmcgui.NOTIFICATION_WARNING)
        return
    _finish_add_sequence(label, 'movie', items, 'films')


def _add_binge():
    """
    <summary>
    Guided add for a crossover/binge slot: name -> add episode ranges
    (show/season/start/end, repeated) -> day -> time.
    </summary>
    """
    label = xbmcgui.Dialog().input('Binge name (e.g. Arrowverse)',
                                   type=xbmcgui.INPUT_ALPHANUM)
    if not label:
        return
    items = _edit_binge_list([])
    if not items:
        _notify('No episodes added, binge not created.', xbmcgui.NOTIFICATION_WARNING)
        return
    _finish_add_sequence(label, 'binge', items, 'episodes')


def _finish_add_sequence(label, seq_type, items, noun):
    """
    <summary>
    The tail shared by the movie sequence and binge add flows: pick the days and a time,
    then save the new sequence slot.
    </summary>
    <param name="label">The name the user entered.</param>
    <param name="seq_type">'movie' or 'binge'.</param>
    <param name="items">The ordered item dicts already built by the list editor.</param>
    <param name="noun">The word for the items in the confirmation toast, 'films' or 'episodes'.</param>
    <remarks>
    Backing out of the repeat or time picker abandons the slot; nothing is saved.
    </remarks>
    """
    slot = store.new_sequence(label=label, seq_type=seq_type)
    schedule = _pick_schedule(slot)
    if not schedule:
        return
    slot.update(schedule)
    t = _pick_time(19, 0)
    if t is None:
        return
    slots = store.load_slots()
    slot['id'] = store.next_id(slots)
    slot['items'] = items
    slot['hour'], slot['minute'] = t
    slots.append(slot)
    store.save_slots(slots)
    _log('added {} sequence id={} "{}" ({} items)'.format(seq_type, slot['id'], label, len(items)))
    _notify('Added: {} ({} {})'.format(label, len(items), noun))


# --- Functional skin lists -------------------------------------------------

def _pick_list(current_name):
    """
    <summary>
    Select dialog over the Functional skin's lists, with their item counts.
    </summary>
    <param name="current_name">The list to preselect, '' for none.</param>
    <returns>The chosen list's name, or None when there are no lists or the user
    backed out.</returns>
    <remarks>
    The lists are read from the skin's own file each time, so a list made in the skin
    a moment ago is offered without restarting anything.
    </remarks>
    """
    lists = functional_lists.load_lists()
    if not lists:
        _notify('No Functional skin lists found. Make one in the skin first.',
                xbmcgui.NOTIFICATION_WARNING, 6000)
        return None
    labels = ['{} ({} items)'.format(entry['name'], len(entry['items'])) for entry in lists]
    names = [entry['name'].lower() for entry in lists]
    pre = names.index(current_name.lower()) if current_name.lower() in names else -1
    idx = xbmcgui.Dialog().select('Pick a Functional list', labels, preselect=pre)
    return lists[idx]['name'] if idx >= 0 else None


def _pick_list_mode(current):
    """
    <summary>
    Ask whether a list slot plays in list order or at random.
    </summary>
    <param name="current">The mode to preselect.</param>
    <returns>'next' or 'random', or None when the user backed out.</returns>
    """
    labels = [functional_lists.MODE_LABELS[m] for m in functional_lists.MODES]
    pre = functional_lists.MODES.index(current) if current in functional_lists.MODES else 0
    idx = xbmcgui.Dialog().select('What should play', labels, preselect=pre)
    return functional_lists.MODES[idx] if idx >= 0 else None


def _pick_list_sequels(current):
    """
    <summary>
    Ask whether a random list slot keeps each film series (a Kodi movie set, or one
    show's episodes) in order.
    </summary>
    <param name="current">The choice to preselect, one of functional_lists.SEQUELS.</param>
    <returns>One of functional_lists.SEQUELS, or None when the user backed out.</returns>
    """
    choices = functional_lists.SEQUELS
    labels = [functional_lists.SEQUEL_LABELS[c] for c in choices]
    pre = choices.index(current) if current in choices else 0
    idx = xbmcgui.Dialog().select('Sequels in order?', labels, preselect=pre)
    return choices[idx] if idx >= 0 else None


def _add_list_slot():
    """
    <summary>
    Guided add for a Functional list slot: list, then in order or random, then for a
    random slot whether sequels keep their order, then the repeat and the time.
    </summary>
    <remarks>
    The label is left empty so the slot shows the list's own name. Backing out at any
    step saves nothing.
    </remarks>
    """
    name = _pick_list('')
    if not name:
        return
    mode = _pick_list_mode(functional_lists.DEFAULT_MODE)
    if mode is None:
        return
    slot = store.new_list_slot(name, mode)
    if mode in functional_lists.RANDOM_MODES:
        sequels = _pick_list_sequels(functional_lists.SEQUELS_OFF)
        if sequels is None:
            return
        slot['list_sequels'] = sequels
    schedule = _pick_schedule(slot)
    if not schedule:
        return
    slot.update(schedule)
    t = _pick_time(19, 0)
    if t is None:
        return
    slots = store.load_slots()
    slot['id'] = store.next_id(slots)
    slot['hour'], slot['minute'] = t
    slots.append(slot)
    store.save_slots(slots)
    _log('added list slot id={} list="{}" mode={}'.format(slot['id'], name, mode))
    _notify('Added: {} ({}), {} {:02d}:{:02d}'.format(
        name, functional_lists.mode_text(slot), _schedule_text(slot), t[0], t[1]))


def _edit_list_choice(s):
    """
    <summary>
    Change which Functional list a slot plays from, or how it picks from it.
    </summary>
    <remarks>
    The sequels line is offered only for the random modes, the only ones it affects.
    A sequels choice left on a slot switched to an in order mode is kept but ignored.
    </remarks>
    <param name="s">The slot dict, changed in place; the caller saves it.</param>
    """
    mode = functional_lists.mode_of(s)
    options = ['List: {}'.format(s.get('list_name') or '(not set)'),
               'Play: {}'.format(functional_lists.MODE_LABELS[mode])]
    sequels = functional_lists.sequels_of(s)
    if mode in functional_lists.RANDOM_MODES:
        options.append('Sequels: {}'.format(functional_lists.SEQUEL_LABELS[sequels]))
    idx = xbmcgui.Dialog().select('Functional list', options)
    if idx == 0:
        name = _pick_list(s.get('list_name') or '')
        if name:
            s['list_name'] = name
    elif idx == 1:
        new_mode = _pick_list_mode(mode)
        if new_mode:
            s['list_mode'] = new_mode
    elif idx == 2:
        new_sequels = _pick_list_sequels(sequels)
        if new_sequels:
            s['list_sequels'] = new_sequels


def _edit_slot(slot_id):
    """
    <summary>
    Edit menu for one slot. Loops so several fields can be changed in a row.
    </summary>
    """
    while True:
        slots = store.load_slots()
        s = store.get_slot(slots, slot_id)
        if s is None:
            return

        is_seq = s.get('kind') == 'sequence'
        is_list = s.get('kind') == 'list'
        seq_type = s.get('seq_type', 'movie')
        name = s.get('label') or s.get('favourite') or s.get('list_name') or 'Sequence'
        if is_list:
            second = 'Functional list: {}, {}...'.format(
                s.get('list_name') or '(not set)',
                functional_lists.mode_text(s))
            label_line = 'Label: {}'.format(s.get('label') or '(auto, uses list name)')
        elif is_seq:
            cur_items = store.items_of(s)
            if seq_type == 'binge':
                second = 'Binge order: {} episodes...'.format(len(cur_items))
            else:
                second = 'Movie list: {} films...'.format(len(cur_items))
            label_line = 'Label: {}'.format(s.get('label') or '(none)')
        else:
            second = 'Favourite: {}'.format(s.get('favourite') or '(not set)')
            label_line = 'Label: {}'.format(s.get('label') or '(auto, uses favourite name)')
        options = [
            'Enabled: {}'.format('yes' if s.get('enabled') else 'no'),
            second,
            label_line,
            'Repeats: {}'.format(_schedule_text(s, with_date=False)),
            'One-shot date: {}'.format(s.get('date') or '(none, uses the repeat)'),
            'Time: {:02d}:{:02d}'.format(s.get('hour', 0), s.get('minute', 0)),
            'More days and times: {}...'.format(_extra_times_summary(s)),
            'Warning minutes: {}'.format(s.get('warning', 5)),
            'When the countdown ends: {}'.format(
                store.TIMEOUT_ACTION_SHORT[store.timeout_action_of(s)]),
            'Banner style: {}'.format(
                store.BANNER_STYLE_SHORT[store.banner_style_of(s)]),
            'Skip the banner, just play at the set time: {}'.format(
                'yes' if s.get('autoopen') else 'no'),
            'Allow snooze: {}'.format('yes' if s.get('snooze') else 'no'),
            'Ends At line after it (Functional skin, Coming up list): {}'.format(
                'on' if store.ends_at_of(s) else 'off'),
        ]
        # ROW-ITEM: a list slot only. Inserted here, so the entries below it move
        # down one and are moved back before the dispatch.
        if is_list:
            options.append('Coming up names (Functional skin): {}'.format(
                'what it will play' if store.row_item_of(s) else 'the list'))
        options += [
            'Duplicate slot',
            '[COLOR red]Delete slot[/COLOR]',
        ]
        idx = xbmcgui.Dialog().select('Edit: {}'.format(name), options)
        if idx < 0:
            return
        if is_list and idx == 13:
            s['row_item'] = not store.row_item_of(s)
            store.save_slots(slots)
            continue
        if is_list and idx > 13:
            idx -= 1

        if idx == 0:
            s['enabled'] = not s.get('enabled')
        elif idx == 1:
            if is_list:
                _edit_list_choice(s)
            elif is_seq:
                current = list(store.items_of(s))
                if seq_type == 'binge':
                    s['items'] = _edit_binge_list(current)
                else:
                    s['items'] = _edit_movie_list(current)
                s.pop('movies', None)  # migrate off the legacy field
            else:
                fav = _pick_favourite()
                if fav:
                    s['favourite'] = fav
        elif idx == 2:
            if is_seq:
                prompt = 'Label'
            elif is_list:
                prompt = 'Label (leave empty to use list name)'
            else:
                prompt = 'Label (leave empty to use favourite name)'
            label = xbmcgui.Dialog().input(prompt, s.get('label', ''), type=xbmcgui.INPUT_ALPHANUM)
            s['label'] = label
        elif idx == 3:
            schedule = _pick_schedule(s)
            if schedule:
                s.update(schedule)
                s.pop('day', None)  # migrate off the legacy single-day field
        elif idx == 4:
            self_date = _edit_date(s.get('date', ''))
            if self_date is not None:
                s['date'] = self_date
        elif idx == 5:
            t = _pick_time(s.get('hour', 19), s.get('minute', 0))
            if t is not None:
                s['hour'], s['minute'] = t
        elif idx == 6:
            _edit_extra_times(s)
        elif idx == 7:
            warn = _pick_warning(s.get('warning', 5))
            if warn is not None:
                s['warning'] = warn
        elif idx == 8:
            action = _pick_timeout_action(store.timeout_action_of(s))
            if action is not None:
                s['timeout_action'] = action
        elif idx == 9:
            style = _pick_banner_style(store.banner_style_of(s))
            if style is not None:
                s['banner_style'] = style
        elif idx == 10:
            s['autoopen'] = not s.get('autoopen')
        elif idx == 11:
            s['snooze'] = not s.get('snooze')
        elif idx == 12:
            s['ends_at'] = not store.ends_at_of(s)
        elif idx == 13:
            import copy
            dup = copy.deepcopy(s)
            dup['id'] = store.next_id(slots)
            dup['label'] = (s.get('label') or s.get('favourite') or s.get('list_name') or '') + ' (copy)'
            slots.append(dup)
            store.save_slots(slots)
            _notify('Duplicated slot')
            continue
        elif idx == 14:
            if xbmcgui.Dialog().yesno('Engage', 'Delete "{}"?'.format(name)):
                slots = [x for x in slots if x.get('id') != slot_id]
                store.save_slots(slots)
                _log('deleted slot id={}'.format(slot_id))
                _notify('Deleted: {}'.format(name))
                return
            continue

        store.save_slots(slots)


def _extra_days_text(slot, entry):
    """
    <summary>
    The days part of one extra day and time, for the slot's summary.
    </summary>
    <param name="slot">The slot it belongs to.</param>
    <param name="entry">One entry from slots.extra_times_of.</param>
    <returns>'Mon,Wed' or 'Every day' for a weekly or every few weeks slot; 'also'
    for a monthly, yearly or one shot slot, where only the time counts.</returns>
    """
    if slot.get('date') or store.repeat_of(slot) in (store.REPEAT_MONTHLY, store.REPEAT_YEARLY):
        return 'also'
    return _days_abbrev(entry['days'])


def _extra_times_summary(slot):
    """
    <summary>
    The edit menu's word for a slot's extra days and times.
    </summary>
    <param name="slot">The slot.</param>
    <returns>'none', or the entries as 'Sat 15:00, Sun 21:30'.</returns>
    """
    entries = store.extra_times_of(slot)
    if not entries:
        return 'none'
    return ', '.join('{} {:02d}:{:02d}'.format(_extra_days_text(slot, e), e['hour'], e['minute'])
                     for e in entries)


def _edit_extra_times(slot):
    """
    <summary>
    Add, change and remove the days and times a slot runs at beyond its first.
    </summary>
    <param name="slot">The slot being edited; its extra_times is replaced in place.
    The caller saves it.</param>
    <remarks>
    MULTI-TIMES, 22/10/2026. Loops until Back. Choosing an entry offers to change its
    days, change its time or remove it; the last line adds one, asking for its days
    and then its time, and backing out of either adds nothing. For a monthly, yearly
    or one shot slot the days are still asked for but only the time is used, which
    the summary says by reading 'also'.
    </remarks>
    """
    while True:
        entries = store.extra_times_of(slot)
        lines = ['{} at {:02d}:{:02d}'.format(_extra_days_text(slot, e), e['hour'], e['minute'])
                 for e in entries]
        lines.append('+ Add another day and time...')
        idx = xbmcgui.Dialog().select('More days and times (first: {} at {:02d}:{:02d})'.format(
            _schedule_text(slot), slot.get('hour', 0), slot.get('minute', 0)), lines)
        if idx < 0:
            return
        if idx == len(entries):
            days = _pick_days(slot.get('days') or [])
            if days is None:
                continue
            t = _pick_time(slot.get('hour', 19), slot.get('minute', 0))
            if t is None:
                continue
            entries.append({'days': days, 'hour': t[0], 'minute': t[1]})
        else:
            entry = entries[idx]
            what = xbmcgui.Dialog().select(lines[idx], ['Change days', 'Change time',
                                                        '[COLOR red]Remove[/COLOR]'])
            if what == 0:
                days = _pick_days(entry['days'])
                if days is not None:
                    entry['days'] = days
            elif what == 1:
                t = _pick_time(entry['hour'], entry['minute'])
                if t is not None:
                    entry['hour'], entry['minute'] = t
            elif what == 2:
                entries.pop(idx)
        slot['extra_times'] = entries


def _safe_day_index(day):
    """
    <summary>
    Position of a weekday name in DAY_NAMES, 0 (Monday) for anything unrecognised.
    </summary>
    <param name="day">A weekday name; None or a misspelling is tolerated.</param>
    <returns>An int from 0 to 6.</returns>
    <remarks>
    Has no caller in the add-on at present.
    </remarks>
    """
    try:
        return DAY_NAMES.index(day)
    except (ValueError, TypeError):
        return 0


def _days_abbrev(days):
    """
    <summary>
    Compact day summary, e.g. 'Mon,Wed,Fri' or 'Every day'.
    </summary>
    """
    order = [d for d in DAY_NAMES if d in days]
    if len(order) == 7:
        return 'Every day'
    if not order:
        return '?'
    return ','.join(d[:3] for d in order)


def _pick_days(current):
    """
    <summary>
    Multi-select weekday picker. Returns a day-name list (calendar order) or
    None if cancelled / nothing chosen.
    </summary>
    """
    preselect = [i for i, d in enumerate(DAY_NAMES) if d in current]
    sel = xbmcgui.Dialog().multiselect('Days of week', DAY_NAMES, preselect=preselect)
    if not sel:
        return None
    return [DAY_NAMES[i] for i in sel]


def _first_in_cycle(days, week_start, interval, today):
    """
    <summary>
    The first date on or after today that an every few weeks slot runs.
    </summary>
    <param name="days">The slot's weekday names.</param>
    <param name="week_start">The Monday of an on week.</param>
    <param name="interval">Weeks between on weeks.</param>
    <param name="today">The date to search from.</param>
    <returns>The date, or None when no days are chosen.</returns>
    <remarks>
    Date only: a run earlier today whose time has already passed still counts, so a
    slot made on its own day shows today rather than the next on week.
    </remarks>
    """
    from datetime import timedelta
    wanted = [DAY_NAMES.index(d) for d in days if d in DAY_NAMES]
    for n in range(7 * interval):
        day = today + timedelta(days=n)
        if day.weekday() in wanted and store.in_week_cycle(day, week_start, interval):
            return day
    return None


def _short_date(day):
    """
    <summary>
    A date as 'Tue 14/10/2026', for the every few weeks picker and summary.
    </summary>
    <param name="day">The date.</param>
    <returns>The text, weekday from DAY_NAMES so it does not depend on the locale.</returns>
    """
    return '{} {:02d}/{:02d}/{}'.format(DAY_NAMES[day.weekday()][:3], day.day, day.month, day.year)


def _pick_week_start(days, interval, current_start):
    """
    <summary>
    Ask which week an every few weeks slot starts in, by the date it first runs.
    </summary>
    <param name="days">The weekday names already chosen.</param>
    <param name="interval">Weeks between on weeks.</param>
    <param name="current_start">The slot's present start Monday, so its rhythm is
    preselected when the slot is edited; None for a slot that has none yet, which
    preselects this week.</param>
    <returns>The Monday of the chosen week as 'YYYY-MM-DD', or None when the user
    backed out.</returns>
    <remarks>
    One choice per week of the cycle, this week first, each labelled with the first
    date it would run, so 'every other Tuesday' becomes a choice between this Tuesday
    and next Tuesday. When this week's days have already gone, that choice's first
    run is a whole cycle away, and its label says so by its date.
    </remarks>
    """
    from datetime import date, timedelta
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    mondays = [this_monday + timedelta(weeks=k) for k in range(interval)]
    labels, pre = [], 0
    for k, monday in enumerate(mondays):
        first = _first_in_cycle(days, monday, interval, today)
        labels.append('{}, then every {} weeks'.format(_short_date(first) if first else '?', interval))
        if current_start and store.in_week_cycle(monday, current_start, interval):
            pre = k
    idx = xbmcgui.Dialog().select('First showing', labels, preselect=pre)
    return mondays[idx].isoformat() if idx >= 0 else None


def _schedule_text(slot, with_date=True):
    """
    <summary>
    How often a slot runs, in words: 'Weekly on Mon,Wed', 'Every day',
    'Every 2 weeks on Tue, next Tue 27/10/2026', 'Monthly on the 15th' or
    'Yearly on 25th December'.
    </summary>
    <param name="slot">A slot dict.</param>
    <param name="with_date">When true and a one-shot date is set, describe that
    instead, since it overrides the repeat.</param>
    <returns>The text.</returns>
    """
    if with_date and slot.get('date'):
        parts = slot['date'].split('-')
        if len(parts) == 3:
            return 'Once on {}/{}/{}'.format(parts[2], parts[1], parts[0])
        return 'Once on {}'.format(slot['date'])
    repeat = store.repeat_of(slot)
    day = store.month_day_of(slot)
    if repeat == store.REPEAT_MONTHLY:
        return 'Monthly on the {}'.format(store.ordinal(day))
    if repeat == store.REPEAT_YEARLY:
        return 'Yearly on {} {}'.format(store.ordinal(day), store.MONTH_NAMES[store.month_of(slot) - 1])
    days = _days_abbrev(store.days_of(slot))
    if repeat == store.REPEAT_EVERY_WEEKS:
        from datetime import date
        interval = store.week_interval_of(slot)
        first = _first_in_cycle(store.days_of(slot), store.week_start_of(slot), interval, date.today())
        on = 'every day' if days == 'Every day' else 'on {}'.format(days)
        nxt = ', next {}'.format(_short_date(first)) if first else ''
        return 'Every {} weeks {}{}'.format(interval, on, nxt)
    return days if days == 'Every day' else 'Weekly on {}'.format(days)


def _pick_schedule(slot):
    """
    <summary>
    Ask how a slot repeats (weekly, every few weeks, monthly or yearly), then for the
    days, the gap and first showing, the day of the month, or the date.
    </summary>
    <param name="slot">The slot dict, read for the current values to preselect.</param>
    <returns>The fields to merge into the slot ('repeat' plus 'days'; or 'days',
    'week_interval' and 'week_start'; or 'month_day'; or 'month' and 'month_day'), or
    None when the user backed out.</returns>
    <remarks>
    The other repeat's fields are left as they were, so switching back restores the
    old days. A one-shot date still wins over whatever is chosen here, and the user is
    told so when one is set.
    </remarks>
    """
    labels = ['Weekly, on chosen days', 'Every few weeks, e.g. every other Tuesday',
              'Monthly, on a day of the month', 'Yearly, on a date']
    current = store.repeat_of(slot)
    idx = xbmcgui.Dialog().select('Repeats', labels, preselect=store.REPEATS.index(current))
    if idx < 0:
        return None
    repeat = store.REPEATS[idx]
    if repeat == store.REPEAT_WEEKLY:
        days = _pick_days(store.days_of(slot))
        if not days:
            return None
        result = {'repeat': repeat, 'days': days}
    elif repeat == store.REPEAT_EVERY_WEEKS:
        intervals = store.WEEK_INTERVALS
        current = store.week_interval_of(slot)
        w_idx = xbmcgui.Dialog().select('How often', ['Every {} weeks'.format(n) for n in intervals],
                                        preselect=intervals.index(current))
        if w_idx < 0:
            return None
        interval = intervals[w_idx]
        days = _pick_days(store.days_of(slot))
        if not days:
            return None
        start = _pick_week_start(days, interval,
                                 store.week_start_of(slot) if slot.get('week_start') else None)
        if start is None:
            return None
        result = {'repeat': repeat, 'days': days, 'week_interval': interval, 'week_start': start}
    elif repeat == store.REPEAT_MONTHLY:
        day = _pick_month_day(store.month_day_of(slot), 31, 'Day of the month',
                              'last day in shorter months', 29)
        if day is None:
            return None
        result = {'repeat': repeat, 'month_day': day}
    else:
        m_idx = xbmcgui.Dialog().select('Month', store.MONTH_NAMES,
                                        preselect=store.month_of(slot) - 1)
        if m_idx < 0:
            return None
        month = m_idx + 1
        # 2000 is a leap year, so February offers the 29th.
        max_day = store.last_day_of_month(2000, month)
        note_from = 29 if month == 2 else max_day + 1
        day = _pick_month_day(min(store.month_day_of(slot), max_day), max_day,
                              'Day of {}'.format(store.MONTH_NAMES[m_idx]),
                              '28th outside leap years', note_from)
        if day is None:
            return None
        result = {'repeat': repeat, 'month': month, 'month_day': day}
    if slot.get('date'):
        _notify('A one-shot date is set and overrides this until it is cleared.',
                xbmcgui.NOTIFICATION_WARNING, 6000)
    return result


def _pick_month_day(current, max_day, heading, note, note_from):
    """
    <summary>
    Select list of days 1st to max_day.
    </summary>
    <param name="current">The day to preselect.</param>
    <param name="max_day">The last day offered.</param>
    <param name="heading">The dialog heading.</param>
    <param name="note">Shown in brackets beside the days that do not always exist.</param>
    <param name="note_from">The first day that carries the note.</param>
    <returns>The chosen day as an int, or None when the user backed out.</returns>
    """
    labels = []
    for d in range(1, max_day + 1):
        label = store.ordinal(d)
        if d >= note_from:
            label += ' ({})'.format(note)
        labels.append(label)
    pre = current - 1 if 1 <= current <= max_day else 0
    idx = xbmcgui.Dialog().select(heading, labels, preselect=pre)
    return idx + 1 if idx >= 0 else None


def _edit_date(current):
    """
    <summary>
    Set or clear the one-shot date. Returns 'YYYY-MM-DD', '' to clear, or
    None if the user backed out.
    </summary>
    """
    choices = ['Set a date...', 'Clear (use the repeat)']
    idx = xbmcgui.Dialog().select('One-shot date', choices)
    if idx < 0:
        return None
    if idx == 1:
        return ''
    # Plain text entry, Kodi's numeric date dialog has the same blank-window
    # problem as its time dialog on some skins.
    result = xbmcgui.Dialog().input('Date (YYYY-MM-DD)', current or '',
                                    type=xbmcgui.INPUT_ALPHANUM)
    if not result:
        return None
    try:
        parts = result.strip().split('-')
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        # Validates ranges (month 1-12, real day-of-month etc.)
        from datetime import datetime as _dt
        _dt(year, month, day)
        return '{:04d}-{:02d}-{:02d}'.format(year, month, day)
    except (ValueError, IndexError):
        _notify('Invalid date: {} (use YYYY-MM-DD)'.format(result), xbmcgui.NOTIFICATION_WARNING)
        return None
