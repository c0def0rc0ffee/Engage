#!/usr/bin/env python3
"""
<summary>
Unit test for slots that play from a Functional skin list and for the
monthly and yearly repeats: reading the skin's lists.json, which item an in
order or random list slot plays, that a random pick holds for its occurrence,
what the skin is shown, the recent watch lookup, and which days a weekly,
monthly, yearly or one-shot slot runs on.
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


def movie(movieid, playcount=0, lastplayed=''):
    """<summary>Put a movie in the fake library.</summary>"""
    LIBRARY[('VideoLibrary.GetMovieDetails', movieid)] = {
        'movieid': movieid, 'playcount': playcount, 'lastplayed': lastplayed}


def save_list_slot(name, mode, slot_id=1):
    """<summary>Store one enabled list slot and return its dict.</summary>"""
    data = store.new_list_slot(name, mode)
    data['id'] = slot_id
    store.save_slots([data])
    return data


def make_scheduler():
    """<summary>An EngageScheduler ready for the list checks.</summary>"""
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


# --- Repeats -----------------------------------------------------------------------

def runs(data, *days):
    """<summary>Which of the given dates a slot dict runs on.</summary>"""
    slot = scheduler.Slot(data)
    return [d for d in days if slot.runs_on(d)]


def test_repeats():
    """<summary>Weekly, monthly, yearly and one-shot rules, short months included.</summary>"""
    weekly = store.new_slot(favourite='X', day='Saturday')
    equal(runs(weekly, date(2026, 10, 17), date(2026, 10, 18)), [date(2026, 10, 17)], 'weekly Saturday')
    equal(scheduler.Slot({'days': ['Monday']}).repeat, 'weekly', 'old slots without the key are weekly')

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
                 test_recent_watch, test_cache_signature, test_repeats, test_monthly_occurrences):
        print('running {}'.format(test.__name__))
        test()
    if FAILURES:
        print('{} check(s) failed'.format(len(FAILURES)))
        return 1
    print('test_list_slot: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
