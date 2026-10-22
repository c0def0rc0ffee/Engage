"""
<summary>
Read only access to the lists the Functional skin keeps, so a slot can play
from one of them.
</summary>
<remarks>
The skin's own service owns the file, addon_data/skin.functional/lists.json,
and rewrites it atomically whenever a list changes. Engage never writes to it:
a slot refers to a list by name and reads it afresh each time, so items added,
reordered or removed in the skin (including its Remove Watched switch) apply to
the next slot without any sync step. The shape relied on is the skin's
version 1 format: {"lists": [{"name": str, "items": [item, ...]}]}, each item
carrying label, file, dbtype, dbid, thumb, show, season and episode. A whole
show or season row (the skin's 0.22.0 and later) is a tvshow or season dbtype
with a dbid, no file, and an optional skip list of "SxE" keys; it stands for
its episodes, which the scheduler asks the library for at use. Anything
else in the file is ignored. A missing or unreadable file reads as no lists;
it is never repaired or moved from here, that is the skin's job.
</remarks>
"""

import json
import os

import xbmc
import xbmcvfs

SKIN_ID = 'skin.functional'
LISTS_FILE = 'lists.json'

# Library item types the skin stores a dbid for, mapped to the Player.Open key
# that plays them. Anything else is played by its file path.
DBID_KEYS = {
    'movie': 'movieid',
    'episode': 'episodeid',
    'musicvideo': 'musicvideoid',
    'song': 'songid',
}

# Row types the skin stores for a whole show or season. They have a dbid but
# nothing Player.Open can take: the scheduler expands them into their episodes
# (see is_group and episode_item).
GROUP_TYPES = ('tvshow', 'season')

# How a list slot chooses. The two unwatched modes skip what the library says
# has been played; the two 'any' modes ignore watched state. MODE_TURN walks
# the list one item per showing and wraps, remembering its place in
# list_progress.json (see slots.get_list_progress and turn_index).
MODE_NEXT = 'next'
MODE_TURN = 'turn'
MODE_RANDOM = 'random'
MODE_RANDOM_ANY = 'random_any'
# LIST-EACH, 22/10/2026: every show in the list at once, the next unwatched
# episode of each, played back to back as one Kodi playlist. Only offered for
# a list of TV (EACH_TYPES); anything else in the list is passed over.
MODE_EACH = 'each'
EACH_TYPES = ('tvshow', 'season', 'episode')
DEFAULT_MODE = MODE_NEXT
RANDOM_MODES = (MODE_RANDOM, MODE_RANDOM_ANY)
# Order here is the order the picker shows them in.
MODES = [MODE_NEXT, MODE_TURN, MODE_RANDOM, MODE_RANDOM_ANY, MODE_EACH]
MODE_LABELS = {
    MODE_NEXT: 'In order, the next unwatched item',
    MODE_TURN: 'In order, every item in turn, watched or not',
    MODE_RANDOM: 'Random, any unwatched item',
    MODE_RANDOM_ANY: 'Random, any item, watched or not',
    MODE_EACH: 'Every show, the next unwatched episode of each',
}
MODE_SHORT = {
    MODE_NEXT: 'in order, unwatched',
    MODE_TURN: 'in order, any',
    MODE_RANDOM: 'random, unwatched',
    MODE_RANDOM_ANY: 'random, any',
    MODE_EACH: 'one episode from each show',
}

# Whether a random list slot keeps a series in order, an option that is off
# unless chosen. A series is the films of one Kodi movie set, or the episodes of
# one show, that are in the list. With it on, only the next film of a series is
# in the draw, so a sequel never plays before the film it follows. 'list'
# orders a series by where its items sit in the list, so an exception is fixed
# by reordering the list in the skin; 'date' orders films by release date and
# episodes by season and episode.
SEQUELS_OFF = 'off'
SEQUELS_LIST = 'list'
SEQUELS_DATE = 'date'
SEQUELS = [SEQUELS_OFF, SEQUELS_LIST, SEQUELS_DATE]
SEQUEL_LABELS = {
    SEQUELS_OFF: 'Off, any film of a series can come up',
    SEQUELS_LIST: 'In order, as the series sits in the list',
    SEQUELS_DATE: 'In order, by release date',
}
SEQUEL_SHORT = {
    SEQUELS_OFF: 'sequels any order',
    SEQUELS_LIST: 'sequels in list order',
    SEQUELS_DATE: 'sequels by release date',
}


