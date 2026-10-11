#!/usr/bin/env python3
"""
<summary>
Unit test for the next slot the scheduler publishes to Kodi's Home window for
skins: the when_label wording, which occurrence of a slot counts as next, and
the rule that a property is written only when its value changes.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero on the first
failure. No Kodi is needed: the xbmc modules are stubbed in sys.modules before
the scheduler is imported, the Home window is a dict that records every
write, and the slot store writes into a temporary folder. The library lookups
behind the episode line are replaced by a counter so the cache can be checked
without JSON-RPC.

Usage:
    python3 tests/test_next_slot.py
</remarks>
"""

import os
import sys
import tempfile
import types
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, 'service.engage')
DATA_DIR = tempfile.mkdtemp(prefix='engage-test-')

# --- Kodi stubs, installed before the add-on modules are imported -----------

WRITES = []
BUILTINS = []


class _Window:
    """
    <summary>
    Stand-in for xbmcgui.Window: one shared property dict, every set and clear recorded.
    </summary>
    """
    props = {}

    def __init__(self, window_id):
        self.window_id = window_id

    def getProperty(self, key):
        return self.props.get(key, '')

    def setProperty(self, key, value):
        self.props[key] = value
        WRITES.append(('set', key, value))

    def clearProperty(self, key):
        self.props.pop(key, None)
        WRITES.append(('clear', key))


class _Addon:
    """
    <summary>
    Stand-in for xbmcaddon.Addon with the settings the scheduler reads.
    </summary>
    """
    settings = {'catchup_hours': 6, 'debug': False, 'poll_interval': 30, 'test_mode': False}

    def __init__(self, addon_id=None):
        pass

    def getSettingInt(self, key):
        return int(self.settings[key])

    def getSettingBool(self, key):
        return bool(self.settings[key])

    def getSetting(self, key):
        return str(self.settings.get(key, ''))

    def setSetting(self, key, value):
        self.settings[key] = value

    def getAddonInfo(self, key):
        return ADDON_DIR if key == 'path' else '0.0.0'


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
xbmc.executebuiltin = lambda cmd: BUILTINS.append(cmd)
xbmc.executeJSONRPC = lambda req: sys.exit('unexpected JSON-RPC call in test: {}'.format(req))
xbmc.getCondVisibility = lambda cond: True
xbmc.sleep = lambda ms: None

xbmcaddon = types.ModuleType('xbmcaddon')
xbmcaddon.Addon = _Addon

xbmcgui = types.ModuleType('xbmcgui')
xbmcgui.Window = _Window
xbmcgui.WindowXMLDialog = object
xbmcgui.Dialog = object
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_WARNING = xbmcgui.NOTIFICATION_ERROR = 'info'

xbmcvfs = types.ModuleType('xbmcvfs')
xbmcvfs.translatePath = lambda path: DATA_DIR + os.sep

for name, module in (('xbmc', xbmc), ('xbmcaddon', xbmcaddon), ('xbmcgui', xbmcgui), ('xbmcvfs', xbmcvfs)):
    sys.modules[name] = module
sys.path.insert(0, ADDON_DIR)

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
    """
    <summary>check() for an equality, printing both sides on failure.</summary>
    """
    check(actual == expected, '{}: got {!r}, expected {!r}'.format(what, actual, expected))


def make_slot(slot_id, label, days, hour, minute=0, date='', enabled=True, favourite='Some Show'):
    """
    <summary>A favourite slot dict as the store would hold it.</summary>
    """
    data = store.new_slot(favourite=favourite, day=days[0])
    data.update({'id': slot_id, 'label': label, 'days': days, 'hour': hour, 'minute': minute,
                 'date': date, 'enabled': enabled})
    return data


def make_scheduler(describe_results):
    """
    <summary>
    An EngageScheduler whose library lookups are replaced by a dict of slot id to
    (episode_label, thumb), counting how often each is asked for.
    </summary>
    """
    sched = scheduler.EngageScheduler()
    sched._gui_ready = True
    sched.describe_calls = []

    def describe(slot):
        sched.describe_calls.append(slot.id)
        return describe_results.get(slot.id, ('', ''))

    sched._describe_slot = describe
    return sched


