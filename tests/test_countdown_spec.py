#!/usr/bin/env python3
"""
<summary>
Spec tests for the Coming up countdown the scheduler publishes: the pure helper
countdown_values, and what _publish_next puts on the Home window.
</summary>
<remarks>
Written from the brief, not from the code. The Kodi stubs and helpers are those
of tests/test_next_slot.py, which installs them at import. Exits non zero when
a check fails.

Usage:
    python3 tests/test_countdown_spec.py
</remarks>
"""

import os
import re
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_next_slot as T  # noqa: E402  (installs the Kodi stubs, imports the scheduler)

scheduler = T.scheduler
FAILURES = []
NOW = datetime(2026, 11, 12, 18, 0)  # a Thursday


def equal(got, want, what):
    """
    <summary>Record a failure when got differs from want.</summary>
    <param name="got">The value produced.</param>
    <param name="want">The value expected.</param>
    <param name="what">The requirement being checked.</param>
    """
    if got != want:
        FAILURES.append('{}: got {!r}, want {!r}'.format(what, got, want))
        print('FAIL: {}'.format(FAILURES[-1]))


def ahead(minutes, seconds=0):
    """
    <summary>A due time the given distance after NOW.</summary>
    <param name="minutes">Whole minutes ahead (negative for overdue).</param>
    <param name="seconds">Extra seconds ahead.</param>
    <returns>A datetime.</returns>
    """
    return NOW + timedelta(minutes=minutes, seconds=seconds)


def test_helper_off_when_window_zero_or_less():
    """<summary>A window of 0 or less means off: ('', '').</summary>"""
    equal(scheduler.countdown_values(ahead(10), NOW, 0), ('', ''), 'window 0 is off')
    equal(scheduler.countdown_values(ahead(10), NOW, -5), ('', ''), 'a negative window is off')


def test_helper_outside_window():
    """<summary>Further away than the window, due now and overdue all give ('', '').</summary>"""
    equal(scheduler.countdown_values(ahead(31), NOW, 30), ('', ''), 'before the window opens')
    equal(scheduler.countdown_values(ahead(600), NOW, 30), ('', ''), 'hours away')
    equal(scheduler.countdown_values(ahead(0), NOW, 30), ('', ''), 'exactly due')
    equal(scheduler.countdown_values(ahead(-1), NOW, 30), ('', ''), 'a minute overdue')
    equal(scheduler.countdown_values(ahead(-90), NOW, 30), ('', ''), 'long overdue')


def test_helper_words_and_percent():
    """<summary>Words 'Starts in N min' and percent from 100 down towards 0.</summary>"""
    equal(scheduler.countdown_values(ahead(30), NOW, 30), ('Starts in 30 min', '100'), 'start of the window is 100')
    equal(scheduler.countdown_values(ahead(15), NOW, 30), ('Starts in 15 min', '50'), 'half way is 50')
    equal(scheduler.countdown_values(ahead(6), NOW, 30), ('Starts in 6 min', '20'), 'a fifth left is 20')
    equal(scheduler.countdown_values(ahead(12), NOW, 12), ('Starts in 12 min', '100'), 'another window size')
    equal(scheduler.countdown_values(ahead(6), NOW, 12), ('Starts in 6 min', '50'), 'half of a 12 minute window')


def test_helper_minutes_round_up():
    """<summary>Minutes round up, so it never says sooner than it is.</summary>"""
    words, _ = scheduler.countdown_values(ahead(14, 1), NOW, 30)
    equal(words, 'Starts in 15 min', 'one second over 14 minutes is 15')
    words, _ = scheduler.countdown_values(ahead(1, 30), NOW, 30)
    equal(words, 'Starts in 2 min', 'a minute and a half is 2')


def test_helper_last_minute():
    """<summary>In the last minute the words are 'Starting now'.</summary>"""
    for seconds in (59, 30, 1):
        words, percent = scheduler.countdown_values(ahead(0, seconds), NOW, 30)
        equal(words, 'Starting now', 'last minute at {} seconds'.format(seconds))
        check_percent = bool(re.fullmatch(r'\d+', percent)) and 0 <= int(percent) <= 100
        equal(check_percent, True, 'percent is a whole number string at {} seconds'.format(seconds))


def test_helper_returns_strings_and_whole_percent():
    """<summary>Both values are strings and the percent is a whole number from 100 to 0.</summary>"""
    last = 101
    for second in range(0, 30 * 60, 7):
        words, percent = scheduler.countdown_values(ahead(30) - timedelta(seconds=second), NOW, 30)
        equal((isinstance(words, str), isinstance(percent, str)), (True, True), 'strings at {}s'.format(second))
        ok = bool(re.fullmatch(r'\d+', percent))
        equal(ok, True, 'whole percent at {}s'.format(second))
        if ok:
            value = int(percent)
            equal(0 <= value <= 100 and value <= last, True, 'percent only falls and stays in range at {}s'.format(second))
            last = value


