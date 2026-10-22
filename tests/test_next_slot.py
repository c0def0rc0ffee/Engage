#!/usr/bin/env python3
"""
<summary>
Unit test for the next slot the scheduler publishes to Kodi's Home window for
skins: the when_label wording, which occurrence of a slot counts as next, the
numbered Engage.Next.N.* rows (title and When only) for a skin's Coming up list, the order they are
written and cleared in, and the rule that a property is written only when its
value changes.
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


def make_scheduler(describe_results, runtimes=None):
    """
    <summary>
    An EngageScheduler whose library lookups are replaced by a dict of slot id to
    (episode_label, thumb), counting how often each is asked for, and a dict of slot
    id to the running time of each coming showing (COMING-UP-RUNTIME).
    </summary>
    """
    store.set_list_picks({})  # COMING-UP-PICK-AHEAD: picks persist, so each scheduler starts with none
    sched = scheduler.EngageScheduler()
    sched._gui_ready = True
    sched.describe_calls = []

    def describe(slot):
        sched.describe_calls.append(slot.id)
        return describe_results.get(slot.id, ('', ''))

    sched._describe_slot = describe
    sched._lookup_runtimes = lambda slot: (runtimes or {}).get(slot.id, [])
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
    equal(scheduler.when_label(at(-1, 22), now), 'Yesterday 22:00', 'an overdue occurrence from the day before')


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
    equal(due, datetime(2026, 10, 11, 19, 0), 'exactly six hours past, the window is inclusive, still yesterday')
    due, _ = sched._next_occurrence(sunday_only, now.replace(hour=1, minute=1) + timedelta(days=1))
    equal(due, datetime(2026, 10, 18, 19, 0), 'six hours and a minute past, beyond catch-up, next week')
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

    # Yesterday's occurrence is still next after midnight while the catch-up
    # window covers it, or while a snooze on it is running.
    late = scheduler.Slot(make_slot(6, 'Late show', ['Saturday'], 22))
    after_midnight = datetime(2026, 10, 11, 0, 30)  # Sunday, 2.5 hours after Saturday 22:00
    due, key = sched._next_occurrence(late, after_midnight)
    equal((due, key), (datetime(2026, 10, 10, 22, 0), '2026-10-10-6-2200'),
          'yesterday\'s occurrence inside the catch-up window is next')
    _Addon.settings['catchup_hours'] = 0
    due, _ = sched._next_occurrence(late, after_midnight)
    equal(due, datetime(2026, 10, 17, 22, 0), 'with the window off it has gone')
    sched.snoozed['2026-10-10-6-2200'] = datetime(2026, 10, 11, 0, 45)
    due, _ = sched._next_occurrence(late, after_midnight)
    equal(due, datetime(2026, 10, 10, 22, 0), 'unless a snooze on it is still running')
    sched.snoozed.clear()
    _Addon.settings['catchup_hours'] = 6


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
        single = [w[1] for w in WRITES if not w[1][len('Engage.Next.'):].split('.')[0].isdigit()]
        equal(single[-1], 'Engage.Next.Label', 'Label is written last of the single set')
        equal(got.get('1.Label'), 'TNG', 'row 1 is the same occurrence as the single set')
        equal(got.get('1.Episode'), None, 'rows carry no episode line')
        equal(got.get('1.When'), 'Tuesday 20:30', 'row 1 When')
        equal(got.get('2.Label'), 'Voyager', 'row 2 is the next occurrence after it')
        equal(got.get('2.Episode'), None, 'row 2 has no episode line either')
        equal(got.get('2.When'), 'Thursday 19:00', 'row 2 When')
        equal(got.get('3.Label'), None, 'no third occurrence, nothing written for row 3')
        equal(WRITES[-1][1], 'Engage.Next.1.Label', 'row 1 Label is written last of all')
        equal(len(WRITES), 16, 'every property written once: ten single, two rows of three (no Runtime known)')
        equal(sched.describe_calls, [2], 'only the single set slot is resolved, rows need no lookup')

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
        single = [name for name in written if not name[len('Engage.Next.'):].split('.')[0].isdigit()]
        equal(single[-1], 'Engage.Next.Label', 'Label still written last of the single set')
        equal(got.get('1.Label'), 'Voyager', 'row 1 follows the single set')
        equal(got.get('2.Label'), None, 'row 2 is cleared when the list shrinks')
        equal(got.get('2.When'), None, 'row 2 When cleared too')
        row2 = [w for w in WRITES if w[1].startswith('Engage.Next.2.')]
        equal(row2[0], ('clear', 'Engage.Next.2.Label'), 'a row dropping off the list clears Label first')
        equal(written[-1], 'Engage.Next.1.Label', 'row 1 Label still written last of all')

        # A save from the slot manager arrives as a notification and clears the cache.
        # _publish_async is swapped for a recorder so no thread can refill the cache
        # before the check; the real publish is covered by the direct calls above.
        async_reasons = []
        sched._publish_async = async_reasons.append
        try:
            sched._on_notification('service.engage', 'Other.slots_saved', 'null')
        finally:
            del sched._publish_async
        equal(sched._next_item_cache, {}, 'slots_saved clears the episode cache')
        equal(async_reasons, ['slots saved'], 'slots_saved asks for one republish')

        # Nothing enabled: everything cleared, Label first.
        store.save_slots([make_slot(1, 'Voyager', ['Thursday'], 19, enabled=False)])
        del WRITES[:]
        sched._publish_next('all disabled')
        equal(props(), {}, 'no enabled slot clears every property, rows included')
        equal(WRITES[0], ('clear', 'Engage.Next.Label'), 'Label is cleared first')
        rows = [w for w in WRITES if w[1][len('Engage.Next.'):].split('.')[0].isdigit()]
        equal(rows[0], ('clear', 'Engage.Next.1.Label'), 'then row 1, Label first')
        check(WRITES.index(rows[0]) > WRITES.index(('clear', 'Engage.Next.Episode')),
              'the single set is cleared before the rows')
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


# --- next_property_order ----------------------------------------------------------

def test_property_order():
    """<summary>Rows written from 22 down to 1, Label last; cleared from 1 up, Label first.</summary>"""
    publishing = scheduler.next_property_order({'Label': 'x', '1.Label': 'x', '2.Label': 'x'})
    equal(publishing[:10], list(scheduler.NEXT_PROPERTY_NAMES), 'single set first, in its order')
    equal(publishing[10:14], ['22.Label', '22.When', '22.Runtime', '22.Due'],
          'an empty row clears Label first')
    equal(publishing[-8:], ['2.Due', '2.Runtime', '2.When', '2.Label',
                            '1.Due', '1.Runtime', '1.When', '1.Label'],
          'occupied rows end with row 1, each Label last')
    equal(len(publishing), 10 + 4 * scheduler.NEXT_LIST_COUNT, 'every name once')
    check(not any(name.endswith('.Episode') for name in publishing), 'rows have no Episode')
    clearing = scheduler.next_property_order({})
    equal(clearing[:10], list(reversed(scheduler.NEXT_PROPERTY_NAMES)), 'single set reversed')
    equal(clearing[10:18], ['1.Label', '1.When', '1.Runtime', '1.Due',
                            '2.Label', '2.When', '2.Runtime', '2.Due'],
          'rows from 1 upwards, Label first')
    full = scheduler.next_property_order({name: 'x' for name in publishing})
    equal(list(reversed(full[10:])), clearing[10:], 'rows are cleared in the reverse of their publish order')
    equal(list(reversed(full[:10])), clearing[:10], 'and so is the single set')


# --- _occurrences -----------------------------------------------------------------

def test_occurrences():
    """<summary>Every occurrence over the look ahead, earliest first, dealt with ones skipped.</summary>"""
    sched = make_scheduler({})
    now = datetime(2026, 10, 11, 12, 0)  # Sunday noon
    weekly = scheduler.Slot(make_slot(1, 'Voyager', ['Sunday', 'Thursday'], 19))
    found = sched._occurrences(weekly, now)
    equal([due for due, _ in found],
          [datetime(2026, 10, 11, 19, 0), datetime(2026, 10, 15, 19, 0), datetime(2026, 10, 18, 19, 0)],
          'two weekdays over today plus seven days gives three occurrences')
    equal(found[0], sched._next_occurrence(weekly, now), '_next_occurrence is the first of them')
    sched.triggered['2026-10-11-1-1900'] = True
    found = sched._occurrences(weekly, now)
    equal([due for due, _ in found], [datetime(2026, 10, 15, 19, 0), datetime(2026, 10, 18, 19, 0)],
          'a triggered occurrence drops out, the rest stay')
    sched.triggered.clear()
    dated = scheduler.Slot(make_slot(2, 'Film night', ['Monday'], 20, date='2026-10-14'))
    equal([due for due, _ in sched._occurrences(dated, now)], [datetime(2026, 10, 14, 20, 0)],
          'a one off date slot occurs once')
    equal(sched._occurrences(dated, now + timedelta(days=4)), [], 'and not at all once gone')
    equal(sched._next_occurrence(dated, now + timedelta(days=4)), (None, None),
          '_next_occurrence of nothing is (None, None)')


# --- The numbered list ----------------------------------------------------------------

def test_publish_list():
    """<summary>Rows in due order across slots, title and When only, repeats allowed, 22 at most, write and clear order.</summary>"""
    reset_window()
    store.save_slots([
        make_slot(1, 'Voyager', ['Sunday', 'Thursday'], 19),
        make_slot(2, 'Film night', ['Monday'], 20, 30),
        make_slot(3, 'Daily', ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'], 18),
        make_slot(4, 'TNG', ['Saturday'], 21),
        make_slot(5, 'Late', ['Sunday'], 23),
    ])
    sched = make_scheduler({1: ('S03E04, Gravity', ''), 3: ('S01E02, Second', ''), 4: ('S06E15, Tapestry', '')},
                           {1: [2700, 2640, 2580]})
    fixed_now = datetime(2026, 10, 11, 12, 0)  # Sunday noon
    scheduler.datetime = _FixedDatetime.at(fixed_now)
    try:
        sched._publish_next('list')
        got = props()
        expected = [
            ('Voyager', 'Tonight 19:00'),
            ('Late', 'Tonight 23:00'),
            ('Daily', 'Tomorrow 18:00'),
            ('Film night', 'Tomorrow 20:30'),
            ('Daily', 'Tuesday 18:00'),
            ('Daily', 'Wednesday 18:00'),
            ('Daily', 'Thursday 18:00'),
            ('Voyager', 'Thursday 19:00'),
            ('Daily', 'Friday 18:00'),
            ('TNG', 'Saturday 21:00'),
            ('Voyager', 'Sunday 18/10/2026 19:00'),
            ('Late', 'Sunday 18/10/2026 23:00'),
        ]
        for number, (label, when) in enumerate(expected, start=1):
            equal(got.get('{}.Label'.format(number)), label, 'row {} Label'.format(number))
            equal(got.get('{}.Episode'.format(number)), None, 'row {} has no episode line'.format(number))
            equal(got.get('{}.When'.format(number)), when, 'row {} When'.format(number))
        equal(got.get('13.Label'), None, 'nothing past the seven day look ahead is published')
        # COMING-UP-RUNTIME: every row has its Due; Voyager's three showings have
        # their own episode's length, and the slots with nothing known have none.
        equal(got.get('1.Due'), '2026-10-11T19:00', 'row 1 Due')
        equal(got.get('11.Due'), '2026-10-18T19:00', 'row 11 Due')
        equal([got.get('{}.Runtime'.format(n)) for n in (1, 8, 11)], ['2700', '2640', '2580'],
              'each Voyager showing has the length of the episode it will play')
        equal(got.get('2.Runtime'), None, 'no Runtime when nothing is known')
        equal(got.get('Label'), 'Voyager', 'single set Label matches row 1')
        equal(got.get('Episode'), 'S03E04, Gravity', 'the single set keeps its episode line')
        equal(got.get('When'), 'Tonight 19:00', 'single set When matches row 1')
        equal(got.get('Count'), '5', 'Count is still the enabled slots, not the rows')
        equal(sched.describe_calls, [1], 'only the single set slot is resolved, the rows cost no lookups')

        written = [w[1] for w in WRITES]
        labels = [name for name in written if name.endswith('.Label') and name != 'Engage.Next.Label']
        equal(labels, ['Engage.Next.{}.Label'.format(n) for n in range(12, 0, -1)],
              'row Labels written from 12 down to 1')
        for number in range(1, 13):
            row = [name for name in written if name.startswith('Engage.Next.{}.'.format(number))]
            equal(row[-1], 'Engage.Next.{}.Label'.format(number), 'row {} Label written last'.format(number))
        check(written.index('Engage.Next.Label') < written.index('Engage.Next.12.Label'),
              'the single set goes before the rows')
        equal(len(WRITES), 10 + 12 * 3 + 3 - 1,
              'every property written once; the empty Thumb is not written')

        del WRITES[:]
        sched._publish_next('list again')
        equal(WRITES, [], 'nothing changed, nothing written')

        # COMING-UP-MORE: the cap is 22. Four daily slots give 32 in the window.
        every_day = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        store.save_slots([make_slot(n, 'Cap{}'.format(n), every_day, 17 + n) for n in range(1, 5)])
        sched._publish_next('cap')
        got = props()
        check(got.get('22.Label'), 'row 22 is published')
        equal(got.get('23.Label'), None, 'row 23 is not: 22 at most')

        # Two slots due at the same minute keep their list order, in row 1 and the single set.
        store.save_slots([
            make_slot(1, 'First', ['Monday'], 18),
            make_slot(2, 'Second', ['Monday'], 18),
        ])
        del WRITES[:]
        sched._publish_next('tie')
        got = props()
        equal(got.get('Label'), 'First', 'single set: first in list order on a tie')
        equal(got.get('1.Label'), 'First', 'row 1: the same')
        equal(got.get('2.Label'), 'Second', 'row 2: the other')
        equal(got.get('3.Label'), None, 'rows 3 to 11 cleared when the list shrinks')
        equal(props().get('11.When'), None, 'row 11 cleared too')
        dropped = [w for w in WRITES if w[1].startswith('Engage.Next.11.')]
        equal(dropped[0], ('clear', 'Engage.Next.11.Label'), 'a dropped row clears Label first')

        # Everything cleared: single set Label first, then rows 1 upwards Label first.
        store.save_slots([make_slot(1, 'First', ['Monday'], 18, enabled=False)])
        del WRITES[:]
        sched._publish_next('nothing enabled')
        equal(props(), {}, 'everything cleared')
        equal(WRITES[0], ('clear', 'Engage.Next.Label'), 'single Label first')
        rows = [w[1] for w in WRITES if w[1][len('Engage.Next.'):].split('.')[0].isdigit()]
        equal(rows, ['Engage.Next.1.Label', 'Engage.Next.1.When', 'Engage.Next.1.Runtime', 'Engage.Next.1.Due',
                     'Engage.Next.2.Label', 'Engage.Next.2.When', 'Engage.Next.2.Due'],
              'rows cleared from 1 upwards, Label first, only what was set')
        check(written.index('Engage.Next.11.Label') < written.index('Engage.Next.1.Label'),
              'row 11 was written before row 1')
    finally:
        scheduler.datetime = datetime


def test_runtimes():
    """<summary>COMING-UP-RUNTIME: where a row's length comes from, and when it is unknown.</summary>"""
    sched = make_scheduler({})
    equal(sched._details_runtime({'runtime': 2700}), 2700, 'the library runtime')
    equal(sched._details_runtime({'runtime': 0, 'streamdetails': {'video': [{'duration': 2580}]}}), 2580,
          'the first video stream when the runtime is empty')
    equal(sched._details_runtime({'duration': 215}), 215, 'a song duration')
    equal(sched._details_runtime({}), 0, 'nothing known')
    equal(sched._spec_runtime({'file': '/films/a.mkv'}), 0, 'a plain file asks nothing and is unknown')
    equal(sched._spec_runtime(None), 0, 'no spec is unknown')
    rnd = scheduler.Slot(make_slot(7, 'Random night', ['Monday'], 20))
    rnd.kind, rnd.list_mode = 'list', 'random'
    rnd.list_items = []
    equal(sched._row_runtime(rnd, 0, 'k1'), 0, 'a random list with nothing to draw is unknown')
    sched._list_picks['k1'] = {'spec': {'file': '/films/a.mkv'}, 'runtime': 6300}
    sched._picks_checked.add('k1')  # a kept pick already checked against the list
    equal(sched._row_runtime(rnd, 0, 'k1'), 6300, 'a drawn pick gives its length')
    fav = scheduler.Slot(make_slot(8, 'Voyager', ['Monday'], 19))
    sched._lookup_runtimes = lambda slot: [2700, 2640]
    equal([sched._row_runtime(fav, n, None) for n in range(3)], [2700, 2640, 0],
          'the nth showing takes the nth length, unknown past the end')


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
    for test in (test_when_label, test_occurrence_key, test_next_occurrence, test_occurrences,
                 test_next_state, test_property_order, test_publish, test_publish_list,
                 test_runtimes):
        print('running {}'.format(test.__name__))
        test()
    if FAILURES:
        print('{} check(s) failed'.format(len(FAILURES)))
        return 1
    print('test_next_slot: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