def props():
    """<summary>The Engage.Next.* properties currently on the stub window, prefix stripped.</summary>"""
    prefix = scheduler.NEXT_PROPERTY_PREFIX
    return {k[len(prefix):]: v for k, v in _Window.props.items() if k.startswith(prefix)}


def reset_window():
    """<summary>Empty the stub window and the write log.</summary>"""
    _Window.props.clear()
    del WRITES[:]


# --- when_label -----------------------------------------------------------------

def test_when_label():
    """<summary>The wording for today, tomorrow, within the week and beyond.</summary>"""
    now = datetime(2026, 10, 11, 12, 0)  # a Sunday
    at = lambda days, hour=19, minute=0: now.replace(hour=hour, minute=minute) + timedelta(days=days)
    equal(scheduler.when_label(at(0), now), 'Tonight 19:00', 'today')
    equal(scheduler.when_label(at(0, 9, 5), now), 'Tonight 09:05', 'today, morning, zero padded')
    equal(scheduler.when_label(at(0, 8), now.replace(hour=10)), 'Tonight 08:00', 'today, time already passed')
    equal(scheduler.when_label(at(1), now), 'Tomorrow 19:00', 'tomorrow')
    equal(scheduler.when_label(at(2), now), 'Tuesday 19:00', 'two days ahead')
    equal(scheduler.when_label(at(6), now), 'Saturday 19:00', 'six days ahead, still bare weekday')
    equal(scheduler.when_label(at(7), now), 'Sunday 18/10/2026 19:00', 'seven days ahead, dated en-GB')
    equal(scheduler.when_label(datetime(2027, 1, 3, 20, 30), now), 'Sunday 03/01/2027 20:30', 'far ahead, dated')


# --- occurrence_key -------------------------------------------------------------

def test_occurrence_key():
    """<summary>The key shape matches the scheduler's per-day trackers.</summary>"""
    slot = scheduler.Slot(make_slot(3, 'Voyager', ['Thursday'], 19, 5))
    equal(scheduler.occurrence_key(slot, datetime(2026, 10, 15, 19, 5)), '2026-10-15-3-1905', 'key shape')
    sched = make_scheduler({})
    equal(sched._today_key(slot), scheduler.occurrence_key(slot, datetime.now()), '_today_key uses the same shape')


# --- _next_occurrence -----------------------------------------------------------