def test_settings_defaults_declared():
    """<summary>The switch is off by default and the window defaults to 30 minutes.</summary>"""
    path = os.path.join(os.path.dirname(HERE), 'service.engage', 'resources', 'settings.xml')
    with open(path, encoding='utf-8') as handle:
        text = handle.read()
    switch = re.search(r'<setting id="next_countdown"[^>]*>', text)
    minutes = re.search(r'<setting id="next_countdown_minutes"[^>]*>', text)
    equal(bool(switch and 'type="bool"' in switch.group(0) and 'default="false"' in switch.group(0)), True,
          'next_countdown is a bool, off by default')
    equal(bool(minutes and 'default="30"' in minutes.group(0)), True, 'next_countdown_minutes defaults to 30')


def _publish_at(sched, when):
    """
    <summary>Publish with the scheduler's clock pinned to when.</summary>
    <param name="sched">The scheduler.</param>
    <param name="when">The moment to treat as now.</param>
    """
    scheduler.datetime = T._FixedDatetime.at(when)
    try:
        sched._publish_next('countdown spec')
    finally:
        scheduler.datetime = datetime


def _countdown_props():
    """<summary>The published (Countdown, CountdownPercent) pair, '' where not set.</summary>"""
    got = T.props()
    return (got.get('Countdown') or '', got.get('CountdownPercent') or '')


def _row_countdowns():
    """<summary>Any numbered row property carrying a countdown.</summary>"""
    return [k for k in T.props() if k[:1].isdigit() and 'Countdown' in k]


def test_publish():
    """<summary>The Home window gets the countdown for the next slot only, and only when on and in window.</summary>"""
    original_bool = T._Addon.getSettingBool
    # The stub's getSettingBool would read the string 'false' as true, so make it honour the strings the tests set.
    T._Addon.getSettingBool = lambda self, key: str(self.settings[key]).lower() in ('true', '1')
    try:
        T.reset_window()
        T.store.save_slots([T.make_slot(1, 'Voyager', ['Thursday'], 19),
                            T.make_slot(2, 'TNG', ['Thursday'], 19, 30),
                            T.make_slot(3, 'Film', ['Friday'], 20)])
        sched = T.make_scheduler({})
        at_1845 = datetime(2026, 11, 12, 18, 45)
        T._Addon.settings['next_countdown_minutes'] = 30

        T._Addon.settings['next_countdown'] = 'false'
        _publish_at(sched, at_1845)
        equal(_countdown_props(), ('', ''), 'switch off: nothing set')

        T._Addon.settings['next_countdown'] = 'true'
        _publish_at(sched, at_1845)
        equal(_countdown_props(), ('Starts in 15 min', '50'), 'on, inside window: next slot counts down')
        equal(T.props().get('Label'), 'Voyager', 'the countdown belongs to the slot that plays next')
        equal(_row_countdowns(), [], 'numbered rows never carry a countdown')
        equal([T.props().get('{}.Label'.format(n)) for n in (1, 2, 3)], ['Voyager', 'TNG', 'Film'],
              'rows are still published')

        _publish_at(sched, datetime(2026, 11, 12, 17, 0))
        equal(_countdown_props(), ('', ''), 'outside the window: cleared')
        _publish_at(sched, datetime(2026, 11, 12, 18, 30))
        equal(_countdown_props(), ('Starts in 30 min', '100'), 'window opens at 100')
        _publish_at(sched, datetime(2026, 11, 12, 18, 59, 20))
        equal(_countdown_props()[0], 'Starting now', 'last minute reads Starting now')
        _publish_at(sched, datetime(2026, 11, 12, 19, 0))
        equal(_countdown_props(), ('', ''), 'due: cleared')
        _publish_at(sched, datetime(2026, 11, 12, 19, 2))
        equal(_countdown_props(), ('', ''), 'overdue: cleared')

        T._Addon.settings['next_countdown_minutes'] = 10
        _publish_at(sched, at_1845)
        equal(_countdown_props(), ('', ''), 'a 10 minute window does not yet reach 15 minutes out')
        _publish_at(sched, datetime(2026, 11, 12, 18, 50))
        equal(_countdown_props(), ('Starts in 10 min', '100'), 'a 10 minute window opens full')

        T._Addon.settings['next_countdown_minutes'] = 30
        T._Addon.settings['next_countdown'] = 'false'
        _publish_at(sched, at_1845)
        equal(_countdown_props(), ('', ''), 'switching off clears it')
    finally:
        T._Addon.getSettingBool = original_bool
        T._Addon.settings.pop('next_countdown', None)
        T._Addon.settings.pop('next_countdown_minutes', None)
        scheduler.datetime = datetime


def main():
    """<summary>Run every test and exit non zero when any check failed.</summary>"""
    for test in (test_helper_off_when_window_zero_or_less, test_helper_outside_window,
                 test_helper_words_and_percent, test_helper_minutes_round_up, test_helper_last_minute,
                 test_helper_returns_strings_and_whole_percent, test_settings_defaults_declared,
                 test_publish):
        print('running {}'.format(test.__name__))
        test()
    if FAILURES:
        print('{} check(s) failed'.format(len(FAILURES)))
        return 1
    print('test_countdown_spec: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
