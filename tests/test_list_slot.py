#!/usr/bin/env python3
"""
<summary>
Unit test for slots that play from a Functional skin list and for the
every few weeks, monthly and yearly repeats: reading the skin's lists.json, which item each of
the four list modes plays (in order or random, unwatched or any), that a
random pick holds for its occurrence, that the optional sequels in order switch
keeps a movie set from jumping ahead, that the in turn mode keeps its place,
what the skin is shown, the recent watch lookup, and which days a weekly,
every few weeks, monthly, yearly or one-shot slot runs on.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero on the first
failure. No Kodi is needed: the xbmc modules are stubbed in sys.modules before
the add-on is imported, both addon_data folders point at one temporary
directory, and JSON-RPC is answered from a dict of library playcounts and
lastplayed stamps, with every Player.Open recorded.

Usage:
    python3 tests/test_list_slot.py
</remarks>
"""

import json
import os
import sys
import tempfile
import types
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, 'service.engage')
DATA_DIR = tempfile.mkdtemp(prefix='engage-list-test-')

# --- Kodi stubs, installed before the add-on modules are imported -----------

# (method, id) -> details dict the fake library returns.
LIBRARY = {}
PLAYED = []
# tvshowid -> episode rows; seasonid -> {'tvshowid', 'season'}; episodeid -> playcount.
EPISODES = {}
SEASONS = {}
EPISODE_PLAYS = {}
NOTIFIED = []


def fake_jsonrpc(request):
    """
    <summary>
    Answer the JSON-RPC calls the scheduler makes for list slots from LIBRARY.
    </summary>
    <param name="request">The JSON request string.</param>
    <returns>The JSON response string; an unknown id answers with no result, as
    Kodi does for a missing item.</returns>
    """
    req = json.loads(request)
    method, params = req['method'], req.get('params', {})
    if method == 'Player.Open':
        PLAYED.append(params['item'])
        return json.dumps({'result': 'OK'})
    result_keys = {
        'VideoLibrary.GetMovieDetails': ('movieid', 'moviedetails'),
        'VideoLibrary.GetEpisodeDetails': ('episodeid', 'episodedetails'),
        'VideoLibrary.GetMusicVideoDetails': ('musicvideoid', 'musicvideodetails'),
        'AudioLibrary.GetSongDetails': ('songid', 'songdetails'),
    }
    if method == 'VideoLibrary.GetSeasonDetails':
        details = SEASONS.get(params['seasonid'])
        return json.dumps({'result': {'seasondetails': details}} if details else {'error': {}})
    if method == 'VideoLibrary.GetEpisodes':
        rows = [dict(row, playcount=EPISODE_PLAYS.get(row['episodeid'], 0))
                for row in EPISODES.get(params['tvshowid'], [])
                if 'season' not in params or row['season'] == params['season']]
        return json.dumps({'result': {'episodes': rows}})
    if method in result_keys:
        id_key, result_key = result_keys[method]
        details = LIBRARY.get((method, params[id_key]))
        return json.dumps({'result': {result_key: details}} if details else {'error': {}})
    sys.exit('unexpected JSON-RPC call in test: {}'.format(request))


class _Addon:
    """<summary>Stand-in for xbmcaddon.Addon with the settings the scheduler reads.</summary>"""
    settings = {'catchup_hours': 6, 'debug': False, 'recent_watch_hours': 4}

    def __init__(self, addon_id=None):
        pass

    def getSettingInt(self, key):
        return int(self.settings[key])

    def getSettingBool(self, key):
        return bool(self.settings[key])

    def getSetting(self, key):
        return str(self.settings.get(key, ''))

    def getAddonInfo(self, key):
        return ADDON_DIR if key == 'path' else '0.0.0'


class _Dialog:
    """<summary>Stand-in for xbmcgui.Dialog that records notifications.</summary>"""

    def notification(self, *args):
        NOTIFIED.append(args)


class _Monitor:
    """<summary>Stand-in for xbmc.Monitor that never aborts.</summary>"""

    def abortRequested(self):
        return False

    def waitForAbort(self, seconds=0):
        return False


class _Player:
    """<summary>Stand-in for xbmc.Player; nothing ever plays.</summary>"""

    def isPlaying(self):
        return False


xbmc = types.ModuleType('xbmc')
xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
xbmc.log = lambda msg, level=1: None
xbmc.Monitor = _Monitor
xbmc.Player = _Player
xbmc.executebuiltin = lambda cmd: None
xbmc.executeJSONRPC = fake_jsonrpc
xbmc.getCondVisibility = lambda cond: True
xbmc.sleep = lambda ms: None

