#!/usr/bin/env python3
"""
<summary>
Unit test for the scheduler's handling of one occurrence of a slot
(EngageScheduler._process_slot and the trackers around it): an autoopen slot
gets no banner and never plays twice, a snooze and a catch-up both survive
midnight, a stop issued by Stop and Resume After leaves the queue alone, a
banner reached with no warning window still counts down so its timeout action
can fire, and the banner style a slot's prompt opens with is resolved from the
slot's own choice and the master setting each time the banner opens.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero on the first
failure reported. No Kodi is needed: the xbmc modules are stubbed in sys.modules
before the scheduler is imported, the clock is pinned through a datetime
subclass, the banner is replaced by a function that records the call and answers
with a chosen action, and playback is recorded rather than started.

Usage:
    python3 tests/test_process_slot.py
</remarks>
"""

import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, 'service.engage')
DATA_DIR = tempfile.mkdtemp(prefix='engage-process-test-')

# --- Kodi stubs, installed before the add-on modules are imported -----------

NOTIFIED = []
PLAYING = [False]


class _Window:
    """<summary>Stand-in for xbmcgui.Window: one shared property dict.</summary>"""
    props = {}

    def __init__(self, window_id):
        pass

    def getProperty(self, key):
        return self.props.get(key, '')

    def setProperty(self, key, value):
        self.props[key] = value

    def clearProperty(self, key):
        self.props.pop(key, None)


class _Addon:
    """<summary>Stand-in for xbmcaddon.Addon with the settings the scheduler reads.</summary>"""
    settings = {'catchup_hours': 6, 'catchup_max_prompts': 3, 'debug': False, 'poll_interval': 30,
                'test_mode': False, 'recent_watch_hours': 0}

    def __init__(self, addon_id=None):
        pass

    def getSettingInt(self, key):
        return int(self.settings[key])

    def getSettingBool(self, key):
        return bool(self.settings[key])

    def setSettingBool(self, key, value):
        self.settings[key] = value

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
    """
    <summary>
    Stand-in for xbmc.Player whose playing state the tests set, and whose stop()
    reports itself the way Kodi does, through the scheduler's playback ended hook.
    </summary>
    """
    on_stop = None

    def isPlaying(self):
        return PLAYING[0]

    def stop(self):
        PLAYING[0] = False
        if _Player.on_stop is not None:
            _Player.on_stop()

    def play(self, path):
        PLAYING[0] = True


class _Dialog:
    """<summary>Stand-in for xbmcgui.Dialog that records notifications.</summary>"""

    def notification(self, *args):
        NOTIFIED.append(args)


xbmc = types.ModuleType('xbmc')
xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
xbmc.log = lambda msg, level=1: None
xbmc.Monitor = _Monitor
xbmc.Player = _Player
xbmc.executebuiltin = lambda cmd: None
xbmc.executeJSONRPC = lambda req: json.dumps({'result': {}})
xbmc.getCondVisibility = lambda cond: True
xbmc.sleep = lambda ms: None

xbmcaddon = types.ModuleType('xbmcaddon')
xbmcaddon.Addon = _Addon

xbmcgui = types.ModuleType('xbmcgui')
xbmcgui.Window = _Window
xbmcgui.WindowXMLDialog = object
xbmcgui.Dialog = _Dialog
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_WARNING = xbmcgui.NOTIFICATION_ERROR = 'info'

xbmcvfs = types.ModuleType('xbmcvfs')
xbmcvfs.translatePath = lambda path: DATA_DIR + os.sep

for name, module in (('xbmc', xbmc), ('xbmcaddon', xbmcaddon), ('xbmcgui', xbmcgui), ('xbmcvfs', xbmcvfs)):
    sys.modules[name] = module
sys.path.insert(0, ADDON_DIR)

from resources.lib import prompt  # noqa: E402
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


class _FixedDatetime(datetime):
    """<summary>datetime whose now() is pinned, so the scheduler's clock is the test's.</summary>"""
    fixed = None

    @classmethod
    def now(cls, tz=None):
        """<summary>The pinned time, whatever tz is asked for.</summary>"""
        return cls.fixed


def at(when):
    """<summary>Pin the scheduler's clock to when.</summary>"""
    _FixedDatetime.fixed = when
    scheduler.datetime = _FixedDatetime


