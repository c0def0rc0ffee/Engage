"""Dialog-driven slot management UI for Engage.

Everything is built from Kodi's native dialogs (select, input, yesno) so it
works equally well with a remote, keyboard, or touch. (Dialog().numeric is
deliberately avoided, it renders a blank window on some skins.)
"""

import xbmc
import xbmcgui

from resources.lib import slots as store
from resources.lib.favourites_ui import _get_favourites, _maybe_filter_video

DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('Engage manager: {}'.format(msg), level)


def _notify(message, icon=xbmcgui.NOTIFICATION_INFO, duration=4000):
    xbmcgui.Dialog().notification('Engage', message, icon, duration)


def _pick_favourite():
    """Select dialog over current (video-filtered) favourites. Returns title or None."""
    favs = _maybe_filter_video(_get_favourites())
    if not favs:
        _notify('No favourites found.', xbmcgui.NOTIFICATION_WARNING)
        return None
    titles = [f.get('title', '(untitled)') for f in favs]
    idx = xbmcgui.Dialog().select('Pick favourite', titles)
    return titles[idx] if idx >= 0 else None


def _pick_time(current_hour, current_minute):
    """HH:MM time selector via two select lists. Returns (hour, minute) or None.

    Hour list 00-23, then full minute list 00-59, no steps, any exact minute.
    (Kodi's numeric time dialog renders a blank window on some skins, so we
    avoid it and use select lists, which work everywhere.)
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
    """Warning-minutes selector via a select list (0-30 min). Returns int or None.
    Avoids Dialog().numeric, which renders blank on some skins."""
    values = list(range(0, 31))
    labels = ['{} min'.format(v) for v in values]
    labels[0] = 'No warning (0 min)'
    pre = current if current in values else 5
    idx = xbmcgui.Dialog().select('Warning minutes before start', labels, preselect=pre)
    if idx < 0:
        return None
    return values[idx]


def _pick_timeout_action(current):
    """What the slot does if the banner's countdown runs out untouched.
    Returns an action id, or None if the user backed out."""
    labels = [store.TIMEOUT_ACTION_LABELS[a] for a in store.TIMEOUT_ACTIONS]
    try:
        pre = store.TIMEOUT_ACTIONS.index(current)
    except ValueError:
        pre = store.TIMEOUT_ACTIONS.index(store.DEFAULT_TIMEOUT_ACTION)
    idx = xbmcgui.Dialog().select('When the countdown ends', labels, preselect=pre)
    if idx < 0:
        return None
    return store.TIMEOUT_ACTIONS[idx]


def _slot_line(slot):
    """One-line summary for the slot list."""
    if slot.get('kind') == 'sequence':
        count = len(store.items_of(slot))
        noun = 'episodes' if slot.get('seq_type') == 'binge' else 'films'
        name = '{} [{} {}]'.format(slot.get('label') or 'Sequence', count, noun)
    else:
        name = slot.get('label') or slot.get('favourite') or '(no favourite)'
    if slot.get('date'):
        when = slot['date']
    else:
        when = _days_abbrev(store.days_of(slot))
    line = '{}, {} {:02d}:{:02d}'.format(name, when, slot.get('hour', 0), slot.get('minute', 0))
    if not slot.get('enabled'):
        line += '  [disabled]'
    return line


def manage_slots():
    """Top-level slot list. Loops until the user backs out."""
    while True:
        slots = store.load_slots()
        items = [_slot_line(s) for s in slots]
        items.append('+ Add new slot (single show/movie)...')
        items.append('+ Add movie sequence...')
        items.append('+ Add binge order (crossover)...')
        items.append('[COLOR cyan]Backup / Restore...[/COLOR]')
        idx = xbmcgui.Dialog().select('Engage, Slots', items)
        if idx < 0:
            return
        if idx == len(slots):
            _add_slot()
        elif idx == len(slots) + 1:
            _add_sequence()
        elif idx == len(slots) + 2:
            _add_binge()
        elif idx == len(slots) + 3:
            _backup_restore_menu()
        else:
            _edit_slot(slots[idx]['id'])


def _backup_restore_menu():
    """Backup to / restore from a JSON file the user chooses."""
    options = ['Backup now (save to file)...', 'Restore from backup file...']
    idx = xbmcgui.Dialog().select('Engage, Backup / Restore', options)
    if idx == 0:
        _do_backup()
    elif idx == 1:
        _do_restore()


def _do_backup():
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
        xbmcgui.Dialog().ok('Engage', 'Backup saved to:\n{}'.format(full))
    except (OSError, IOError) as e:
        _log('backup failed: {}'.format(e), xbmc.LOGERROR)
        xbmcgui.Dialog().ok('Engage', 'Backup failed:\n{}'.format(e))


def _do_restore():
    # Pick a backup file
    path = xbmcgui.Dialog().browse(1, 'Select Engage backup file', 'files', '.json', False, False, '')
    if not path:
        return

    slots = store.load_slots()
    if slots:
        if not xbmcgui.Dialog().yesno(
                'Engage',
                'Restoring will REPLACE your current {} slot(s).\nContinue?'.format(len(slots))):
            return

    try:
        count = store.restore_backup(path)
        _notify('Restored {} slot(s)'.format(count), duration=6000)
        xbmcgui.Dialog().ok('Engage', 'Restored {} slot(s) from backup.'.format(count))
    except (ValueError, OSError, IOError) as e:
        _log('restore failed: {}'.format(e), xbmc.LOGERROR)
        xbmcgui.Dialog().ok('Engage', 'Restore failed:\n{}'.format(e))


def _add_slot():
    """Guided add: favourite -> day -> time. Everything else gets defaults."""
    fav = _pick_favourite()
    if not fav:
        return

    days = _pick_days(['Monday'])
    if not days:
        return

    t = _pick_time(19, 0)
    if t is None:
        return

    slots = store.load_slots()
    slot = store.new_slot(favourite=fav)
    slot['id'] = store.next_id(slots)
    slot['days'] = days
    slot['hour'], slot['minute'] = t
    slots.append(slot)
    store.save_slots(slots)
    _log('added slot id={} fav="{}" days={}'.format(slot['id'], fav, days))
    _notify('Added: {}, {} {:02d}:{:02d}'.format(fav, _days_abbrev(days), t[0], t[1]))


# --- library queries -------------------------------------------------------

def _library_movies(query=''):
    """Return library movies as [{'type','id','title','year'}], optionally
    filtered by a title substring."""
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
    """Return library TV shows as [{'tvshowid','title','year'}], title-sorted."""
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
    """Return the season numbers present for a show, ascending."""
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
    """Return one season's episodes as ordered item dicts (by episode number)."""
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
    """One-line label for a sequence item (movie or episode)."""
    if it.get('type') == 'episode':
        return '{} · S{:02d}E{:02d}, {}'.format(
            it.get('show', '?'), it.get('season', 0), it.get('episode', 0),
            it.get('title', ''))
    return '{} ({})'.format(it.get('title', '?'), it.get('year') or '?')


