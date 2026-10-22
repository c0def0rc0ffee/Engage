#!/usr/bin/env python3
"""
<summary>
Unit test for the Manage slots window's ordering (SLOTS-WINDOW, 22/10/2026):
next up, A to Z, time of day and type, the tie breaks, where disabled slots
go, and the second line of each row.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero on the first
failure. No Kodi is needed: the xbmc modules are stubbed in sys.modules before
the add-on is imported, as in test_next_slot.py, and the window class itself is
not drawn; only the pure functions it uses are checked.

Usage:
    python3 tests/test_slots_window.py
</remarks>
"""

import os
import sys
import tempfile
import types
from datetime import datetime

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

from resources.lib import slots as store  # noqa: E402
from resources.lib import slots_window  # noqa: E402

# --- Helpers ------------------------------------------------------------------

FAILURES = []


def equal(actual, expected, what):
    """
    <summary>Record a failure when actual is not expected; the run carries on.</summary>
    <param name="actual">The value produced.</param>
    <param name="expected">The value wanted.</param>
    <param name="what">What is being checked, for the report.</param>
    """
    if actual != expected:
        FAILURES.append(what)
        print('FAIL: {}: got {!r}, expected {!r}'.format(what, actual, expected))


def slot(slot_id, label, days, hour, minute=0, enabled=True, kind='favourite', **extra):
    """
    <summary>A stored slot dict with the fields the window reads.</summary>
    <returns>The dict.</returns>
    """
    data = store.new_slot(favourite=label, day=days[0])
    data.update({'id': slot_id, 'label': label, 'days': days, 'hour': hour, 'minute': minute,
                 'enabled': enabled, 'kind': kind})
    data.update(extra)
    return data


SLOTS = [
    slot(1, 'voyager', ['Saturday'], 19),
    slot(2, 'Dune', ['Thursday'], 21, 30),
    slot(3, 'Breakfast', ['Friday'], 8),
    slot(4, 'Archive', ['Thursday'], 20, enabled=False),
    slot(5, 'Films', ['Thursday'], 22, kind='list', list_name='Films', list_mode='random'),
    slot(6, 'Binge', ['Sunday'], 19, kind='sequence', seq_type='binge',
         items=[{'type': 'episode', 'id': 1}, {'type': 'episode', 'id': 2}]),
]
NOW = datetime(2026, 10, 22, 18, 0)  # a Thursday


def names(rows):
    """<summary>The labels of sort_slots rows, in order.</summary>"""
    return [row[0]['label'] for row in rows]


# --- Tests --------------------------------------------------------------------

def test_next_up():
    """<summary>Soonest first, disabled last, each with its due time.</summary>"""
    rows = slots_window.sort_slots(SLOTS, 'next', NOW)
    equal(names(rows), ['Dune', 'Films', 'Breakfast', 'voyager', 'Binge', 'Archive'],
          'next up: tonight, then tomorrow, the weekend, and the disabled slot last')
    equal(rows[0][1], datetime(2026, 10, 22, 21, 30), 'the due time comes with each row')


def test_other_orders():
    """<summary>A to Z ignores case, time of day and type break ties by name.</summary>"""
    equal(names(slots_window.sort_slots(SLOTS, 'az', NOW)),
          ['Archive', 'Binge', 'Breakfast', 'Dune', 'Films', 'voyager'], 'A to Z without case')
    equal(names(slots_window.sort_slots(SLOTS, 'time', NOW)),
          ['Breakfast', 'Binge', 'voyager', 'Archive', 'Dune', 'Films'], 'time of day, ties by name')
    equal(names(slots_window.sort_slots(SLOTS, 'type', NOW)),
          ['Archive', 'Breakfast', 'Dune', 'voyager', 'Binge', 'Films'],
          'favourites, then sequences, then lists')
    equal(names(slots_window.sort_slots(SLOTS, 'nonsense', NOW)),
          names(slots_window.sort_slots(SLOTS, 'next', NOW)), 'an unknown sort is next up')
    equal(slots_window.sort_label('az'), 'A to Z', 'the button words')
    equal(slots_window.sort_label('nonsense'), 'Next up', 'unknown words fall back')


def test_kind_text():
    """<summary>The second line says what each kind of slot plays.</summary>"""
    equal(slots_window.kind_text(SLOTS[0]), 'Favourite: voyager', 'a favourite')
    equal(slots_window.kind_text(SLOTS[5]), 'Binge order, 2 episodes', 'a binge order')
    equal(slots_window.kind_text(dict(SLOTS[5], seq_type='movie', items=[{'type': 'movie', 'id': 1}])),
          'Film sequence, 1 film', 'a one film sequence')
    check_list = slots_window.kind_text(SLOTS[4])
    equal(check_list.startswith('List: Films, random'), True, 'a list slot: ' + check_list)


def main():
    """<summary>Run every test and report.</summary><returns>The exit code.</returns>"""
    for test in (test_next_up, test_other_orders, test_kind_text):
        print('running {}'.format(test.__name__))
        test()
    if FAILURES:
        print('{} check(s) failed'.format(len(FAILURES)))
        return 1
    print('test_slots_window: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