def save_slot(**fields):
    """<summary>Store one enabled favourite slot with the given fields and return it.</summary>"""
    data = store.new_slot(favourite='Some Show', day='Wednesday')
    data.update({'id': 1, 'label': 'Show'})
    data.update(fields)
    store.save_slots([data])
    return data


def make_scheduler(answers):
    """
    <summary>
    An EngageScheduler whose banner is a recorder answering from a list of
    (action, snooze minutes), and whose playback is recorded in sched.played.
    </summary>
    <param name="answers">The answers the banner gives, in order; the last one repeats.</param>
    """
    sched = scheduler.EngageScheduler()
    sched._gui_ready = True
    sched.banners = []
    sched.played = []
    answers = list(answers)

    def handle_warning(slot, scheduled_dt, key=None):
        """<summary>Record the banner and answer with the next scripted action.</summary>"""
        sched.banners.append((key, scheduled_dt))
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def play_slot(slot, key=None):
        """<summary>Record the play and mark the player as playing.</summary>"""
        sched.played.append(key)
        PLAYING[0] = True

    sched._handle_warning = handle_warning
    sched._play_slot = play_slot
    return sched


# --- Tests --------------------------------------------------------------------

def test_autoopen_no_banner_no_double_play():
    """<summary>An autoopen slot gets no banner in its warning window and plays exactly once.</summary>"""
    save_slot(autoopen=True, warning=5, hour=19, minute=0)
    sched = make_scheduler([('queue', 5)])
    PLAYING[0] = True
    at(datetime(2026, 10, 14, 18, 57))  # a Wednesday, inside the warning window
    sched._tick()
    equal(sched.banners, [], 'no banner for an autoopen slot before its time')
    equal(sched.queued_next, [], 'nothing queued either')
    at(datetime(2026, 10, 14, 19, 0, 20))
    sched._tick()
    equal(sched.played, ['2026-10-14-1-1900'], 'played once at the set time')
    sched._on_playback_ended()
    equal(sched.played, ['2026-10-14-1-1900'], 'nothing more plays when the current item ends')

    # A queue entry for the same occurrence (a banner timed out to Queue Next
    # under an older build) is dropped rather than played a second time.
    sched = make_scheduler([('queue', 5)])
    slot = sched._reload_slots()[0]
    sched.queued_next.append((slot, '2026-10-14-1-1900'))
    at(datetime(2026, 10, 14, 19, 0, 20))
    sched._tick()
    equal(sched.played, ['2026-10-14-1-1900'], 'autoopen plays the slot')
    equal(sched.queued_next, [], 'the stale queue entry is dropped')
    sched._on_playback_ended()
    equal(sched.played, ['2026-10-14-1-1900'], 'and so it does not play again')
    PLAYING[0] = False


def test_snooze_across_midnight():
    """<summary>A snooze running past midnight comes back with a fresh countdown.</summary>"""
    _Addon.settings['catchup_hours'] = 0
    save_slot(warning=5, hour=23, minute=30)
    sched = make_scheduler([('snooze', 60), ('cancel', 5)])
    at(datetime(2026, 10, 14, 23, 26))
    sched._tick()
    key = '2026-10-14-1-2330'
    equal(sched.snoozed.get(key), datetime(2026, 10, 15, 0, 26), 'snoozed for an hour')
    at(datetime(2026, 10, 15, 0, 0, 30))
    sched._tick()
    check(key in sched.snoozed, 'the snooze survives the first tick after midnight')
    equal(len(sched.banners), 1, 'and no banner yet')
    at(datetime(2026, 10, 15, 0, 27))
    sched._tick()
    equal(len(sched.banners), 2, 'the banner comes back once the snooze runs out')
    equal(sched.banners[1], (key, datetime(2026, 10, 15, 0, 27) + timedelta(seconds=scheduler.LATE_BANNER_SECONDS)),
          'with a short countdown from now, under yesterday\'s key')
    check(key not in sched.snoozed, 'the snooze is spent')
    check(key not in sched.cancelled, 'the answer is let go with its day, since the occurrence cannot come back')
    at(datetime(2026, 10, 15, 0, 28))
    sched._tick()
    equal(len(sched.banners), 2, 'no third banner')
    _Addon.settings['catchup_hours'] = 6