xbmcaddon = types.ModuleType('xbmcaddon')
xbmcaddon.Addon = _Addon

xbmcgui = types.ModuleType('xbmcgui')
xbmcgui.Window = object
xbmcgui.WindowXMLDialog = object
xbmcgui.Dialog = _Dialog
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_WARNING = xbmcgui.NOTIFICATION_ERROR = 'info'

xbmcvfs = types.ModuleType('xbmcvfs')
xbmcvfs.translatePath = lambda path: DATA_DIR + os.sep

for name, module in (('xbmc', xbmc), ('xbmcaddon', xbmcaddon), ('xbmcgui', xbmcgui), ('xbmcvfs', xbmcvfs)):
    sys.modules[name] = module
sys.path.insert(0, ADDON_DIR)

from resources.lib import functional_lists  # noqa: E402
from resources.lib import scheduler  # noqa: E402
from resources.lib import slots as store  # noqa: E402

# --- Helpers ------------------------------------------------------------------

FAILURES = []


def check(condition, message):
    """
    <summary>Record a failure when the condition is false; the run carries on.</summary>
    <param name="condition">What must hold.</param>
    <param name="message">What to report when it does not.</param>
    """
    if not condition:
        FAILURES.append(message)
        print('FAIL: {}'.format(message))


def equal(actual, expected, what):
    """<summary>check() for an equality, printing both sides on failure.</summary>"""
    check(actual == expected, '{}: got {!r}, expected {!r}'.format(what, actual, expected))


def item(label, dbtype='movie', dbid=0, file='', **extra):
    """<summary>A list item in the skin's shape.</summary>"""
    out = {'label': label, 'file': file, 'dbtype': dbtype, 'dbid': dbid, 'thumb': 'thumb-' + label,
           'duration': 0, 'show': '', 'season': '', 'episode': '', 'artist': '', 'year': '',
           'kind': 'video'}
    out.update(extra)
    return out


def write_lists(lists):
    """<summary>Write the skin's lists.json into the temporary addon_data folder.</summary>"""
    with open(os.path.join(DATA_DIR, 'lists.json'), 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'last': '', 'lists': lists}, f)


def movie(movieid, playcount=0, lastplayed='', setid=0, premiered=''):
    """<summary>Put a movie in the fake library, optionally in a movie set.</summary>"""
    LIBRARY[('VideoLibrary.GetMovieDetails', movieid)] = {
        'movieid': movieid, 'playcount': playcount, 'lastplayed': lastplayed,
        'setid': setid, 'premiered': premiered, 'year': int(premiered[:4] or 0)}


def save_list_slot(name, mode, slot_id=1, sequels=None):
    """<summary>Store one enabled list slot and return its dict.</summary>"""
    data = store.new_list_slot(name, mode)
    if sequels:
        data['list_sequels'] = sequels
    data['id'] = slot_id
    store.save_slots([data])
    return data


def make_scheduler():
    """<summary>An EngageScheduler ready for the list checks.</summary>"""
    store.set_list_picks({})  # COMING-UP-PICK-AHEAD: picks persist, so each scheduler starts with none
    sched = scheduler.EngageScheduler()
    sched._gui_ready = True
    return sched


# --- The skin's file ---------------------------------------------------------------

def test_load_lists():
    """<summary>Missing, broken and well formed lists.json.</summary>"""
    path = os.path.join(DATA_DIR, 'lists.json')
    if os.path.exists(path):
        os.remove(path)
    equal(functional_lists.load_lists(), [], 'no file reads as no lists')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('{not json')
    equal(functional_lists.load_lists(), [], 'a broken file reads as no lists')
    write_lists([
        {'name': 'Saturday Night Movie', 'items': [item('Alpha', dbid=1), {'label': 'empty'}, 'junk']},
        {'name': '', 'items': [item('Nameless', dbid=9)]},
        {'name': 'Docs', 'items': [item('Clip', dbtype='', file='/media/clip.mkv')]},
    ])
    lists = functional_lists.load_lists()
    equal([entry['name'] for entry in lists], ['Saturday Night Movie', 'Docs'], 'nameless list dropped')
    equal([it['label'] for it in lists[0]['items']], ['Alpha'], 'items with no file or dbid dropped')
    equal(functional_lists.find_list('saturday night MOVIE', lists)['name'], 'Saturday Night Movie',
          'name match ignores case')
    equal(functional_lists.find_list('Missing', lists), None, 'unknown name finds nothing')