def test_next_occurrence():
    """<summary>Which occurrence of a slot counts as next.</summary>"""
    sched = make_scheduler({})
    now = datetime(2026, 10, 11, 12, 0)  # Sunday noon

    weekly = scheduler.Slot(make_slot(1, 'Voyager', ['Sunday', 'Thursday'], 19))
    due, key = sched._next_occurrence(weekly, now)
    equal(due, datetime(2026, 10, 11, 19, 0), 'weekly slot later today')
    equal(key, '2026-10-11-1-1900', 'key of today\'s occurrence')

    sched.triggered[key] = True
    due, _ = sched._next_occurrence(weekly, now)
    equal(due, datetime(2026, 10, 15, 19, 0), 'triggered today, so Thursday is next')
    sched.triggered.clear()

    sched.cancelled[key] = True
    due, _ = sched._next_occurrence(weekly, now)
    equal(due, datetime(2026, 10, 15, 19, 0), 'cancelled today, so Thursday is next')
    sched.cancelled.clear()

    sunday_only = scheduler.Slot(make_slot(2, 'TNG', ['Sunday'], 19))
    sched.triggered['2026-10-11-2-1900'] = True
    due, _ = sched._next_occurrence(sunday_only, now)
    equal(due, datetime(2026, 10, 18, 19, 0), 'single weekday dealt with today rolls to next week')
    sched.triggered.clear()

    # Past occurrence today: still next inside the catch-up window, gone beyond it.
    _Addon.settings['catchup_hours'] = 6
    due, _ = sched._next_occurrence(sunday_only, now.replace(hour=20))
    equal(due, datetime(2026, 10, 11, 19, 0), 'one hour overdue, inside catch-up, still today')
    due, _ = sched._next_occurrence(sunday_only, now.replace(hour=1) + timedelta(days=1))
    equal(due, datetime(2026, 10, 18, 19, 0), 'six hours past, beyond catch-up, next week')
    _Addon.settings['catchup_hours'] = 0
    due, _ = sched._next_occurrence(sunday_only, now.replace(hour=19, minute=1))
    equal(due, datetime(2026, 10, 11, 19, 0), 'catch-up off, one minute past, inside the grace')
    due, _ = sched._next_occurrence(sunday_only, now.replace(hour=19, minute=3))
    equal(due, datetime(2026, 10, 18, 19, 0), 'catch-up off, three minutes past, next week')
    _Addon.settings['catchup_hours'] = 6

    # One off date slots occur on their date only.
    dated = scheduler.Slot(make_slot(3, 'Film night', ['Monday'], 20, date='2026-10-14'))
    due, _ = sched._next_occurrence(dated, now)
    equal(due, datetime(2026, 10, 14, 20, 0), 'date slot on its date, ignoring its weekday list')
    due, _ = sched._next_occurrence(dated, now + timedelta(days=4))
    equal(due, None, 'date slot already gone')
    far = scheduler.Slot(make_slot(4, 'Far off', ['Monday'], 20, date='2026-11-30'))
    due, _ = sched._next_occurrence(far, now)
    equal(due, None, 'date slot beyond the lookahead is not offered')
    bad_date = scheduler.Slot(make_slot(5, 'Bad date', ['Monday'], 20, date='not-a-date'))
    due, _ = sched._next_occurrence(bad_date, now)
    equal(due, datetime(2026, 10, 12, 20, 0), 'invalid date falls back to the weekly days')


# --- _next_state ----------------------------------------------------------------

def test_next_state():
    """<summary>upcoming, warning, overdue and snoozed.</summary>"""
    sched = make_scheduler({})
    slot = scheduler.Slot(make_slot(1, 'Voyager', ['Sunday'], 19))  # warning 5 minutes
    due = datetime(2026, 10, 11, 19, 0)
    key = scheduler.occurrence_key(slot, due)
    equal(sched._next_state(slot, due, key, due - timedelta(hours=1)), 'upcoming', 'an hour before')
    equal(sched._next_state(slot, due, key, due - timedelta(minutes=5)), 'warning', 'at the warning edge')
    equal(sched._next_state(slot, due, key, due), 'warning', 'at the scheduled minute itself')
    equal(sched._next_state(slot, due, key, due + timedelta(minutes=1)), 'overdue', 'a minute after')
    sched.snoozed[key] = due + timedelta(minutes=10)
    equal(sched._next_state(slot, due, key, due + timedelta(minutes=1)), 'snoozed', 'snoozed wins')


# --- _publish_next and the write discipline -------------------------------------