def test_catchup_across_midnight():
    """<summary>A slot missed late in the evening is caught up after midnight, once.</summary>"""
    _Addon.settings['catchup_hours'] = 6
    save_slot(warning=5, hour=22, minute=0)
    sched = make_scheduler([('cancel', 5)])
    key = '2026-10-14-1-2200'
    at(datetime(2026, 10, 15, 0, 30))  # Thursday, 2.5 hours after a Wednesday 22:00 slot
    sched._tick()
    equal(sched.banners, [(key, datetime(2026, 10, 14, 22, 0))], 'catch-up banner for yesterday\'s occurrence')
    equal(store.get_catchup_count(key), 1, 'the catch-up count is kept under yesterday\'s key')
    at(datetime(2026, 10, 15, 0, 31))
    sched._tick()
    equal(len(sched.banners), 1, 'answered once, not shown again a minute later')
    check(key in sched.cancelled, 'the answer survives the purge while the window is open')
    at(datetime(2026, 10, 15, 6, 1))  # past midnight plus the six hour window
    sched._tick()
    check(key not in sched.cancelled, 'the key is purged once the window has closed')
    equal(len(sched.banners), 1, 'and the occurrence is not offered again')

    # The Coming up publisher agrees: at 00:30 yesterday's overdue slot is next.
    sched = make_scheduler([('cancel', 5)])
    at(datetime(2026, 10, 15, 0, 30))
    slot = sched._reload_slots()[0]
    due, found_key = sched._next_occurrence(slot, datetime(2026, 10, 15, 0, 30))
    equal((due, found_key), (datetime(2026, 10, 14, 22, 0), key), 'the publisher offers yesterday\'s occurrence')
    equal(scheduler.when_label(due, datetime(2026, 10, 15, 0, 30)), 'Yesterday 22:00', 'and words it as yesterday')
    _Addon.settings['catchup_hours'] = 0
    sched = make_scheduler([('cancel', 5)])
    at(datetime(2026, 10, 15, 0, 30))
    sched._tick()
    equal(sched.banners, [], 'with the window off there is no catch-up')
    _Addon.settings['catchup_hours'] = 6


def test_stop_and_requeue_keeps_queue():
    """<summary>The stop Engage issues for Stop and Resume After does not fire the queue.</summary>"""
    sched = make_scheduler([('cancel', 5)])
    slot_a = scheduler.Slot(dict(store.new_slot(favourite='A'), id=1, label='A'))
    slot_b = scheduler.Slot(dict(store.new_slot(favourite='B'), id=2, label='B'))
    sched.queued_next.append((slot_a, 'kA'))
    sched._capture_now_playing = lambda: {'time': 10, 'file': '/f.mkv', 'describe': 'f'}
    PLAYING[0] = True
    _Player.on_stop = sched._on_playback_ended
    try:
        sched._stop_and_requeue(slot_b, 'kB')
    finally:
        _Player.on_stop = None
    equal(sched.played, ['kB'], 'only the chosen slot plays now')
    equal([k for _, k in sched.queued_next], ['kA'], 'the queued slot keeps its place')
    equal(sched._stopping_on_purpose, False, 'the flag is lowered again')
    sched._on_playback_ended()
    equal(sched.played, ['kB', 'kA'], 'a real end of playback fires the queue as before')
    PLAYING[0] = False


def test_warning_zero_countdown():
    """<summary>A warning 0 slot's banner counts down from now so its timeout action can fire.</summary>"""
    save_slot(warning=0, hour=19, minute=0)
    sched = make_scheduler([('cancel', 5)])
    now = datetime(2026, 10, 14, 19, 0, 10)
    at(now)
    sched._tick()
    equal(sched.banners, [('2026-10-14-1-1900', now + timedelta(seconds=scheduler.LATE_BANNER_SECONDS))],
          'the banner is given a short countdown rather than one already at zero')
    due = datetime(2026, 10, 14, 19, 0)
    check(prompt.allow_autofire(due, due - timedelta(minutes=5)), 'autofire allowed ahead of time')
    check(prompt.allow_autofire(due, due + timedelta(seconds=prompt.AUTOFIRE_GRACE_SECONDS - 1)),
          'autofire allowed inside the grace')
    check(not prompt.allow_autofire(due, due + timedelta(seconds=prompt.AUTOFIRE_GRACE_SECONDS + 1)),
          'a catch-up banner beyond the grace waits for the user')