def test_item_helpers():
    """<summary>Play spec, label and library reference for each kind of item.</summary>"""
    equal(functional_lists.play_spec(item('A', dbid=4)), {'movieid': 4}, 'movie by id')
    equal(functional_lists.play_spec(item('S', dbtype='song', dbid=7)), {'songid': 7}, 'song by id')
    equal(functional_lists.play_spec(item('F', dbtype='', file='/x.mkv')), {'file': '/x.mkv'}, 'file by path')
    equal(functional_lists.play_spec(item('Odd', dbtype='season', dbid=3, file='/s/')), {'file': '/s/'},
          'a dbid on a non library type is ignored')
    ep = item('Pilot', dbtype='episode', dbid=11, show='Show', season='1', episode='2')
    equal(functional_lists.item_label(ep), 'Show · S01E02, Pilot', 'episode label matches sequences')
    equal(functional_lists.library_ref(ep), {'type': 'episode', 'id': 11}, 'episode library ref')
    equal(functional_lists.library_ref(item('S', dbtype='song', dbid=7)), None, 'song has no ref')


# --- List slots in the scheduler ---------------------------------------------

def test_reload():
    """<summary>A list slot takes its items and label from the skin, or is skipped.</summary>"""
    write_lists([{'name': 'Saturday Night Movie', 'items': [item('Alpha', dbid=1)]},
                 {'name': 'Empty', 'items': []}])
    save_list_slot('Saturday Night Movie', 'next')
    sched = make_scheduler()
    slots = sched._reload_slots()
    equal(len(slots), 1, 'list slot loaded')
    equal(slots[0].label, 'Saturday Night Movie', 'label defaults to the list name')
    equal([it['label'] for it in slots[0].list_items], ['Alpha'], 'items read from the skin')
    save_list_slot('Gone', 'next')
    equal(sched._reload_slots(), [], 'a missing list is skipped')
    save_list_slot('Empty', 'next')
    equal(sched._reload_slots(), [], 'an empty list is skipped')


def test_next_mode():
    """<summary>In order plays the first unwatched item and says when all are watched.</summary>"""
    LIBRARY.clear()
    movie(1, playcount=1)
    movie(2)
    write_lists([{'name': 'Films', 'items': [item('Alpha', dbid=1), item('Bravo', dbid=2),
                                             item('Clip', dbtype='', file='/c.mkv')]}])
    save_list_slot('Films', 'next')
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    equal(sched._describe_slot(slot), ('Bravo', 'thumb-Bravo'), 'watched Alpha is passed over')
    del PLAYED[:]
    sched._play_slot(slot, 'k')
    equal(PLAYED, [{'movieid': 2}], 'Bravo is played by library id')
    movie(2, playcount=1)
    equal(sched._describe_slot(slot)[0], 'Clip', 'a plain file counts as unwatched')
    write_lists([{'name': 'Films', 'items': [item('Alpha', dbid=1), item('Bravo', dbid=2)]}])
    slot = sched._reload_slots()[0]
    equal(sched._describe_slot(slot), ('All watched', ''), 'all watched')
    del NOTIFIED[:]
    del PLAYED[:]
    sched._play_slot(slot, 'k')
    equal(PLAYED, [], 'nothing plays when all are watched')
    equal(len(NOTIFIED), 1, 'the user is told instead')


def test_random_mode():
    """<summary>Random picks an unwatched item, holds it per occurrence, then falls back to all.</summary>"""
    LIBRARY.clear()
    movie(1, playcount=1)
    movie(2)
    movie(3)
    write_lists([{'name': 'Films', 'items': [item('Alpha', dbid=1), item('Bravo', dbid=2),
                                             item('Charlie', dbid=3)]}])
    save_list_slot('Films', 'random')
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    equal(sched._describe_slot(slot), ('Random pick', ''), 'no key: the skin is told it is random')
    equal(sched._list_picks, {}, 'and no pick is made')
    seen = set()
    for n in range(40):
        label, _ = sched._describe_slot(slot, '2026-10-17-1-{:04d}'.format(n))
        seen.add(label)
    check('Alpha' not in seen, 'a watched item is never picked while others are unwatched')
    equal(seen, {'Bravo', 'Charlie'}, 'both unwatched items get picked over 40 draws')
    key = '2026-10-17-1-2000'
    banner = sched._describe_slot(slot, key)[0]
    for _ in range(5):
        equal(sched._describe_slot(slot, key)[0], banner, 'the pick holds for its occurrence')
    del PLAYED[:]
    sched._play_slot(slot, key)
    expected = {'Bravo': 2, 'Charlie': 3}[banner]
    equal(PLAYED, [{'movieid': expected}], 'playback is the item the banner named')
    movie(2, playcount=1)
    movie(3, playcount=1)
    label, _ = sched._describe_slot(slot, '2026-10-18-1-2000')
    check(label in ('Alpha', 'Bravo', 'Charlie'), 'all watched still picks something')