def _item_entry_menu(items, i):
    """Move up / move down / move to position / remove a single item."""
    options = ['Move up', 'Move down', 'Move to position…', '[COLOR red]Remove[/COLOR]']
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
    """Search the library and multi-select movies to add (excluding ones already
    in the list). Returns item dicts in year order."""
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
    """Interactive editor for an ordered movie sequence. Mutates and returns it."""
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
    """Add a contiguous run of episodes: pick show -> season -> start -> end.
    Appends them (in episode order) to the end of the list. Returns count added."""
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
    """Interactive editor for a crossover/binge episode list, built from manual
    show/season episode ranges in the order you add them."""
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
    """Guided add for a movie-sequence slot: name -> build list -> day -> time."""
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
    """Guided add for a crossover/binge slot: name -> add episode ranges
    (show/season/start/end, repeated) -> day -> time."""
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
    days = _pick_days(['Monday'])
    if not days:
        return
    t = _pick_time(19, 0)
    if t is None:
        return
    slots = store.load_slots()
    slot = store.new_sequence(label=label, seq_type=seq_type)
    slot['id'] = store.next_id(slots)
    slot['days'] = days
    slot['items'] = items
    slot['hour'], slot['minute'] = t
    slots.append(slot)
    store.save_slots(slots)
    _log('added {} sequence id={} "{}" ({} items)'.format(seq_type, slot['id'], label, len(items)))
    _notify('Added: {} ({} {})'.format(label, len(items), noun))