def test_publish():
    """<summary>Earliest slot wins, every property is set, nothing is rewritten unchanged.</summary>"""
    reset_window()
    store.save_slots([
        make_slot(1, 'Voyager', ['Thursday'], 19),
        make_slot(2, 'TNG', ['Tuesday'], 20, 30),
        make_slot(3, 'Disabled', ['Monday'], 18, enabled=False),
        make_slot(4, 'No favourite', ['Monday'], 18, favourite=''),
    ])
    check(any(cmd.startswith('NotifyAll(service.engage,slots_saved') for cmd in BUILTINS),
          'save_slots announces the save with NotifyAll')

    sched = make_scheduler({2: ('S03E04, Gravity', 'poster.jpg'), 1: ('S01E01, Caretaker', '')})
    fixed_now = datetime(2026, 10, 11, 12, 0)  # Sunday
    scheduler.datetime = _FixedDatetime.at(fixed_now)
    try:
        sched._publish_next('test')
        got = props()
        equal(got.get('Label'), 'TNG', 'Tuesday 20:30 beats Thursday 19:00')
        equal(got.get('Episode'), 'S03E04, Gravity', 'episode line from the resolver')
        equal(got.get('When'), 'Tuesday 20:30', 'When wording')
        equal(got.get('Day'), 'Tuesday', 'Day')
        equal(got.get('Date'), '13/10/2026', 'Date en-GB')
        equal(got.get('Time'), '20:30', 'Time')
        equal(got.get('Thumb'), 'poster.jpg', 'Thumb')
        equal(got.get('SlotId'), '2', 'SlotId')
        equal(got.get('State'), 'upcoming', 'State')
        equal(got.get('Count'), '2', 'Count is the usable enabled slots only')
        equal(WRITES[-1][1], 'Engage.Next.Label', 'Label is written last')
        equal(len(WRITES), 10, 'every property written once')
        equal(sched.describe_calls, [2], 'only the next slot is resolved')

        del WRITES[:]
        sched._publish_next('test again')
        equal(WRITES, [], 'a second publish with nothing changed writes nothing')
        equal(sched.describe_calls, [2], 'the episode line came from the cache')

        # The library changed: cache cleared, resolver asked again, nothing rewritten.
        sched._on_notification('xbmc', 'VideoLibrary.OnUpdate', '{"item":{"id":5,"type":"episode"}}')
        sched._publish_next('after library update')
        equal(sched.describe_calls, [2, 2], 'library update clears the cache')
        equal(WRITES, [], 'same values after the lookup, still nothing written')

        # TNG dealt with: Voyager becomes next, only the changed properties are written.
        sched.triggered['2026-10-13-2-2030'] = True
        del WRITES[:]
        sched._publish_next('TNG triggered')
        got = props()
        equal(got.get('Label'), 'Voyager', 'next slot moves on')
        equal(got.get('When'), 'Thursday 19:00', 'When follows')
        equal(got.get('Thumb'), None, 'empty thumb is cleared, not written as an empty string')
        written = [w[1] for w in WRITES]
        check('Engage.Next.Count' not in written, 'unchanged Count is not rewritten')
        check('Engage.Next.State' not in written, 'unchanged State is not rewritten')
        equal(written[-1], 'Engage.Next.Label', 'Label still written last')

        # A save from the slot manager arrives as a notification and clears the cache.
        sched._on_notification('service.engage', 'Other.slots_saved', 'null')
        equal(sched._next_item_cache, {}, 'slots_saved clears the episode cache')

        # Nothing enabled: everything cleared, Label first.
        store.save_slots([make_slot(1, 'Voyager', ['Thursday'], 19, enabled=False)])
        del WRITES[:]
        sched._publish_next('all disabled')
        equal(props(), {}, 'no enabled slot clears every property')
        equal(WRITES[0], ('clear', 'Engage.Next.Label'), 'Label is cleared first')
        del WRITES[:]
        sched._publish_next('still nothing')
        equal(WRITES, [], 'clearing an already clear window writes nothing')

        # Before the GUI is ready nothing is touched at all.
        store.save_slots([make_slot(1, 'Voyager', ['Thursday'], 19)])
        sched._gui_ready = False
        sched._publish_next('too early')
        equal(WRITES, [], 'no Home window writes before _wait_for_gui has finished')
        sched._gui_ready = True

        # Stopping clears whatever is there.
        sched._publish_next('back on')
        equal(props().get('Label'), 'Voyager', 'published again once ready')
        sched._clear_next()
        equal(props(), {}, 'service stop clears every property')
    finally:
        scheduler.datetime = datetime


class _FixedDatetime(datetime):
    """
    <summary>
    datetime whose now() is pinned, so the publisher's idea of today is the test's.
    </summary>
    """
    fixed = None

    @classmethod
    def at(cls, when):
        cls.fixed = when
        return cls

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


def main():
    """<summary>Run every test and exit non zero if any check failed.</summary>"""
    for test in (test_when_label, test_occurrence_key, test_next_occurrence, test_next_state, test_publish):
        print('running {}'.format(test.__name__))
        test()
    if FAILURES:
        print('{} check(s) failed'.format(len(FAILURES)))
        return 1
    print('test_next_slot: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