def test_pick_ahead():
    """<summary>COMING-UP-PICK-AHEAD: a random pick is kept across a restart, kept for days to come, and drawn again when stale.</summary>"""
    LIBRARY.clear()
    movie(1)
    movie(2)
    movie(3)
    write_lists([{'name': 'Films', 'items': [item('Alpha', dbid=1), item('Bravo', dbid=2),
                                             item('Charlie', dbid=3)]}])
    save_list_slot('Films', 'random')
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    today, saturday = '2026-10-22-1-1900', '2026-10-24-1-1900'
    first = sched._describe_slot(slot, saturday)[0]
    check(first in ('Alpha', 'Bravo', 'Charlie'), 'Saturday is drawn days ahead')
    equal(set(store.get_list_picks()), {saturday}, 'and kept in list_picks.json')

    restarted = scheduler.EngageScheduler()
    slot = restarted._reload_slots()[0]
    equal(restarted._describe_slot(slot, saturday)[0], first, 'a restart names the same pick')

    restarted._describe_slot(slot, today)
    restarted._purge_trackers(datetime(2026, 10, 23, 12, 0))
    equal(set(restarted._list_picks), {saturday}, 'the day after, a spent pick goes and Saturday stays')
    equal(set(store.get_list_picks()), {saturday}, 'and the file says the same')

    movie({'Alpha': 1, 'Bravo': 2, 'Charlie': 3}[first], playcount=1)
    restarted._picks_checked.clear()  # what a library change does
    again = restarted._describe_slot(slot, saturday)[0]
    check(again != first, 'a pick watched since it was drawn is drawn again')

    write_lists([{'name': 'Films', 'items': [item('Delta', dbid=4)]}])
    movie(4)
    slot = restarted._reload_slots()[0]
    restarted._picks_checked.clear()
    equal(restarted._describe_slot(slot, saturday)[0], 'Delta', 'a list edited under the pick draws again')


def test_random_any_mode():
    """<summary>Random any includes watched items and still holds its pick per occurrence.</summary>"""
    LIBRARY.clear()
    movie(1, playcount=1)
    movie(2)
    write_lists([{'name': 'Films', 'items': [item('Alpha', dbid=1), item('Bravo', dbid=2)]}])
    save_list_slot('Films', 'random_any')
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    equal(sched._describe_slot(slot), ('Random pick', ''), 'no key: random any is not resolved')
    seen = {sched._describe_slot(slot, '2026-10-17-1-{:04d}'.format(n))[0] for n in range(40)}
    equal(seen, {'Alpha', 'Bravo'}, 'watched Alpha is in the draw too')
    key = '2026-10-17-1-3000'
    banner = sched._describe_slot(slot, key)[0]
    del PLAYED[:]
    sched._play_slot(slot, key)
    equal(PLAYED, [{'movieid': {'Alpha': 1, 'Bravo': 2}[banner]}], 'random any plays what the banner named')


def test_turn_index():
    """<summary>The in turn walk: next after the last played, wrap, reorder and removal.</summary>"""
    a, b, c = item('A', dbid=1), item('B', dbid=2), item('C', dbid=3)
    ident = functional_lists.item_identity
    equal(functional_lists.turn_index([a, b, c], None), 0, 'nothing played yet: first item')
    equal(functional_lists.turn_index([a, b, c], {'identity': list(ident(a)), 'index': 0}), 1, 'after A comes B')
    equal(functional_lists.turn_index([a, b, c], {'identity': list(ident(c)), 'index': 2}), 0, 'wraps after the last')
    equal(functional_lists.turn_index([c, a, b], {'identity': list(ident(a)), 'index': 0}), 2,
          'found by identity after a reorder')
    equal(functional_lists.turn_index([a, c], {'identity': list(ident(b)), 'index': 1}), 1,
          'B removed: the item that slid into its place plays')
    equal(functional_lists.turn_index([a], {'identity': ['movie', 9], 'index': 4}), 0, 'old index wraps')
    equal(functional_lists.turn_index([a, b], {'index': 'x'}), 0, 'unreadable progress: first item')


