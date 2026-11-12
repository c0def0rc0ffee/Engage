#!/usr/bin/env python3
"""
<summary>
Unit test for the Full Monty over playback step down: with the add-on setting
fullmonty_over_playback_uberthin on and Kodi already playing, a banner whose
resolved style is the Full Monty opens as Uber thin instead, and in every other
case the style is unchanged.
</summary>
<remarks>
Run on its own; exits non zero on any failure. No Kodi is needed: the xbmc
modules are stubbed in sys.modules before the add-on is imported, the prompt is
replaced by a recorder, and playback state is a flag the tests set. Written from
the feature brief, in the style of tests/test_process_slot.py.

Usage:
    python3 tests/test_banner_over_playback.py
</remarks>
"""

import json
import os
import sys
import tempfile
import types
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, 'service.engage')
DATA_DIR = tempfile.mkdtemp(prefix='engage-overplay-test-')

PLAYING = [False]
SETTING = 'fullmonty_over_playback_uberthin'


class _Window:
    """<summary>Stand-in for xbmcgui.Window with one shared property dict.</summary>"""
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
    """<summary>Stand-in for xbmcaddon.Addon backed by a settings dict.</summary>"""
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
    """<summary>Stand-in for xbmc.Player whose playing state the tests set.</summary>"""

    def isPlaying(self):
        return PLAYING[0]

    def stop(self):
        PLAYING[0] = False

    def play(self, path):
        PLAYING[0] = True


class _Dialog:
    """<summary>Stand-in for xbmcgui.Dialog that ignores notifications.</summary>"""

    def notification(self, *args):
        pass


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


def test_pure_step_down_truth_table():
    """<summary>Only fullmonty with playing and the switch on becomes uberthin; all else is unchanged.</summary>"""
    for style in ('standard', 'thin', 'uberthin', 'uberthin_nobuttons', 'fullmonty'):
        for playing in (False, True):
            for step_down in (False, True):
                want = 'uberthin' if (style == 'fullmonty' and playing and step_down) else style
                equal(store.banner_style_over_playback(style, playing, step_down), want,
                      'style={} playing={} switch={}'.format(style, playing, step_down))


def test_setting_off_by_default_and_readable():
    """<summary>The setting reads False when unset and follows the stored value both ways.</summary>"""
    _Addon.settings.pop(SETTING, None)
    equal(store.fullmonty_over_playback_setting(), False, 'off by default when unset')
    _Addon.settings[SETTING] = True
    equal(store.fullmonty_over_playback_setting(), True, 'on when switched on')
    _Addon.settings[SETTING] = False
    equal(store.fullmonty_over_playback_setting(), False, 'off when switched off')
    _Addon.settings.pop(SETTING, None)


def test_settings_xml_declares_setting():
    """<summary>settings.xml declares the setting as a boolean defaulting to false.</summary>"""
    with open(os.path.join(ADDON_DIR, 'resources', 'settings.xml'), encoding='utf-8') as f:
        text = f.read()
    check('id="{}"'.format(SETTING) in text, 'the setting id is declared')
    idx = text.find('id="{}"'.format(SETTING))
    tag = text[text.rfind('<', 0, idx):text.find('>', idx) + 1]
    check('type="bool"' in tag, 'it is a boolean')
    check('default="false"' in tag, 'it defaults to false')


def run_banners(setting, playing, slot_style, master='0'):
    """
    <summary>Open one banner through the scheduler and report what it asked for.</summary>
    <param name="setting">Value of the over playback setting, or None to leave it unset.</param>
    <param name="playing">Whether Kodi is playing.</param>
    <param name="slot_style">The slot's own banner_style.</param>
    <param name="master">The master banner_style setting as Kodi stores it.</param>
    <returns>(style passed to the prompt, details passed, number of details lookups).</returns>
    """
    seen = {}
    looked = []

    def fake_prompt(addon_path, show_name, episode_label, scheduled_dt, thumb='',
                    timeout_action='queue', banner_style='standard', details=None):
        """<summary>Record the style and details the banner opened with.</summary>"""
        seen['style'] = banner_style
        seen['details'] = details
        return ('cancel', 5)

    real = prompt.show_engage_prompt
    prompt.show_engage_prompt = fake_prompt
    try:
        _Addon.settings['banner_style'] = master
        if setting is None:
            _Addon.settings.pop(SETTING, None)
        else:
            _Addon.settings[SETTING] = setting
        PLAYING[0] = playing
        sched = scheduler.EngageScheduler()
        sched._describe_slot = lambda slot, key=None: ('', '')
        sched._banner_details = lambda slot, key=None: looked.append(slot.id) or {'plot': 'P'}
        slot = scheduler.Slot(dict(store.new_slot(favourite='A'), id=1, label='A', banner_style=slot_style))
        sched._handle_warning(slot, datetime(2026, 10, 14, 19, 0))
    finally:
        prompt.show_engage_prompt = real
        _Addon.settings.pop('banner_style', None)
        _Addon.settings.pop(SETTING, None)
        PLAYING[0] = False
    return seen.get('style'), seen.get('details'), len(looked)