def test_list_pick_kept_while_snoozed():
    """<summary>A random list pick survives midnight while its occurrence is snoozed.</summary>"""
    _Addon.settings['catchup_hours'] = 0  # so nothing from yesterday is kept by date alone
    sched = make_scheduler([('cancel', 5)])
    key = '2026-10-14-1-2330'
    sched._list_picks[key] = {'label': 'Film'}
    sched.snoozed[key] = datetime(2026, 10, 15, 0, 26)
    sched.warned[key] = True
    sched._purge_trackers(datetime(2026, 10, 15, 0, 0, 30))
    check(key in sched._list_picks, 'the pick is kept while the snooze runs')
    check(key in sched.snoozed, 'and so is the snooze')
    check(key not in sched.warned, 'while yesterday\'s other state goes')
    del sched.snoozed[key]
    sched._purge_trackers(datetime(2026, 10, 15, 0, 1))
    check(key not in sched._list_picks, 'and dropped once the snooze is gone and its day is over')
    _Addon.settings['catchup_hours'] = 6


def test_banner_style_resolution():
    """
    <summary>
    A slot's banner style wins over the master, a slot on 'default' follows the
    master as it is when the banner opens, and every unreadable value falls back
    to the standard banner.
    </summary>
    """
    # The master setting, as Kodi stores an enum: the index as a string.
    equal(store.parse_banner_style_setting('0'), 'standard', 'enum index 0 is standard')
    equal(store.parse_banner_style_setting('1'), 'thin', 'enum index 1 is thin')
    equal(store.parse_banner_style_setting(''), 'standard', 'an unset master is standard')
    equal(store.parse_banner_style_setting('Thin'), 'thin', 'a style name is accepted too')
    equal(store.parse_banner_style_setting('2'), 'uberthin', 'enum index 2 is uber thin')
    equal(store.parse_banner_style_setting('3'), 'uberthin_nobuttons', 'enum index 3 is uber thin, no buttons')
    equal(store.parse_banner_style_setting('4'), 'fullmonty', 'enum index 4 is the Full Monty')
    equal(store.parse_banner_style_setting('7'), 'standard', 'an index off the end is standard')
    equal(store.parse_banner_style_setting(None), 'standard', 'None is standard')
    equal(store.BANNER_STYLE_SETTING_ORDER, ['standard', 'thin', 'uberthin', 'uberthin_nobuttons',
                                             'fullmonty'],
          'the enum order the settings.xml comment promises')
    with open(os.path.join(ADDON_DIR, 'resources', 'settings.xml'), encoding='utf-8') as f:
        check('values="Standard|Thin|Uber thin|Uber thin, no buttons|Full Monty"' in f.read(),
              'settings.xml lists the styles in BANNER_STYLE_SETTING_ORDER')

    # The slot's own choice.
    equal(store.banner_style_of({}), 'default', 'a slot saved before the setting follows the master')
    equal(store.banner_style_of({'banner_style': 'thin'}), 'thin', 'thin stored reads as thin')
    equal(store.banner_style_of({'banner_style': 'Standard'}), 'standard', 'case does not matter')
    equal(store.banner_style_of({'banner_style': 'bogus'}), 'default', 'an unknown choice follows the master')
    equal(store.banner_style_of({'banner_style': 'uberthin_nobuttons'}), 'uberthin_nobuttons',
          'the buttonless style is a slot choice')
    for style in store.SLOT_BANNER_STYLES:
        check(style in store.BANNER_STYLE_LABELS and style in store.BANNER_STYLE_SHORT,
              '{} has a label and a short form'.format(style))
    equal(store.new_slot()['banner_style'], 'default', 'a new slot follows the master')

    # Resolution: the slot wins, else the master, else standard.
    equal(store.resolve_banner_style('default', 'thin'), 'thin', 'default takes the master')
    equal(store.resolve_banner_style('default', 'standard'), 'standard', 'default takes the master either way')
    equal(store.resolve_banner_style('standard', 'thin'), 'standard', 'the slot overrides a thin master')
    equal(store.resolve_banner_style('thin', 'standard'), 'thin', 'the slot overrides a standard master')
    equal(store.resolve_banner_style('bogus', 'bogus'), 'standard', 'nothing readable gives standard')
    equal(store.resolve_banner_style('default', 'uberthin'), 'uberthin', 'default takes an uber thin master')
    equal(store.resolve_banner_style('uberthin_nobuttons', 'thin'), 'uberthin_nobuttons',
          'the slot overrides with the buttonless style')

    # The master is read from the add-on settings.
    _Addon.settings['banner_style'] = '1'
    equal(store.master_banner_style(), 'thin', 'the master is read from the setting')
    _Addon.settings.pop('banner_style')
    equal(store.master_banner_style(), 'standard', 'a missing setting reads as standard')

    # Each style maps to its own window XML, and both files ship.
    equal(prompt.banner_xml_for('thin'), 'EngagePromptThin.xml', 'thin has its own window')
    equal(prompt.banner_xml_for('standard'), 'EngagePrompt.xml', 'standard keeps the original window')
    equal(prompt.banner_xml_for('nonsense'), 'EngagePrompt.xml', 'an unknown style gets the original')
    equal(prompt.banner_xml_for('uberthin'), 'EngagePromptUberThin.xml', 'uber thin has its own window')
    equal(prompt.banner_xml_for('uberthin_nobuttons'), 'EngagePromptUberThinNoButtons.xml',
          'the buttonless uber thin has its own window')
    for style in store.BANNER_STYLES:
        check(style in prompt.BANNER_XML, '{} has a window'.format(style))
    equal(prompt.banner_xml_for('fullmonty'), 'EngagePromptFullMonty.xml', 'the Full Monty has its own window')

    # The Full Monty's lines, from what the library returns for each kind.
    film = prompt.full_monty_details('movie', {
        'plot': 'Spice.', 'year': 2021, 'runtime': 9300, 'genre': ['Science Fiction', 'Adventure', 'Drama', 'War'],
        'rating': 8.04, 'mpaa': '12A', 'tagline': 'Beyond fear.', 'art': {'fanart': 'f.jpg', 'poster': 'p.jpg'}})
    equal(film['infoline'], '2021 \u00b7 2h 35m \u00b7 Science Fiction, Adventure, Drama \u00b7 8.0 \u00b7 12A',
          'film line: year, running time, three genres, rating, certificate')
    equal((film['plot'], film['fanart'], film['tagline']), ('Spice.', 'f.jpg', 'Beyond fear.'), 'film extras')
    episode = prompt.full_monty_details('episode', {
        'plot': 'Neelix.', 'runtime': 2700, 'firstaired': '1997-01-08', 'rating': 0,
        'art': {'tvshow.fanart': 'show.jpg'}})
    equal(episode['infoline'], 'First aired 08/01/1997 \u00b7 45m', 'episode line: en-GB date, running time')
    equal(episode['fanart'], 'show.jpg', "an episode takes its show's fanart")
    equal(prompt.full_monty_details('movie', None),
          {'plot': '', 'fanart': '', 'infoline': '', 'tagline': ''}, 'nothing known, nothing shown')
    equal(prompt._runtime_text('x'), '', 'a nonsense running time is left out')

    # A buttonless banner keeps its layout while it can fire on its own, and
    # becomes the buttoned uber thin one for an overdue catch-up, which waits
    # for the user and so needs something to press.
    due = datetime(2026, 10, 20, 20, 0)
    equal(prompt.opening_style('uberthin_nobuttons', due, due - timedelta(minutes=5)),
          'uberthin_nobuttons', 'a buttonless banner opening ahead of time stays buttonless')
    equal(prompt.opening_style('uberthin_nobuttons', due, due + timedelta(seconds=60)),
          'uberthin_nobuttons', 'inside the grace it still fires, so stays buttonless')
    equal(prompt.opening_style('uberthin_nobuttons', due, due + timedelta(hours=2)),
          'uberthin', 'an overdue catch-up gets the buttons back')
    equal(prompt.opening_style('thin', due, due + timedelta(hours=2)),
          'thin', 'a buttoned style is never swapped')

    with open(os.path.join(ADDON_DIR, 'resources', 'skins', 'Default', '720p',
                           'EngagePromptUberThinNoButtons.xml'), encoding='utf-8') as f:
        check('type="button"' not in f.read(), 'the buttonless window has no buttons')

    # The countdown says queuing only when the countdown will queue behind
    # something playing; with nothing playing a queued slot starts at once.
    equal(prompt.countdown_text(297, 'queue', True), 'Queuing in 4m 57s', 'queue while playing says queuing')
    equal(prompt.countdown_text(5, 'queue', True), 'Queuing in 5s', 'queue under a minute')
    equal(prompt.countdown_text(0, 'queue', True), 'Queuing now', 'queue at zero')
    equal(prompt.countdown_text(297, 'queue', False), 'Starts in 4m 57s', 'queue with nothing playing starts')
    equal(prompt.countdown_text(297, 'open', True), 'Starts in 4m 57s', 'start now says starts')
    equal(prompt.countdown_text(0, 'open', False), 'Starting now', 'start now at zero')
    equal(prompt.countdown_text(-122, 'queue', True), 'Overdue by 2m 02s', 'overdue reads the same for every action')

    for name in prompt.BANNER_XML.values():
        check(os.path.isfile(os.path.join(ADDON_DIR, 'resources', 'skins', 'Default', '720p', name)),
              '{} exists'.format(name))

    # The scheduler resolves at popup time: the same stored slot opens a thin
    # banner once the master changes, and a slot with its own choice does not.
    opened = []

    given = []

    def fake_prompt(addon_path, show_name, episode_label, scheduled_dt, thumb='',
                    timeout_action='queue', banner_style='standard', details=None):
        """<summary>Record the style the banner was asked to open with, and its details.</summary>"""
        opened.append(banner_style)
        given.append(details)
        return ('cancel', 5)

    real_prompt = prompt.show_engage_prompt
    prompt.show_engage_prompt = fake_prompt
    try:
        sched = scheduler.EngageScheduler()
        sched._describe_slot = lambda slot, key=None: ('', '')
        when = datetime(2026, 10, 14, 19, 0)
        at(when)
        following = scheduler.Slot(dict(store.new_slot(favourite='A'), id=1, label='A'))
        own_choice = scheduler.Slot(dict(store.new_slot(favourite='B'), id=2, label='B',
                                         banner_style='standard'))
        _Addon.settings['banner_style'] = '0'
        sched._handle_warning(following, when)
        _Addon.settings['banner_style'] = '1'
        sched._handle_warning(following, when)
        sched._handle_warning(own_choice, when)
        equal(opened, ['standard', 'thin', 'standard'],
              'the master applies at popup time and a slot\'s own choice wins')
        equal(given, [None, None, None], 'no details are looked up for the other layouts')

        # The Full Monty asks for the item's details, and only it does.
        looked = []
        sched._banner_details = lambda slot, key=None: looked.append(slot.id) or {'plot': 'P'}
        monty = scheduler.Slot(dict(store.new_slot(favourite='C'), id=3, label='C',
                                    banner_style='fullmonty'))
        sched._handle_warning(monty, when)
        sched._handle_warning(own_choice, when)
        equal((opened[-2:], given[-2:], looked), (['fullmonty', 'standard'], [{'plot': 'P'}, None], [3]),
              'the Full Monty gets its details; a standard banner straight after does not')
    finally:
        prompt.show_engage_prompt = real_prompt
        _Addon.settings.pop('banner_style', None)


def main():
    """<summary>Run every test and exit non zero if any check failed.</summary>"""
    for test in (test_autoopen_no_banner_no_double_play, test_snooze_across_midnight,
                 test_catchup_across_midnight, test_stop_and_requeue_keeps_queue,
                 test_warning_zero_countdown, test_list_pick_kept_while_snoozed,
                 test_banner_style_resolution):
        print('running {}'.format(test.__name__))
        try:
            test()
        finally:
            scheduler.datetime = datetime
    if FAILURES:
        print('{} check(s) failed'.format(len(FAILURES)))
        return 1
    print('test_process_slot: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