def test_turn_mode():
    """<summary>In turn plays every item, watched or not, one per showing, and remembers its place.</summary>"""
    LIBRARY.clear()
    movie(1, playcount=1)
    movie(2, playcount=3)
    write_lists([{'name': 'Films', 'items': [item('Alpha', dbid=1), item('Bravo', dbid=2),
                                             item('Clip', dbtype='', file='/c.mkv')]}])
    save_list_slot('Films', 'turn', slot_id=7)
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    equal(sched._describe_slot(slot), ('Alpha', 'thumb-Alpha'), 'starts at the top, watched or not')
    del PLAYED[:]
    for _ in range(4):
        sched._play_slot(slot, 'k')
    equal(PLAYED, [{'movieid': 1}, {'movieid': 2}, {'file': '/c.mkv'}, {'movieid': 1}],
          'one item per showing, wrapping round')
    equal(store.get_list_progress(7)['index'], 0, 'progress written after each play')
    fresh = make_scheduler()
    equal(fresh._describe_slot(fresh._reload_slots()[0])[0], 'Bravo', 'the place survives a restart')
    sched._next_item_cache[7] = ('stale', 'x', '')
    sched._play_slot(slot, 'k')
    check(7 not in sched._next_item_cache, 'playing drops the published line so the next one shows')


def test_series_pool():
    """<summary>The pure narrowing: earliest unwatched per series, or the walk from progress.</summary>"""
    a, b, c, d = item('A', dbid=1), item('B', dbid=2), item('C', dbid=3), item('D', dbid=4)
    items = [a, b, c, d]
    series = {0: ('set:1', (0,)), 1: ('set:1', (1,)), 2: ('set:1', (2,))}
    equal(functional_lists.series_pool(items, [0, 1, 2, 3], series), [0, 3],
          'only the first of the series and the standalone film')
    equal(functional_lists.series_pool(items, [1, 2, 3], series), [1, 3],
          'first watched: the second is next')
    dated = {0: ('set:1', ('2004', 0)), 1: ('set:1', ('2001', 1)), 2: ('set:1', ('2002', 2))}
    equal(functional_lists.series_pool(items, [0, 1, 2, 3], dated), [1, 3], 'by date the 2001 film leads')
    walk = functional_lists.series_pool
    equal(walk(items, [0, 1, 2, 3], series, {}), [0, 3], 'no progress: walk starts at the first')
    equal(walk(items, [0, 1, 2, 3], series, {'set:1': {'identity': list(functional_lists.item_identity(b)),
                                                       'index': 1}}), [2, 3], 'after B comes C')
    equal(walk(items, [0, 1, 2, 3], series, {'set:1': {'identity': list(functional_lists.item_identity(c)),
                                                       'index': 2}}), [0, 3], 'wraps after the last')
    equal(functional_lists.series_position(items, series, 2), ('set:1', ('movie', 3), 2), 'position of C')
    equal(functional_lists.series_position(items, series, 3), None, 'standalone has no position')
    equal(functional_lists.sequels_of({}), 'off', 'the option is off unless chosen')
    equal(functional_lists.mode_text({'list_mode': 'random', 'list_sequels': 'list'}),
          'random, unwatched, sequels in list order', 'menus name the option when on')
    equal(functional_lists.mode_text({'list_mode': 'next', 'list_sequels': 'list'}), 'in order, unwatched',
          'and not for an in order slot')


def test_random_sequels():
    """<summary>Unwatched random with sequels in order never jumps ahead in a movie set.</summary>"""
    LIBRARY.clear()
    movie(1, setid=5, premiered='2001-11-16')
    movie(2, setid=5, premiered='2002-11-15')
    movie(3, setid=5, premiered='2004-05-31')
    movie(4)
    # Listed out of release order on purpose: list order and date order differ.
    write_lists([{'name': 'Films', 'items': [item('Azkaban', dbid=3), item('Stone', dbid=1),
                                             item('Chamber', dbid=2), item('Solo', dbid=4)]}])
    save_list_slot('Films', 'random', sequels='date')
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    seen = {sched._describe_slot(slot, '2026-10-17-1-{:04d}'.format(n))[0] for n in range(40)}
    equal(seen, {'Stone', 'Solo'}, 'by date only the first film of the set is in the draw')
    movie(1, playcount=1, setid=5, premiered='2001-11-16')
    seen = {sched._describe_slot(slot, '2026-10-18-1-{:04d}'.format(n))[0] for n in range(40)}
    equal(seen, {'Chamber', 'Solo'}, 'once it is watched the next film takes its place')
    save_list_slot('Films', 'random', sequels='list')
    slot = sched._reload_slots()[0]
    LIBRARY.clear()
    movie(1, setid=5, premiered='2001-11-16')
    movie(2, setid=5, premiered='2002-11-15')
    movie(3, setid=5, premiered='2004-05-31')
    movie(4)
    seen = {sched._describe_slot(slot, '2026-10-19-1-{:04d}'.format(n))[0] for n in range(40)}
    equal(seen, {'Azkaban', 'Solo'}, 'by list order the first listed leads, whatever its date')
    save_list_slot('Films', 'random', sequels='off')
    slot = sched._reload_slots()[0]
    seen = {sched._describe_slot(slot, '2026-10-20-1-{:04d}'.format(n))[0] for n in range(60)}
    equal(seen, {'Azkaban', 'Stone', 'Chamber', 'Solo'}, 'with the option off every film can come up')