def test_fullmonty_over_playback_steps_down():
    """<summary>Full Monty slot, setting on, playing: opens uberthin with no details lookup.</summary>"""
    style, details, lookups = run_banners(True, True, 'fullmonty')
    equal(style, 'uberthin', 'stepped down to uber thin')
    equal(lookups, 0, 'no details lookup for a stepped down banner')
    equal(details, None, 'no details handed to the banner')


def test_master_fullmonty_via_default_steps_down():
    """<summary>A slot on default following a Full Monty master also steps down.</summary>"""
    style, _, lookups = run_banners(True, True, 'default', master='4')
    equal(style, 'uberthin', 'default slot on a Full Monty master steps down')
    equal(lookups, 0, 'and skips the lookup')


def test_setting_off_unchanged():
    """<summary>With the setting off or unset the Full Monty stays, and its details are fetched.</summary>"""
    for value in (False, None):
        style, details, lookups = run_banners(value, True, 'fullmonty')
        equal(style, 'fullmonty', 'setting {!r}: Full Monty kept over playback'.format(value))
        equal(lookups, 1, 'setting {!r}: details looked up'.format(value))
        equal(details, {'plot': 'P'}, 'setting {!r}: details passed on'.format(value))


def test_nothing_playing_unchanged():
    """<summary>With nothing playing the Full Monty stays even with the setting on.</summary>"""
    style, _, lookups = run_banners(True, False, 'fullmonty')
    equal(style, 'fullmonty', 'Full Monty kept when idle')
    equal(lookups, 1, 'details looked up when idle')


def test_other_styles_unchanged():
    """<summary>Other styles are not altered over playback with the setting on, and never fetch details.</summary>"""
    for slot_style in ('standard', 'thin', 'uberthin', 'uberthin_nobuttons'):
        style, _, lookups = run_banners(True, True, slot_style, master='4')
        equal(style, slot_style, '{} slot keeps its style over a Full Monty master'.format(slot_style))
        equal(lookups, 0, '{} slot does no details lookup'.format(slot_style))
    for master, want in (('0', 'standard'), ('1', 'thin'), ('2', 'uberthin'), ('3', 'uberthin_nobuttons')):
        style, _, lookups = run_banners(True, True, 'default', master=master)
        equal(style, want, 'default slot on master {} stays {}'.format(master, want))
        equal(lookups, 0, 'no lookup for master {}'.format(master))


def test_resolved_at_popup_time():
    """<summary>Flipping the setting or playback between banners takes effect on the next one.</summary>"""
    results = [run_banners(*case)[0] for case in
               ((False, True, 'fullmonty'), (True, True, 'fullmonty'), (True, False, 'fullmonty'),
                (True, True, 'fullmonty'), (False, True, 'fullmonty'))]
    equal(results, ['fullmonty', 'uberthin', 'fullmonty', 'uberthin', 'fullmonty'],
          'each banner reflects the setting and playback at its own popup time')

    # The same scheduler instance, with no restart between banners.
    opened = []

    def fake_prompt(addon_path, show_name, episode_label, scheduled_dt, thumb='',
                    timeout_action='queue', banner_style='standard', details=None):
        """<summary>Record the style the banner opened with.</summary>"""
        opened.append(banner_style)
        return ('cancel', 5)

    real = prompt.show_engage_prompt
    prompt.show_engage_prompt = fake_prompt
    try:
        sched = scheduler.EngageScheduler()
        sched._describe_slot = lambda slot, key=None: ('', '')
        sched._banner_details = lambda slot, key=None: {'plot': 'P'}
        slot = scheduler.Slot(dict(store.new_slot(favourite='A'), id=1, label='A', banner_style='fullmonty'))
        when = datetime(2026, 10, 14, 19, 0)
        PLAYING[0] = True
        _Addon.settings[SETTING] = False
        sched._handle_warning(slot, when)
        _Addon.settings[SETTING] = True
        sched._handle_warning(slot, when)
        PLAYING[0] = False
        sched._handle_warning(slot, when)
        PLAYING[0] = True
        sched._handle_warning(slot, when)
        equal(opened, ['fullmonty', 'uberthin', 'fullmonty', 'uberthin'],
              'one scheduler, no restart, style follows live state')
    finally:
        prompt.show_engage_prompt = real
        _Addon.settings.pop(SETTING, None)
        PLAYING[0] = False


def main():
    """<summary>Run every test and exit non zero if any check failed.</summary>"""
    for test in (test_pure_step_down_truth_table, test_setting_off_by_default_and_readable,
                 test_settings_xml_declares_setting, test_fullmonty_over_playback_steps_down,
                 test_master_fullmonty_via_default_steps_down, test_setting_off_unchanged,
                 test_nothing_playing_unchanged, test_other_styles_unchanged,
                 test_resolved_at_popup_time):
        print('running {}'.format(test.__name__))
        test()
    if FAILURES:
        print('{} check(s) failed'.format(len(FAILURES)))
        return 1
    print('test_banner_over_playback: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