def _log(msg, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to kodi.log under the 'Engage lists:' prefix.
    </summary>
    <param name="msg">Text to log.</param>
    <param name="level">Kodi log level, LOGINFO by default.</param>
    """
    xbmc.log('Engage lists: {}'.format(msg), level)


def mode_of(slot):
    """
    <summary>
    How a list slot chooses what to play, one of MODES.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>One of MODES; an unknown or missing value gives DEFAULT_MODE.</returns>
    """
    mode = (slot.get('list_mode') or '').strip()
    return mode if mode in MODES else DEFAULT_MODE


def sequels_of(slot):
    """
    <summary>
    Whether a list slot keeps series in order, one of SEQUELS.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>One of SEQUELS; an unknown or missing value gives SEQUELS_OFF, so
    slots saved before the option existed behave as they always did.</returns>
    """
    how = (slot.get('list_sequels') or '').strip()
    return how if how in SEQUELS else SEQUELS_OFF


def mode_text(slot):
    """
    <summary>
    The short description of how a list slot picks, for the slot list and menus.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>MODE_SHORT for its mode, with the sequels choice appended when it is a
    random slot that keeps sequels in order, e.g. 'random, unwatched, sequels in
    list order'.</returns>
    """
    mode, how = mode_of(slot), sequels_of(slot)
    if mode in RANDOM_MODES and how != SEQUELS_OFF:
        return '{}, {}'.format(MODE_SHORT[mode], SEQUEL_SHORT[how])
    return MODE_SHORT[mode]


def lists_path():
    """
    <summary>
    Real path of the skin's lists.json.
    </summary>
    <returns>The path; the file may not exist.</returns>
    <remarks>
    The folder is translated and the file name joined here, rather than
    translating the full special:// path, so a stubbed translatePath in the
    tests can point the folder at a temporary directory.
    </remarks>
    """
    folder = xbmcvfs.translatePath('special://profile/addon_data/{}/'.format(SKIN_ID))
    return os.path.join(folder, LISTS_FILE)


def load_lists():
    """
    <summary>
    Every list the skin holds, in the skin's own order.
    </summary>
    <returns>A list of {'name': str, 'items': [item dict, ...]}; empty when the
    skin is not installed, has no lists, or the file cannot be read.</returns>
    <remarks>
    Entries without a name are dropped, and items that are not dicts or carry
    neither a file nor a dbid are dropped, mirroring the skin's own clean up so
    a hand edited file cannot hand the player an empty item. A whole show or
    season row is kept on its dbid alone.
    </remarks>
    """
    path = lists_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, IOError, ValueError) as e:
        _log('could not read {}: {}'.format(path, e), xbmc.LOGWARNING)
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for entry in data.get('lists') or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name') or '').strip()
        if not name:
            continue
        items = [it for it in (entry.get('items') or [])
                 if isinstance(it, dict) and (it.get('file') or _dbid(it) or is_group(it))]
        out.append({'name': name, 'items': items})
    return out


def find_list(name, lists=None):
    """
    <summary>
    The list with this name, matched without regard to case as the skin does.
    </summary>
    <param name="name">The list name a slot stores.</param>
    <param name="lists">The result of load_lists when the caller already has it.</param>
    <returns>The list dict, or None when no list has that name.</returns>
    """
    wanted = (name or '').strip().lower()
    if not wanted:
        return None
    for entry in (load_lists() if lists is None else lists):
        if entry['name'].lower() == wanted:
            return entry
    return None


def _dbid(item):
    """
    <summary>
    The item's library id as an int, 0 when it has none.
    </summary>
    <param name="item">One list item dict.</param>
    <returns>The dbid, or 0 when it is missing, not a number, or the dbtype is not a
    library type the skin keeps an id for.</returns>
    """
    if item.get('dbtype') not in DBID_KEYS:
        return 0
    try:
        return int(item.get('dbid') or 0)
    except (ValueError, TypeError):
        return 0


def is_group(item):
    """
    <summary>
    Whether a list item is a whole show or season row rather than one thing to play.
    </summary>
    <param name="item">One list item dict.</param>
    <returns>True for a tvshow or season row with a usable dbid.</returns>
    """
    if item.get('dbtype') not in GROUP_TYPES:
        return False
    try:
        return int(item.get('dbid') or 0) > 0
    except (ValueError, TypeError):
        return False


def episode_key(season, episode):
    """
    <summary>
    The "season x episode" key a group row's skip list uses.
    </summary>
    <param name="season">The season number.</param>
    <param name="episode">The episode number.</param>
    <returns>A string such as '2x5', the same form the skin writes.</returns>
    """
    try:
        return '{}x{}'.format(int(season or 0), int(episode or 0))
    except (ValueError, TypeError):
        return '0x0'


def episode_item(row, group):
    """
    <summary>
    One episode of a whole show or season row, in the shape of a list item.
    </summary>
    <param name="row">An episode from VideoLibrary.GetEpisodes, with title, file,
    season, episode, showtitle, art, thumbnail and playcount.</param>
    <param name="group">The group row it came from, for the fallback poster.</param>
    <returns>A list item dict for the episode, carrying its library playcount under
    'engage_playcount' so the scheduler need not ask for it again; the skin never
    writes that key, so a stored item cannot carry a stale one.</returns>
    """
    art = row.get('art') or {}
    return {
        'label': row.get('title') or row.get('label') or '',
        'file': row.get('file') or '',
        'dbtype': 'episode',
        'dbid': int(row.get('episodeid') or 0),
        'thumb': (art.get('tvshow.poster') or art.get('season.poster')
                  or group.get('thumb') or row.get('thumbnail') or ''),
        'show': row.get('showtitle') or group.get('show') or '',
        'season': str(row.get('season', '')),
        'episode': str(row.get('episode', '')),
        'engage_playcount': int(row.get('playcount') or 0),
        # LIST-EACH: the running time in seconds, so a showing's total needs no
        # further lookups; 0 when the library has none.
        'engage_runtime': int(row.get('runtime') or 0),
    }


def play_spec(item):
    """
    <summary>
    The Player.Open item for one list item: by library id when it has one, else by
    file path.
    </summary>
    <param name="item">One list item dict.</param>
    <returns>A dict such as {'movieid': 3} or {'file': '...'}, or None when the item
    has neither.</returns>
    """
    dbid = _dbid(item)
    if dbid:
        return {DBID_KEYS[item['dbtype']]: dbid}
    if item.get('file'):
        return {'file': item['file']}
    return None


def library_ref(item):
    """
    <summary>
    The item as a {'type', 'id'} reference for the video library lookups the
    scheduler already makes for sequences.
    </summary>
    <param name="item">One list item dict.</param>
    <returns>The reference for a library movie or episode, None for anything else
    (music videos, songs and plain files have no lastplayed lookup in Engage).</returns>
    """
    dbid = _dbid(item)
    if dbid and item.get('dbtype') in ('movie', 'episode'):
        return {'type': item['dbtype'], 'id': dbid}
    return None


def item_label(item):
    """
    <summary>
    The line shown on the banner and to skins for one list item.
    </summary>
    <param name="item">One list item dict.</param>
    <returns>'Show · S01E02, Title' for an episode whose season and episode are
    numbers, otherwise the item's label, falling back to the file name.</returns>
    <remarks>
    The episode form matches the one sequence slots use, so a skin sees the same
    shape whichever kind of slot is next.
    </remarks>
    """
    label = item.get('label') or os.path.basename(item.get('file') or '') or '?'
    if item.get('dbtype') == 'episode' and item.get('show'):
        try:
            return '{} · S{:02d}E{:02d}, {}'.format(
                item['show'], int(item.get('season')), int(item.get('episode')), label)
        except (ValueError, TypeError):
            pass
    return label


def all_tv(items):
    """
    <summary>
    Whether a list holds only TV: whole shows, seasons and episodes.
    </summary>
    <param name="items">The list's items.</param>
    <returns>True when there is at least one item and every one is TV.</returns>
    <remarks>LIST-EACH: the one episode from each show mode is offered only then.</remarks>
    """
    return bool(items) and all(item.get('dbtype') in EACH_TYPES for item in items)


def each_label(episodes):
    """
    <summary>
    The banner and Coming up line for one showing of a one episode from each show slot.
    </summary>
    <param name="episodes">The episode items it will play, in order.</param>
    <returns>One episode's own label (see item_label) when there is one, otherwise
    '3 episodes: Voyager S03E04, Babylon 5 S02E10, Doctor Who S01E05'.</returns>
    """
    if len(episodes) == 1:
        return item_label(episodes[0])
    parts = []
    for item in episodes:
        try:
            parts.append('{} S{:02d}E{:02d}'.format(
                item.get('show') or item.get('label') or '?',
                int(item.get('season')), int(item.get('episode'))))
        except (ValueError, TypeError):
            parts.append(item.get('show') or item.get('label') or '?')
    return '{} episodes: {}'.format(len(episodes), ', '.join(parts))


def item_identity(item):
    """
    <summary>
    A hashable identity for one item, for cache signatures.
    </summary>
    <param name="item">One list item dict.</param>
    <returns>(dbtype, dbid) for a library item or a whole show or season row,
    ('file', path) otherwise.</returns>
    """
    dbid = _dbid(item)
    if dbid:
        return (item.get('dbtype'), dbid)
    if is_group(item):
        return (item['dbtype'], int(item['dbid']))
    return ('file', item.get('file') or '')


def turn_index(items, progress):
    """
    <summary>
    Which item an 'every item in turn' slot plays next.
    </summary>
    <param name="items">The list's items as the skin has them now; not empty.</param>
    <param name="progress">What slots.get_list_progress returned: {'identity': [...],
    'index': n} for the item played last time, or None when nothing has played yet.</param>
    <returns>An index into items.</returns>
    <remarks>
    The item after the one played last, found by identity so reordering the list in
    the skin does not lose the place, wrapping to the first after the last. When that
    item has since been removed, its old position is used instead: the item that slid
    into its place is the one after it, so nothing is skipped. With no progress, or
    progress that cannot be read, the first item plays.
    </remarks>
    """
    if not progress:
        return 0
    last = tuple(progress.get('identity') or ())
    for i, it in enumerate(items):
        if item_identity(it) == last:
            return (i + 1) % len(items)
    try:
        return int(progress.get('index', 0)) % len(items)
    except (ValueError, TypeError):
        return 0


def _series_orders(series):
    """
    <summary>
    Each series' list indexes, first to last.
    </summary>
    <param name="series">{index: (group, rank)} as series_pool takes it.</param>
    <returns>{group: [index, ...]} sorted by rank, then by list position.</returns>
    """
    groups = {}
    for index, (group, rank) in series.items():
        groups.setdefault(group, []).append((rank, index))
    return {group: [index for _, index in sorted(members)] for group, members in groups.items()}


def series_pool(items, pool, series, progress=None):
    """
    <summary>
    Narrow a random draw so each series offers only its next item.
    </summary>
    <param name="items">The list's items as the skin has them now.</param>
    <param name="pool">The indexes the draw would otherwise pick from.</param>
    <param name="series">{index: (group, rank)} for every item that belongs to a
    series; items not in it are standalone and are never held back.</param>
    <param name="progress">None to take the earliest item of each series still in
    the pool, which is what the unwatched mode wants. Otherwise {group: {'identity',
    'index'}} from slots.get_series_progress: each series then offers the item after
    the one it played last, wrapping after the last, which is what a draw that
    ignores watched state needs.</param>
    <returns>The pool with every later item of a series removed, in pool order. It
    can be empty only when the pool held nothing but series items that were held
    back; the caller then keeps the unnarrowed pool.</returns>
    <remarks>
    The walk through a series reuses turn_index, so a reordered or shortened series
    keeps its place the same way an in turn slot does.
    </remarks>
    """
    allowed = set(pool)
    held_back = set()
    for group, order in _series_orders(series).items():
        if progress is None:
            keep = [i for i in order if i in allowed][:1]
        else:
            keep = [order[turn_index([items[i] for i in order], progress.get(group))]]
        held_back.update(i for i in order if i not in keep)
    return [i for i in pool if i not in held_back]


def series_position(items, series, index):
    """
    <summary>
    Where one item sits in its series, for recording what a series played last.
    </summary>
    <param name="items">The list's items.</param>
    <param name="series">{index: (group, rank)} as series_pool takes it.</param>
    <param name="index">The list index of the item chosen.</param>
    <returns>(group, identity, position in the series), or None when the item is
    standalone.</returns>
    """
    if index not in series:
        return None
    group = series[index][0]
    order = _series_orders(series)[group]
    return group, item_identity(items[index]), order.index(index)