def test_random_any_sequels():
    """<summary>Random any with sequels in order walks the set film by film and wraps.</summary>"""
    LIBRARY.clear()
    movie(1, playcount=1, setid=5, premiered='2001-11-16')
    movie(2, playcount=1, setid=5, premiered='2002-11-15')
    write_lists([{'name': 'Films', 'items': [item('Stone', dbid=1), item('Chamber', dbid=2)]}])
    save_list_slot('Films', 'random_any', slot_id=9, sequels='list')
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    del PLAYED[:]
    for n in range(3):
        sched._play_slot(slot, '2026-10-21-9-{:04d}'.format(n))
    equal(PLAYED, [{'movieid': 1}, {'movieid': 2}, {'movieid': 1}], 'one film after another, wrapping')
    equal(store.get_series_progress(9)['set:5']['index'], 0, 'the series place is stored')
    store.set_list_progress(9, ('movie', 1), 0)
    check('set:5' in store.get_series_progress(9), 'an in turn write keeps the series places')


def test_recent_watch():
    """<summary>The recent watch check sees a list's library films and ignores the rest.</summary>"""
    LIBRARY.clear()
    movie(1, lastplayed='2026-10-12 18:00:00')
    write_lists([{'name': 'Films', 'items': [item('Clip', dbtype='', file='/c.mkv'), item('Alpha', dbid=1)]}])
    save_list_slot('Films', 'next')
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    equal(sched._last_played_for_slot(slot, datetime(2026, 10, 12, 16, 0)),
          datetime(2026, 10, 12, 18, 0), 'lastplayed found through the list')


def test_cache_signature():
    """<summary>Editing the list in the skin re-resolves the published line.</summary>"""
    LIBRARY.clear()
    movie(1)
    movie(2)
    write_lists([{'name': 'Films', 'items': [item('Alpha', dbid=1)]}])
    save_list_slot('Films', 'next')
    sched = make_scheduler()
    equal(sched._next_item_cached(sched._reload_slots()[0])[0], 'Alpha', 'first line')
    write_lists([{'name': 'Films', 'items': [item('Bravo', dbid=2), item('Alpha', dbid=1)]}])
    equal(sched._next_item_cached(sched._reload_slots()[0])[0], 'Bravo', 'reordered list re-resolves')


def show_library():
    """<summary>A show with Specials and two seasons of two episodes, listed out of order.</summary>"""
    EPISODES.clear()
    SEASONS.clear()
    EPISODE_PLAYS.clear()
    EPISODES[4] = [{'episodeid': 100 + s * 10 + e, 'title': 'Ep{}{}'.format(s, e), 'season': s,
                    'episode': e, 'showtitle': 'Delta', 'file': '/d/{}x{}.mkv'.format(s, e), 'art': {}}
                   for s, e in ((2, 1), (1, 2), (0, 1), (1, 1), (2, 2))]
    SEASONS[20] = {'tvshowid': 4, 'season': 2}


def test_group_rows():
    """<summary>Whole show and season rows: kept, expanded, skipped, and drawn as one entry.</summary>"""
    LIBRARY.clear()
    show_library()
    movie(1)
    whole = item('Delta', dbtype='tvshow', dbid=4, show='Delta')
    season = item('Delta, Season 2', dbtype='season', dbid=20, show='Delta', season='2', skip=['2x1'])
    write_lists([{'name': 'Shows', 'items': [whole]}])
    equal([it['label'] for it in functional_lists.load_lists()[0]['items']], ['Delta'],
          'a whole show row is kept although it has no file')
    save_list_slot('Shows', 'next')
    sched = make_scheduler()
    slots = sched._reload_slots()
    equal(len(slots), 1, 'a list of only show rows is not skipped as empty')
    slot = slots[0]
    equal(sched._describe_slot(slot)[0], 'Delta · S01E01, Ep11', 'in order starts at S01E01, Specials left out')
    EPISODE_PLAYS[111] = 1
    del PLAYED[:]
    sched._play_slot(slot, 'k')
    equal(PLAYED, [{'episodeid': 112}], 'the next unwatched episode plays by library id')
    equal(sched._list_row_labels(slot)[:3], ['Delta · S01E02, Ep12', 'Delta · S02E01, Ep21', 'Delta · S02E02, Ep22'],
          'later showings name the episodes after it')
    write_lists([{'name': 'Shows', 'items': [season]}])
    slot = sched._reload_slots()[0]
    equal([ep['dbid'] for ep in sched._flat_items(slot.list_items)], [122], 'a season row honours its skip list')
    EPISODE_PLAYS[122] = 1
    equal(sched._describe_slot(slot), ('All watched', ''), 'a watched season row is all watched')

    save_list_slot('Shows', 'random')
    write_lists([{'name': 'Shows', 'items': [whole, item('Alpha', dbid=1)]}])
    EPISODE_PLAYS.clear()
    EPISODE_PLAYS[111] = 1
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    seen = {sched._describe_slot(slot, '2026-10-17-1-{:04d}'.format(n))[0] for n in range(60)}
    check('Alpha' in seen, 'the film is drawn')
    check('Delta · S01E01, Ep11' not in seen, 'a watched episode is not drawn from the show')
    check('Delta · S00E01, Ep01' not in seen, 'Specials are not drawn from a whole show')
    check(len(seen - {'Alpha'}) >= 2, 'the show gives one of its unwatched episodes each time')


