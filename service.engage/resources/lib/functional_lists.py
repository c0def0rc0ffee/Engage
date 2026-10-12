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
carrying label, file, dbtype, dbid, thumb, show, season and episode. Anything
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

MODE_NEXT = 'next'
MODE_RANDOM = 'random'
DEFAULT_MODE = MODE_NEXT
# Order here is the order the picker shows them in.
MODES = [MODE_NEXT, MODE_RANDOM]
MODE_LABELS = {
    MODE_NEXT: 'In order, the next unwatched item',
    MODE_RANDOM: 'Random, any unwatched item',
}
MODE_SHORT = {
    MODE_NEXT: 'in order',
    MODE_RANDOM: 'random',
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
    How a list slot chooses what to play, 'next' or 'random'.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>One of MODES; an unknown or missing value gives DEFAULT_MODE.</returns>
    """
    mode = (slot.get('list_mode') or '').strip()
    return mode if mode in MODES else DEFAULT_MODE


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
    a hand edited file cannot hand the player an empty item.
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
                 if isinstance(it, dict) and (it.get('file') or _dbid(it))]
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


def item_identity(item):
    """
    <summary>
    A hashable identity for one item, for cache signatures.
    </summary>
    <param name="item">One list item dict.</param>
    <returns>(dbtype, dbid) for a library item, ('file', path) otherwise.</returns>
    """
    dbid = _dbid(item)
    if dbid:
        return (item.get('dbtype'), dbid)
    return ('file', item.get('file') or '')