def _edit_slot(slot_id):
    """Edit menu for one slot. Loops so several fields can be changed in a row."""
    while True:
        slots = store.load_slots()
        s = store.get_slot(slots, slot_id)
        if s is None:
            return

        is_seq = s.get('kind') == 'sequence'
        seq_type = s.get('seq_type', 'movie')
        name = s.get('label') or s.get('favourite') or 'Sequence'
        if is_seq:
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
            'Days: {}'.format(_days_abbrev(store.days_of(s))),
            'One-shot date: {}'.format(s.get('date') or '(none, weekly)'),
            'Time: {:02d}:{:02d}'.format(s.get('hour', 0), s.get('minute', 0)),
            'Warning minutes: {}'.format(s.get('warning', 5)),
            'When the countdown ends: {}'.format(
                store.TIMEOUT_ACTION_SHORT[store.timeout_action_of(s)]),
            'Skip the banner, just play at the set time: {}'.format(
                'yes' if s.get('autoopen') else 'no'),
            'Allow snooze: {}'.format('yes' if s.get('snooze') else 'no'),
            'Duplicate slot',
            '[COLOR red]Delete slot[/COLOR]',
        ]
        idx = xbmcgui.Dialog().select('Edit: {}'.format(name), options)
        if idx < 0:
            return

        if idx == 0:
            s['enabled'] = not s.get('enabled')
        elif idx == 1:
            if is_seq:
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
            prompt = 'Label' if is_seq else 'Label (leave empty to use favourite name)'
            label = xbmcgui.Dialog().input(prompt, s.get('label', ''), type=xbmcgui.INPUT_ALPHANUM)
            s['label'] = label
        elif idx == 3:
            days = _pick_days(store.days_of(s))
            if days:
                s['days'] = days
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
            warn = _pick_warning(s.get('warning', 5))
            if warn is not None:
                s['warning'] = warn
        elif idx == 7:
            action = _pick_timeout_action(store.timeout_action_of(s))
            if action is not None:
                s['timeout_action'] = action
        elif idx == 8:
            s['autoopen'] = not s.get('autoopen')
        elif idx == 9:
            s['snooze'] = not s.get('snooze')
        elif idx == 10:
            import copy
            dup = copy.deepcopy(s)
            dup['id'] = store.next_id(slots)
            dup['label'] = (s.get('label') or s.get('favourite', '')) + ' (copy)'
            slots.append(dup)
            store.save_slots(slots)
            _notify('Duplicated slot')
            continue
        elif idx == 11:
            if xbmcgui.Dialog().yesno('Engage', 'Delete "{}"?'.format(name)):
                slots = [x for x in slots if x.get('id') != slot_id]
                store.save_slots(slots)
                _log('deleted slot id={}'.format(slot_id))
                _notify('Deleted: {}'.format(name))
                return
            continue

        store.save_slots(slots)


def _safe_day_index(day):
    try:
        return DAY_NAMES.index(day)
    except (ValueError, TypeError):
        return 0


def _days_abbrev(days):
    """Compact day summary, e.g. 'Mon,Wed,Fri' or 'Every day'."""
    order = [d for d in DAY_NAMES if d in days]
    if len(order) == 7:
        return 'Every day'
    if not order:
        return '?'
    return ','.join(d[:3] for d in order)


def _pick_days(current):
    """Multi-select weekday picker. Returns a day-name list (calendar order) or
    None if cancelled / nothing chosen."""
    preselect = [i for i, d in enumerate(DAY_NAMES) if d in current]
    sel = xbmcgui.Dialog().multiselect('Days of week', DAY_NAMES, preselect=preselect)
    if not sel:
        return None
    return [DAY_NAMES[i] for i in sel]


def _edit_date(current):
    """Set or clear the one-shot date. Returns 'YYYY-MM-DD', '' to clear, or
    None if the user backed out."""
    choices = ['Set a date...', 'Clear (repeat weekly)']
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