def test_row_labels():
    """<summary>Coming up rows: a random slot names its list, an in order slot its next items.</summary>"""
    LIBRARY.clear()
    movie(1, playcount=1)
    movie(2)
    movie(3)
    films = [item('Alpha', dbid=1), item('Bravo', dbid=2), item('Charlie', dbid=3)]
    write_lists([{'name': 'Saturday Night Movie', 'items': films}])
    data = save_list_slot('saturday night movie', 'random')
    data['label'] = 'Film night'
    store.save_slots([data])
    sched = make_scheduler()
    slot = sched._reload_slots()[0]
    equal([sched._row_label(slot, n) for n in range(2)], ['Saturday Night Movie'] * 2,
          'random: the list as the skin names it, every showing')
    save_list_slot('Saturday Night Movie', 'next')
    slot = sched._reload_slots()[0]
    equal([sched._row_label(slot, n) for n in range(3)], ['Bravo', 'Charlie', 'Saturday Night Movie'],
          'in order: the next unwatched items, then the slot label once they run out')
    save_list_slot('Saturday Night Movie', 'turn', slot_id=8)
    store.set_list_progress(8, functional_lists.item_identity(films[1]), 1)
    slot = sched._reload_slots()[0]
    equal([sched._row_label(slot, n) for n in range(4)], ['Charlie', 'Alpha', 'Bravo', 'Charlie'],
          'in turn: on from the last played, wrapping')
    other = scheduler.Slot(dict(store.new_slot(favourite='Voyager'), label='Voyager'))
    equal(sched._row_label(other, 0), 'Voyager', 'a favourite slot keeps its label')


# --- Repeats -----------------------------------------------------------------------

def runs(data, *days):
    """<summary>Which of the given dates a slot dict runs on.</summary>"""
    slot = scheduler.Slot(data)
    return [d for d in days if slot.runs_on(d)]


def test_repeats():
    """<summary>Weekly, every few weeks, monthly, yearly and one-shot rules, short months included.</summary>"""
    weekly = store.new_slot(favourite='X', day='Saturday')
    equal(runs(weekly, date(2026, 10, 17), date(2026, 10, 18)), [date(2026, 10, 17)], 'weekly Saturday')
    equal(scheduler.Slot({'days': ['Monday']}).repeat, 'weekly', 'old slots without the key are weekly')

    # 13/10/2026 is a Tuesday; the start week is any day of that week, moved to Monday.
    fortnight = dict(store.new_slot(favourite='X', day='Tuesday'), repeat='every_weeks',
                     week_interval=2, week_start='2026-10-15')
    equal(runs(fortnight, date(2026, 10, 6), date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 20),
               date(2026, 10, 27), date(2026, 11, 3), date(2026, 11, 10)),
          [date(2026, 10, 13), date(2026, 10, 27), date(2026, 11, 10)], 'every other Tuesday')
    check(date(2026, 9, 29) in runs(fortnight, date(2026, 9, 29)), 'the rhythm holds before the start week')
    three = dict(fortnight, week_interval=3, days=['Tuesday', 'Friday'])
    equal(runs(three, date(2026, 10, 13), date(2026, 10, 16), date(2026, 10, 20), date(2026, 10, 27),
               date(2026, 11, 3), date(2026, 11, 6)),
          [date(2026, 10, 13), date(2026, 10, 16), date(2026, 11, 3), date(2026, 11, 6)],
          'every three weeks on two days')
    odd = scheduler.Slot(dict(fortnight, week_interval=9, week_start='soon'))
    equal((odd.week_interval, odd.week_start), (2, date(2024, 1, 1)), 'bad interval and start fall back')
    equal(store.week_start_of({'week_start': '2026-10-18'}), date(2026, 10, 12), 'a Sunday start is its Monday')

    monthly = dict(store.new_slot(favourite='X'), repeat='monthly', month_day=15)
    equal(runs(monthly, date(2026, 10, 14), date(2026, 10, 15), date(2026, 11, 15)),
          [date(2026, 10, 15), date(2026, 11, 15)], 'monthly on the 15th')
    month_end = dict(monthly, month_day=31)
    equal(runs(month_end, date(2026, 4, 30), date(2026, 5, 30), date(2026, 5, 31),
               date(2026, 2, 28), date(2028, 2, 28), date(2028, 2, 29)),
          [date(2026, 4, 30), date(2026, 5, 31), date(2026, 2, 28), date(2028, 2, 29)],
          'the 31st moves to the last day of a shorter month')

    yearly = dict(store.new_slot(favourite='X'), repeat='yearly', month=12, month_day=25)
    equal(runs(yearly, date(2026, 12, 25), date(2026, 11, 25), date(2027, 12, 25)),
          [date(2026, 12, 25), date(2027, 12, 25)], 'yearly on 25th December')
    leap = dict(yearly, month=2, month_day=29)
    equal(runs(leap, date(2027, 2, 28), date(2028, 2, 28), date(2028, 2, 29)),
          [date(2027, 2, 28), date(2028, 2, 29)], '29th February runs on the 28th outside leap years')

    once = dict(monthly, date='2026-10-20')
    equal(runs(once, date(2026, 10, 15), date(2026, 10, 20)), [date(2026, 10, 20)],
          'a one-shot date overrides the repeat')
    bad = dict(store.new_slot(favourite='X'), repeat='fortnightly', month_day=40, month=13)
    slot = scheduler.Slot(bad)
    equal((slot.repeat, slot.month_day, slot.month), ('weekly', 1, 1), 'bad values fall back')

    equal([store.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31)],
          ['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '23rd', '31st'], 'ordinals')


def test_bad_numbers():
    """<summary>Hand edited numbers fall back, and an unreadable slot never stops the others.</summary>"""
    odd = scheduler.Slot({'id': 3, 'hour': '', 'minute': 'x', 'warning': None, 'days': ['Monday']})
    equal((odd.hour, odd.minute, odd.warning, odd.index), (19, 0, 5, 2), 'blank numbers take the defaults')
    wide = scheduler.Slot({'id': 'seven', 'hour': 25, 'minute': 61, 'warning': -4})
    equal((wide.hour, wide.minute, wide.warning, wide.index), (23, 59, 0, -1), 'out of range numbers are clamped')
    good = dict(store.new_slot(favourite='Fine'), id=1)
    store._write_json(store._file_path(), {'version': 1, 'slots': [good, 'junk', dict(good, id=2)]}, indent=2)
    sched = make_scheduler()
    equal([s.id for s in sched._reload_slots()], [1, 2], 'an entry Slot() cannot read is skipped, the rest load')
    check(('unreadable', 'None') in sched._warned_misconfig, 'and warned about once')


def test_monthly_occurrences():
    """<summary>The next-slot walk finds a monthly slot inside the look ahead window only.</summary>"""
    sched = make_scheduler()
    data = dict(store.new_slot(favourite='X'), id=5, repeat='monthly', month_day=17, hour=20)
    slot = scheduler.Slot(data)
    found = sched._occurrences(slot, datetime(2026, 10, 12, 9, 0))
    equal([due for due, _ in found], [datetime(2026, 10, 17, 20, 0)], 'one occurrence this week')
    equal(sched._occurrences(slot, datetime(2026, 10, 1, 9, 0)), [], 'nothing beyond seven days')


def main():
    """<summary>Run every test and exit non zero if any check failed.</summary>"""
    for test in (test_load_lists, test_item_helpers, test_reload, test_next_mode, test_random_mode,
                 test_random_any_mode, test_pick_ahead, test_series_pool, test_random_sequels,
                 test_random_any_sequels, test_turn_index, test_turn_mode, test_recent_watch, test_cache_signature, test_group_rows, test_row_labels, test_repeats, test_monthly_occurrences,
                 test_bad_numbers):
        print('running {}'.format(test.__name__))
        test()
    if FAILURES:
        print('{} check(s) failed'.format(len(FAILURES)))
        return 1
    print('test_list_slot: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
