"""
<summary>
The scheduler proper: decides when a slot is due and puts the prompt on
screen.
</summary>
<remarks>
Slots are stored as JSON by slots.py and wrapped by the Slot class here.
Two timings matter. STARTUP_REST_SECONDS holds the first prompt back until
Kodi has settled after login, because a dialog raised mid-startup is dismissed
by the skin before anyone sees it. SNOOZE_MINUTES is how long a declined
prompt stays quiet. A third, the recent watch window, is a user setting: a
slot whose show was played within that many hours is skipped for the day, so
watching early does not earn a reminder for what was just watched.

The scheduler also publishes the next slot due as Engage.Next.* properties on
Kodi's Home window (id 10000) so a skin can show a "Coming up" block, and the
next few occurrences across every slot as numbered Engage.Next.N.* properties
for a "Coming up" list. See _publish_next for the contract and the reasons
behind its write discipline.
</remarks>
"""
import json
import random
import re
import threading
import time
from datetime import datetime, timedelta

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import functional_lists
from resources.lib import slot_rules
from resources.lib.slots import SLOTS_SAVED_MESSAGE

ADDON_ID = 'service.engage'
DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
SNOOZE_MINUTES = 5
STARTUP_REST_SECONDS = 20  # let Kodi fully settle at login before the first prompt
# The countdown a banner gets when it opens at or after its scheduled time: a
# snooze running out, or a slot reached with no warning window (warning 0, or
# Kodi started right at the time). Long enough to answer, short enough that
# "play at the set time" still means that.
LATE_BANNER_SECONDS = 60
# Used only when the recent_watch_hours setting cannot be read. Matches the
# default declared in resources/settings.xml.
RECENT_WATCH_FALLBACK_HOURS = 4
# Used only when the catchup_hours setting cannot be read. Matches the default
# declared in resources/settings.xml and the fallback in _process_slot.
CATCHUP_FALLBACK_HOURS = 6

# The next slot is published for skins as properties on Kodi's Home window,
# Window(10000), named NEXT_PROPERTY_PREFIX + one of NEXT_PROPERTY_NAMES. The
# Functional skin reads Label, Episode and When and hides its block while
# Label is empty; the rest are extras for any skin that wants them. Label is
# deliberately last in the tuple: it is written last when a slot is published
# and cleared first when none is, so a skin keyed on Label never shows a new
# label over the previous slot's other lines.
HOME_WINDOW_ID = 10000
NEXT_PROPERTY_PREFIX = 'Engage.Next.'
NEXT_PROPERTY_NAMES = ('Episode', 'When', 'Day', 'Date', 'Time', 'Thumb',
                       'SlotId', 'State', 'Count', 'Label')
# Beside the single set, the next NEXT_LIST_COUNT occurrences across every
# enabled slot are published in due order as Engage.Next.<n>.Label and .When
# (n from 1), for a skin's "Coming up" list. Number 1 is the same occurrence
# as the single set. One slot can appear more than once, a weekly slot on two
# days this week for instance; that is wanted. A number with no occurrence has
# both properties cleared. The rows carry no episode line: the list shows the
# slot's title only, and only the single set keeps Episode for the block. A
# Functional list slot is the exception (13/10/2026): its row names the list
# when the slot picks at random, and the item it will play when it plays in
# order, a later showing of the same slot naming the item after that. See
# _row_label.
# Label is last here for the same reason as above: a row is written Label
# last and cleared Label first, rows are written from NEXT_LIST_COUNT down to
# 1 and cleared from 1 upwards, so a skin that gates its list on row 1 and
# each row on its own label never shows a half updated list. See
# next_property_order.
# COMING-UP-MORE, 21/10/2026: 22, up from 11, so Functional's 22 line list can
# fill when several slots share a day and each takes one line instead of two.
NEXT_LIST_COUNT = 22
# COMING-UP-RUNTIME, 22/10/2026: each row also carries Due, the occurrence as
# 'YYYY-MM-DDTHH:MM', and Runtime, how long what it will play runs in whole
# seconds, empty when that cannot be known (a random list pick not yet drawn,
# a plain file, a plugin or a library item with no duration). Functional uses
# them to work out when a slot queued behind another will really start. They
# come before When and Label, so Label is still written last and cleared first.
NEXT_LIST_NAMES = ('Due', 'Runtime', 'When', 'Label')
# How far ahead the next occurrence is looked for. Today plus seven days, so a
# weekly slot already dealt with today still has a next occurrence (the same
# weekday next week) rather than vanishing from the skin for the evening.
NEXT_LOOKAHEAD_DAYS = 7
# How long an occurrence whose time has passed still counts as next when the
# catch-up window is off. Matches the two minute grace in _process_slot.
NEXT_GRACE_MINUTES = 2


def occurrence_key(slot, due):
    """
    <summary>
    The tracker key for one occurrence of a slot: its date, id and scheduled time.
    </summary>
    <param name="slot">The Slot the occurrence belongs to.</param>
    <param name="due">The datetime of the occurrence; only its date is used.</param>
    <returns>A string of the form 'YYYY-MM-DD-<id>-HHMM'.</returns>
    <remarks>
    This is the shape every per-day tracker (triggered, warned, cancelled,
    snoozed) and catchup.json use, so the next-slot publisher can ask whether
    a future occurrence has already been dealt with. The time is part of the
    key so editing a slot's time re-arms it the same day.
    </remarks>
    """
    return '{}-{}-{:02d}{:02d}'.format(due.strftime('%Y-%m-%d'), slot.id, slot.hour, slot.minute)


def when_label(due, now):
    """
    <summary>
    Say when an occurrence is due in words, for the skin's "Coming up" block.
    </summary>
    <param name="due">The occurrence's datetime.</param>
    <param name="now">The current time; only its date matters.</param>
    <returns>'Yesterday HH:MM' for an overdue occurrence from the day before (one
    still inside the catch-up window), 'Tonight HH:MM' for today, 'Tomorrow HH:MM',
    then 'Weekday HH:MM' within the week and 'Weekday dd/mm/yyyy HH:MM' beyond
    that.</returns>
    <remarks>
    Today reads 'Tonight' whatever the hour, which is what the skin asked for.
    The dated form starts at seven days ahead, where a bare weekday would
    otherwise be ambiguous with the same weekday this week. Dates are en-GB.
    </remarks>
    """
    time_text = '{:02d}:{:02d}'.format(due.hour, due.minute)
    days_ahead = (due.date() - now.date()).days
    if days_ahead < 0:
        return 'Yesterday {}'.format(time_text)
    if days_ahead == 0:
        return 'Tonight {}'.format(time_text)
    if days_ahead == 1:
        return 'Tomorrow {}'.format(time_text)
    weekday = DAY_NAMES[due.weekday()]
    if days_ahead < 7:
        return '{} {}'.format(weekday, time_text)
    return '{} {:02d}/{:02d}/{:04d} {}'.format(weekday, due.day, due.month, due.year, time_text)


def next_property_order(values):
    """
    <summary>
    The order in which _write_next_properties visits the Engage.Next.* property
    names (without the prefix) for a given set of values.
    </summary>
    <param name="values">The property name to value dict being published; empty
    when everything is being cleared.</param>
    <returns>A list of names: the single set, then the numbered rows.</returns>
    <remarks>
    Publishing: the single set in NEXT_PROPERTY_NAMES order (Label last), then the
    rows from NEXT_LIST_COUNT down to 1, each in NEXT_LIST_NAMES order (Due,
    Runtime, When, Label). Clearing: the
    single set reversed (Label first, as it always has been), then rows 1 upwards,
    each Label first, so each group is cleared in the reverse of its publish order.
    A row with no Label in values is being cleared, so it takes the clearing order
    within itself even during a publish: a row dropping off the end of the list
    hides before its When goes.
    </remarks>
    """
    if values:
        names = list(NEXT_PROPERTY_NAMES)
        rows = range(NEXT_LIST_COUNT, 0, -1)
    else:
        names = list(reversed(NEXT_PROPERTY_NAMES))
        rows = range(1, NEXT_LIST_COUNT + 1)
    for number in rows:
        row = ['{}.{}'.format(number, name) for name in NEXT_LIST_NAMES]
        if not values.get('{}.Label'.format(number)):
            row.reverse()
        names.extend(row)
    return names


def parse_kodi_datetime(text):
    """
    <summary>
    Turn a Kodi library timestamp such as '2026-09-27 21:24:27' into a datetime.
    </summary>
    <param name="text">The lastplayed string JSON-RPC returned; empty when never played.</param>
    <returns>A naive local datetime, or None when the string is empty or malformed.</returns>
    <remarks>
    Kodi writes these stamps in local time, so they compare directly with
    datetime.now(). Parsed by hand rather than with strptime for the reason
    given on Slot.parsed_date: Kodi's embedded Python raises TypeError from
    strptime when it is first called off the main thread.
    </remarks>
    """
    if not text:
        return None
    try:
        date_part, time_part = text.strip().split(' ')
        year, month, day = (int(p) for p in date_part.split('-'))
        hour, minute, second = (int(p) for p in time_part.split(':'))
        return datetime(year, month, day, hour, minute, second)
    except (ValueError, TypeError):
        return None


class Slot:
    """
    <summary>
    Wraps one slot dict from the JSON store (resources/lib/slots.py).
    </summary>
    """

    def __init__(self, data):
        """
        <summary>
        Copy the slot's fields out of its JSON dict, applying the store's defaults and
        converting the legacy single 'day' and 'movies' fields to 'days' and 'items'.
        </summary>
        <param name="data">One slot dict as slots.py loads it.</param>
        <remarks>
        index is id minus one and exists only so log lines can say 'slot 3' via index + 1.
        timeout_action is resolved through slots.timeout_action_of so an old slot without
        the key gets the default, and banner_style through slots.banner_style_of in the
        same way; banner_style is the slot's own choice ('default' means follow the
        master setting) and is resolved to a layout only when the banner opens, in
        _handle_warning. hour, minute and warning are read with
        slot_rules._int_or and clamped to their ranges, so a hand edited value such as
        '' or 25 gives the default rather than an exception that would stop every slot;
        the dialog and the API only ever store whole numbers in range.
        </remarks>
        """
        self.id = data.get('id', 0)
        # Kept for log readability, "slot 3" rather than internal ids.
        self.index = slot_rules._int_or(self.id, 0) - 1
        self.enabled = bool(data.get('enabled', False))
        self.label = data.get('label') or ''
        # Multiple days supported via 'days'; fall back to legacy single 'day'.
        self.days = data.get('days') or ([data['day']] if data.get('day') else ['Monday'])
        self.specific_date = (data.get('date') or '').strip()
        # Weekly (the days above), every few weeks, monthly or yearly; see runs_on.
        from resources.lib import slots as repeat_store
        self.repeat = repeat_store.repeat_of(data)
        self.week_interval = repeat_store.week_interval_of(data)
        self.week_start = repeat_store.week_start_of(data)
        self.month_day = repeat_store.month_day_of(data)
        self.month = repeat_store.month_of(data)
        self.hour = min(max(slot_rules._int_or(data.get('hour'), 19), 0), 23)
        self.minute = min(max(slot_rules._int_or(data.get('minute'), 0), 0), 59)
        self.warning = max(slot_rules._int_or(data.get('warning'), 5), 0)
        self.favourite = data.get('favourite') or ''
        self.autoopen = bool(data.get('autoopen', False))
        self.snooze_enabled = bool(data.get('snooze', True))
        # What an untouched banner resolves to when the countdown hits zero.
        from resources.lib import slots as slot_store
        self.timeout_action = slot_store.timeout_action_of(data)
        # The slot's banner layout choice: 'standard', 'thin' or 'default'.
        self.banner_style = slot_store.banner_style_of(data)
        # 'favourite' (default) plays a single Kodi favourite (TV next-unwatched
        # or movie/folder). 'sequence' plays the next unwatched item from an
        # ordered library list: movies (e.g. the Marvel films) or episodes
        # across shows (e.g. an Arrowverse air-date binge).
        self.kind = data.get('kind', 'favourite')
        self.seq_type = data.get('seq_type', 'movie')
        # Unified item list; convert legacy 'movies' if present.
        self.items = data.get('items')
        if self.items is None:
            self.items = [{'type': 'movie', 'id': m.get('id'), 'title': m.get('title'),
                           'year': m.get('year')}
                          for m in (data.get('movies') or [])]
        # 'list' plays from one of the Functional skin's lists, named here.
        # list_items is filled by _reload_slots from the skin's file, never
        # from slots.json, so it is always the list as the skin has it now.
        self.list_name = data.get('list_name') or ''
        self.list_mode = functional_lists.mode_of(data)
        self.list_sequels = functional_lists.sequels_of(data)
        self.list_items = []
        # The list's name as the skin spells it, for the Coming up rows of a
        # random slot; filled by _reload_slots with list_items.
        self.list_title = self.list_name

    def day_indexes(self):
        """
        <summary>
        Weekday numbers (Mon=0) this slot runs on.
        </summary>
        """
        out = []
        for d in self.days:
            try:
                out.append(DAY_NAMES.index(d))
            except ValueError:
                pass
        return out

    def runs_on(self, day):
        """
        <summary>
        Whether this slot has an occurrence on the given date.
        </summary>
        <param name="day">A date.</param>
        <returns>True when the slot runs that day.</returns>
        <remarks>
        The one rule both the due check and the next-slot walk use. A valid one-shot
        date wins over the repeat; an invalid one is ignored, as it always has been.
        Monthly runs on month_day, moved to the last day of a shorter month, so the
        31st runs on 30th April and 28th or 29th February. Yearly runs on month_day of
        month with the same move, so 29th February runs on the 28th outside leap years.
        Every few weeks runs on the days list in the weeks that are a whole number of
        week_interval weeks from week_start. Weekly runs on the days list.
        </remarks>
        """
        target_date = self.parsed_date()
        if target_date is not None:
            return day == target_date
        from resources.lib import slots as repeat_store
        if self.repeat == repeat_store.REPEAT_MONTHLY:
            return day.day == min(self.month_day, repeat_store.last_day_of_month(day.year, day.month))
        if self.repeat == repeat_store.REPEAT_YEARLY:
            if day.month != self.month:
                return False
            return day.day == min(self.month_day, repeat_store.last_day_of_month(day.year, day.month))
        if self.repeat == repeat_store.REPEAT_EVERY_WEEKS:
            if not repeat_store.in_week_cycle(day, self.week_start, self.week_interval):
                return False
        return day.weekday() in self.day_indexes()

    def parsed_date(self):
        """
        <summary>
        Return a date object if specific_date is a valid YYYY-MM-DD, else None.
        </summary>
        <remarks>
        Parses manually rather than with datetime.strptime, Kodi's embedded
        Python has a long-standing bug where strptime raises TypeError when
        first called from a non-main thread.
        </remarks>
        """
        if not self.specific_date:
            return None
        try:
            parts = self.specific_date.split('-')
            if len(parts) != 3:
                return None
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            return datetime(year, month, day).date()
        except (ValueError, TypeError):
            return None


class EngageScheduler:

    """
    <summary>
    Polling loop that watches the enabled slots, shows the banner when one is due and
    acts on the answer.
    </summary>
    <remarks>
    Per occurrence state (triggered, warned, cancelled, snoozed) is keyed by
    occurrence_key and purged by _purge_trackers each tick once it can no longer be
    acted on. queued_next and resume_after carry the Queue Next and Stop &
    Resume After choices across the playback events _EngagePlayerMonitor reports.
    </remarks>
    """
    def __init__(self):
        """
        <summary>
        Create the per-day trackers, the Queue Next list, the resume slot and the player
        monitor that reports playback events back to this object.
        </summary>
        <remarks>
        The player monitor is stored on self._player and never read again: it is kept
        only so the xbmc.Player subclass stays alive, and with it its callbacks, for the
        life of the service. self.monitor is the service's own xbmc.Monitor, used for the
        abort wait and for the notifications that refresh the published next slot.
        </remarks>
        """
        self.monitor = _EngageMonitor(self)
        self.triggered = {}
        self.warned = {}
        self.cancelled = {}
        self.snoozed = {}
        self._warned_misconfig = set()
        # When the user picks "Queue Next" we append the (slot, key) here and
        # the player monitor fires the head of the list when current playback
        # ends. A list, not a single item, so a second queued slot waits its
        # turn behind the first instead of replacing it.
        self.queued_next = []
        # When the user picks "Stop & Resume After" we stash the interrupted
        # item here; _resume_armed gates resuming until the slot itself ends.
        self.resume_after = None
        self._resume_armed = False
        # True while _stop_and_requeue is stopping playback itself. Kodi
        # reports that stop through onPlayBackStopped like any other, and
        # without this the queue would be fired by our own stop; see
        # _on_playback_ended.
        self._stopping_on_purpose = False
        # A random list slot's pick, keyed by occurrence like the trackers
        # above, so the banner, a snoozed re-prompt and the playback all name
        # the same item. COMING-UP-PICK-AHEAD, 22/10/2026: drawn as soon as
        # the occurrence is in the Coming up rows, kept in list_picks.json so
        # a restart does not draw again, and dropped once its day has gone.
        from resources.lib import slots as slot_store
        self._list_picks = slot_store.get_list_picks()
        # Picks checked against the library and the list since the caches were
        # last cleared; see _pick_still_good.
        self._picks_checked = set()
        self._picks_lock = threading.RLock()
        self._player = _EngagePlayerMonitor(self)
        # Next-slot publishing for skins. The cache holds each slot's episode
        # line and poster, keyed by slot id, so the library is asked once per
        # slot and again only after a library change or a slot save. The lock
        # serialises publishes, which can come from the scheduler thread (every
        # tick) and from notification threads (a save or a library update).
        # Nothing is written to the Home window until _wait_for_gui has run:
        # Home window writes during Kodi's start-up are implicated in a crash
        # seen by the Functional skin's own service.
        self._next_item_cache = {}
        # The Coming up row labels of each list slot, keyed and signed like
        # _next_item_cache and cleared with it; see _list_row_labels.
        self._next_rows_cache = {}
        # The running time of each coming showing of a slot, keyed, signed and
        # cleared like the two above; see _slot_runtimes.
        self._next_runtime_cache = {}
        self._publish_lock = threading.Lock()
        self._gui_ready = False

    def _log(self, msg, level=xbmc.LOGINFO):
        """
        <summary>
        Write one line to kodi.log under the 'Engage:' prefix.
        </summary>
        <param name="msg">Text to log.</param>
        <param name="level">Kodi log level, LOGINFO by default.</param>
        """
        xbmc.log('Engage: {}'.format(msg), level)

    def _debug(self, msg):
        """
        <summary>
        Log at Kodi's debug level, and only while the add-on's debug setting is on.
        </summary>
        <param name="msg">Text to log; it is prefixed with [DEBUG].</param>
        <remarks>
        The setting is read on every call so it can be toggled without restarting the service.
        </remarks>
        """
        addon = xbmcaddon.Addon(ADDON_ID)
        if addon.getSettingBool('debug'):
            self._log('[DEBUG] {}'.format(msg), xbmc.LOGDEBUG)

    def _reload_slots(self):
        """
        <summary>
        Read the slot store afresh and wrap every enabled, usable slot as a Slot.
        </summary>
        <returns>The list of Slot objects to check this tick.</returns>
        <remarks>
        A sequence with no items, a favourite slot with no favourite, or a list slot whose
        Functional list is missing or empty, is skipped with one warning per slot id per
        service run, and so is a slot dict that Slot() cannot read at all (a hand edited
        file), so one bad entry never stops the others. A missing label defaults to the
        favourite name, the list name, or 'Sequence'. Reading every tick means edits
        made in the Manage slots dialog, and changes made to a list in the skin, take
        effect without a restart. The skin's lists.json is read at most once per call.
        </remarks>
        """
        from resources.lib import slots as slot_store
        slots = []
        skin_lists = None
        for data in slot_store.load_slots():
            try:
                slot = Slot(data)
            except Exception as e:
                bad_id = data.get('id') if isinstance(data, dict) else None
                marker = ('unreadable', str(bad_id))
                if marker not in self._warned_misconfig:
                    self._log('Slot {}: cannot be read ({}: {}), skipping'.format(
                        bad_id, type(e).__name__, e), xbmc.LOGWARNING)
                    self._warned_misconfig.add(marker)
                continue
            if not slot.enabled:
                continue
            if slot.kind == 'list':
                if skin_lists is None:
                    skin_lists = functional_lists.load_lists()
                found = functional_lists.find_list(slot.list_name, skin_lists)
                if not found or not found['items']:
                    if slot.id not in self._warned_misconfig:
                        why = 'is empty' if found else 'was not found in the Functional skin'
                        self._log('Slot {}: list "{}" {}, skipping'.format(
                            slot.id, slot.list_name, why), xbmc.LOGWARNING)
                        self._warned_misconfig.add(slot.id)
                    continue
                # A list that comes back clears the warning, so a second
                # disappearance is logged again.
                self._warned_misconfig.discard(slot.id)
                slot.list_items = found['items']
                slot.list_title = found['name']
                if not slot.label:
                    slot.label = found['name']
            elif slot.kind == 'sequence':
                if not slot.items:
                    if slot.id not in self._warned_misconfig:
                        self._log('Slot {} is an empty sequence, skipping'.format(slot.id), xbmc.LOGWARNING)
                        self._warned_misconfig.add(slot.id)
                    continue
                if not slot.label:
                    slot.label = 'Sequence'
            else:
                if not slot.favourite:
                    if slot.id not in self._warned_misconfig:
                        self._log('Slot {} is enabled but has no favourite set, skipping'.format(slot.id), xbmc.LOGWARNING)
                        self._warned_misconfig.add(slot.id)
                    continue
                # Fall back to the favourite name when the user didn't enter a label.
                if not slot.label:
                    slot.label = slot.favourite
            slots.append(slot)
        return slots

    def _today_key(self, slot):
        """
        <summary>
        Dedup key for today's occurrence of a slot. Includes the scheduled
        time so editing a slot's time re-arms it even if it already fired
        today, important for testing and for genuine same-day reschedules.
        </summary>
        """
        return occurrence_key(slot, datetime.now())

    def _get_scheduled_dt(self, slot, now=None):
        """
        <summary>
        Return today's datetime for this slot if today matches, else None.
        </summary>
        <param name="slot">The slot.</param>
        <param name="now">The tick's time, whose date is 'today'; datetime.now() when
        omitted.</param>
        <returns>The datetime, or None.</returns>
        <remarks>
        Whether today counts is Slot.runs_on: a one-shot date, else the weekly days,
        the days of an on week for every few weeks, the monthly day or the yearly date.
        </remarks>
        """
        if now is None:
            now = datetime.now()
        if not slot.runs_on(now.date()):
            return None
        return now.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)

    def _due_occurrences(self, slot, now):
        """
        <summary>
        The occurrences of a slot that this tick has to look at: yesterday's, when it is
        still live, then today's.
        </summary>
        <param name="slot">An enabled slot.</param>
        <param name="now">The tick's time.</param>
        <returns>A list of (scheduled_dt, key) tuples, earliest first; empty when the
        slot runs on neither day.</returns>
        <remarks>
        Yesterday's occurrence counts while the catch-up window still covers it (a
        22:00 slot with a 6 hour window is still live at 00:30), and while a snooze
        taken on it is still running, whatever the window. Before this the scheduler
        only ever looked at today, so a slot missed late in the evening and Kodi
        turned on after midnight was skipped without a word, and a snooze that ran
        past midnight never came back.
        </remarks>
        """
        found = []
        hours = self._catchup_hours()
        yesterday = now - timedelta(days=1)
        if slot.runs_on(yesterday.date()):
            due = yesterday.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
            key = occurrence_key(slot, due)
            live = hours > 0 and now <= due + timedelta(hours=hours)
            if live or key in self.snoozed:
                found.append((due, key))
        today_dt = self._get_scheduled_dt(slot, now)
        if today_dt is not None:
            found.append((today_dt, occurrence_key(slot, today_dt)))
        return found

    def _activate_favourite(self, favourite_name):
        """
        <summary>
        Activate a Kodi favourite by name. Try JSON-RPC first, fall back to window command.
        </summary>
        """
        self._log('Activating favourite: {}'.format(favourite_name))

        # Get the list of favourites and find a match
        request = json.dumps({
            'jsonrpc': '2.0',
            'method': 'Favourites.GetFavourites',
            'params': {'properties': ['window', 'path', 'windowparameter']},
            'id': 1
        })
        response = xbmc.executeJSONRPC(request)
        self._debug('Favourites.GetFavourites response: {}'.format(response))

        data = json.loads(response)
        favourites = data.get('result', {}).get('favourites', []) or []

        target = None
        for fav in favourites:
            if fav.get('title', '').lower() == favourite_name.lower():
                target = fav
                break

        if not target:
            self._log('Favourite not found: {}'.format(favourite_name), xbmc.LOGWARNING)
            xbmcgui.Dialog().notification(
                'Engage',
                'Favourite not found: {}'.format(favourite_name),
                xbmcgui.NOTIFICATION_WARNING,
                5000
            )
            self._open_favourites_fallback()
            return False

        fav_type = target.get('type', '')
        path = target.get('path', '')
        window = target.get('window', '')
        window_param = target.get('windowparameter', '')

        if fav_type == 'media' and path:
            xbmc.Player().play(path)
            self._log('Playing media favourite: {}'.format(path))
            return True
        elif fav_type == 'window' and window:
            cmd = 'ActivateWindow({},{})'.format(window, window_param) if window_param else 'ActivateWindow({})'.format(window)
            xbmc.executebuiltin(cmd)
            self._log('Opened window favourite: {}'.format(cmd))
            return True
        elif fav_type == 'script' and path:
            xbmc.executebuiltin('RunScript({})'.format(path))
            self._log('Ran script favourite: {}'.format(path))
            return True
        elif path:
            xbmc.Player().play(path)
            self._log('Playing path from favourite: {}'.format(path))
            return True
        else:
            self._log('Unknown favourite type: {}'.format(fav_type), xbmc.LOGWARNING)
            self._open_favourites_fallback()
            return False

    def _open_favourites_fallback(self):
        """
        <summary>
        Open the Kodi Favourites window as a fallback.
        </summary>
        """
        self._log('Opening Favourites window as fallback')
        xbmc.executebuiltin('ActivateWindow(Favourites)')

    def _resolve_episode_info(self, slot):
        """
        <summary>
        For TV show favourites, look up the next unwatched episode.
        Returns the episode dict from JSON-RPC or None for movies / non-TV / failed lookups.
        </summary>
        """
        favs = self._get_favourites_raw()
        fav = next((f for f in favs if f.get('title', '').lower() == slot.favourite.lower()), None)
        if not fav:
            return None
        tvshowid = self._extract_tvshowid(fav)
        if tvshowid is None:
            return None
        return self._get_next_unwatched(tvshowid)

    def _get_favourites_raw(self):
        """
        <summary>
        Kodi's favourites from JSON-RPC with the window, path and windowparameter properties.
        </summary>
        <returns>A list of favourite dicts; empty when the call returns no result.</returns>
        """
        request = json.dumps({
            'jsonrpc': '2.0',
            'method': 'Favourites.GetFavourites',
            'params': {'properties': ['window', 'path', 'windowparameter']},
            'id': 1
        })
        response = xbmc.executeJSONRPC(request)
        data = json.loads(response)
        return data.get('result', {}).get('favourites', []) or []

    def _extract_tvshowid(self, fav):
        """
        <summary>
        Pull the tvshowid out of a videodb://tvshows/titles/<id>/ favourite.
        </summary>
        """
        wp = fav.get('windowparameter') or ''
        path = fav.get('path') or ''
        for source in (wp, path):
            match = re.search(r'videodb://tvshows/titles/(\d+)', source, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def _extract_movieid(self, fav):
        """
        <summary>
        Pull the movieid out of a videodb://movies/titles/<id> favourite.
        </summary>
        <param name="fav">One favourite dict from Favourites.GetFavourites.</param>
        <returns>The id as an int, or None when the favourite is not a library movie path.</returns>
        <remarks>
        A movie favourited from the library often stores its file path rather than a
        videodb path, and those are not matched here; see _last_played_for_slot.
        </remarks>
        """
        wp = fav.get('windowparameter') or ''
        path = fav.get('path') or ''
        for source in (wp, path):
            match = re.search(r'videodb://movies/titles/(\d+)', source, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def _get_next_unwatched(self, tvshowid):
        """
        <summary>
        Return the lowest season/episode unwatched episode for a TV show.
        </summary>
        """
        request = json.dumps({
            'jsonrpc': '2.0',
            'method': 'VideoLibrary.GetEpisodes',
            'params': {
                'tvshowid': tvshowid,
                'properties': ['title', 'season', 'episode', 'playcount', 'file',
                               'showtitle', 'art', 'thumbnail'],
                'filter': {'field': 'playcount', 'operator': 'is', 'value': '0'}
            },
            'id': 1
        })
        response = xbmc.executeJSONRPC(request)
        self._debug('GetEpisodes response (truncated): {}'.format(response[:500]))
        data = json.loads(response)
        episodes = data.get('result', {}).get('episodes', []) or []
        if not episodes:
            return None
        # Sort by (season, episode) for the chronologically earliest unwatched,
        # but push season 0 (specials) to the end, only offer a special when
        # every regular episode has been watched.
        episodes.sort(key=lambda e: (e.get('season', 0) == 0, e.get('season', 0), e.get('episode', 0)))
        return episodes[0]

    def _episode_label(self, episode):
        """
        <summary>
        Format an episode dict as 'S03E14, Lower Decks'.
        </summary>
        """
        if not episode:
            return ''
        return 'S{:02d}E{:02d}, {}'.format(
            episode.get('season', 0),
            episode.get('episode', 0),
            episode.get('title', '')
        )

    def _play_episode(self, episode):
        """
        <summary>
        Play a specific episode by episodeid via Player.Open.
        </summary>
        """
        request = json.dumps({
            'jsonrpc': '2.0',
            'method': 'Player.Open',
            'params': {'item': {'episodeid': episode['episodeid']}},
            'id': 1
        })
        response = xbmc.executeJSONRPC(request)
        self._log('Player.Open episode response: {}'.format(response))

    def _resolve_next_item(self, slot):
        """
        <summary>
        For a sequence slot, return the first unwatched item in the list as
        {'type', 'id', 'label'}, or None if empty / all watched. Items missing
        from the library are skipped. Works for both movie and episode items.
        </summary>
        """
        for entry in slot.items:
            item_id = entry.get('id')
            item_type = entry.get('type', 'movie')
            if not item_id:
                continue
            if item_type == 'episode':
                data = json.loads(xbmc.executeJSONRPC(json.dumps({
                    'jsonrpc': '2.0', 'method': 'VideoLibrary.GetEpisodeDetails',
                    'params': {'episodeid': item_id,
                               'properties': ['title', 'playcount', 'season', 'episode',
                                              'showtitle', 'art', 'thumbnail']},
                    'id': 1
                })))
                details = data.get('result', {}).get('episodedetails')
                if not details:
                    continue
                if int(details.get('playcount', 0)) == 0:
                    label = '{} · S{:02d}E{:02d}, {}'.format(
                        details.get('showtitle', entry.get('show', '')),
                        details.get('season', 0), details.get('episode', 0),
                        details.get('title', ''))
                    return {'type': 'episode', 'id': item_id, 'label': label,
                            'art': self._pick_poster(details, is_episode=True)}
            else:
                data = json.loads(xbmc.executeJSONRPC(json.dumps({
                    'jsonrpc': '2.0', 'method': 'VideoLibrary.GetMovieDetails',
                    'params': {'movieid': item_id,
                               'properties': ['title', 'playcount', 'art', 'thumbnail']},
                    'id': 1
                })))
                details = data.get('result', {}).get('moviedetails')
                if not details:
                    continue
                if int(details.get('playcount', 0)) == 0:
                    return {'type': 'movie', 'id': item_id,
                            'label': details.get('title', entry.get('title', '')),
                            'art': self._pick_poster(details, is_episode=False)}
        return None

    def _pick_poster(self, details, is_episode):
        """
        <summary>
        Choose a portrait poster image path from a library item's art dict.
        Prefers the show/movie poster; falls back to episode thumb.
        </summary>
        """
        art = details.get('art', {}) or {}
        if is_episode:
            for k in ('tvshow.poster', 'season.poster', 'poster'):
                if art.get(k):
                    return art[k]
            return art.get('thumb') or details.get('thumbnail') or ''
        for k in ('poster', 'thumb'):
            if art.get(k):
                return art[k]
        return details.get('thumbnail') or ''

    def _episode_art(self, episode):
        """
        <summary>
        Poster for a next-unwatched episode dict (from _get_next_unwatched).
        </summary>
        """
        return self._pick_poster(episode, is_episode=True)

    def _favourite_thumb(self, name):
        """
        <summary>
        Thumbnail for a plain favourite, matched by title.
        </summary>
        """
        request = json.dumps({
            'jsonrpc': '2.0', 'method': 'Favourites.GetFavourites',
            'params': {'properties': ['thumbnail']}, 'id': 1
        })
        data = json.loads(xbmc.executeJSONRPC(request))
        for fav in (data.get('result', {}).get('favourites', []) or []):
            if fav.get('title', '').lower() == (name or '').lower():
                return fav.get('thumbnail') or ''
        return ''

    def _play_item(self, item):
        """
        <summary>
        Play a sequence item (movie or episode) by library id via Player.Open.
        </summary>
        """
        key = 'episodeid' if item['type'] == 'episode' else 'movieid'
        response = xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'method': 'Player.Open',
            'params': {'item': {key: item['id']}}, 'id': 1
        }))
        self._log('Player.Open {} response: {}'.format(item['type'], response))

    def _list_item_watched(self, item):
        """
        <summary>
        Whether one Functional list item has been watched, by its library playcount.
        </summary>
        <param name="item">One list item dict from the skin's lists.json.</param>
        <returns>True when the library says it has been played; False when it has not,
        or when the library cannot say.</returns>
        <remarks>
        Only items with a library id can be answered. A plain file, a plugin path or a
        library id that no longer resolves counts as unwatched, so it stays playable. In
        'next' mode that means such an item at the head of the list is offered every
        time; the skin's Remove Watched switch on the list, which tracks plain files by
        playback progress, is the way to move past it. An episode expanded from a whole
        show or season row already carries its playcount; the row itself counts as
        watched once every episode in it has been, or when it has none left.
        </remarks>
        """
        if 'engage_playcount' in item:
            return item['engage_playcount'] > 0
        if functional_lists.is_group(item):
            return all(self._list_item_watched(ep) for ep in self._group_episodes(item))
        spec = functional_lists.play_spec(item) or {}
        lookups = {
            'movieid': ('VideoLibrary.GetMovieDetails', 'moviedetails'),
            'episodeid': ('VideoLibrary.GetEpisodeDetails', 'episodedetails'),
            'musicvideoid': ('VideoLibrary.GetMusicVideoDetails', 'musicvideodetails'),
            'songid': ('AudioLibrary.GetSongDetails', 'songdetails'),
        }
        for id_key, (method, result_key) in lookups.items():
            if id_key in spec:
                data = json.loads(xbmc.executeJSONRPC(json.dumps({
                    'jsonrpc': '2.0', 'method': method,
                    'params': {id_key: spec[id_key], 'properties': ['playcount']}, 'id': 1
                })))
                details = data.get('result', {}).get(result_key) or {}
                return int(details.get('playcount', 0) or 0) > 0
        return False

    def _resolve_list_item(self, slot, key=None):
        """
        <summary>
        Choose what a list slot plays, by its mode: the first unwatched item in list
        order, every item in turn, a random unwatched item, or any item at random.
        </summary>
        <param name="slot">A list slot whose list_items _reload_slots has filled.</param>
        <param name="key">The occurrence key when a banner or playback needs a definite
        item; None when only describing the slot.</param>
        <returns>{'spec', 'label', 'art', 'identity', 'index'} for the chosen item, None
        when an unwatched in order list is all watched, or for a random slot asked
        without a key.</returns>
        <remarks>
        The unwatched random mode picks among unwatched items and, once every item has
        been watched, among all of them, so a rewatch list never runs dry; the any mode
        picks among all of them every time. Either random pick is stored
        under the key and reused for that occurrence, so a snoozed banner comes back with
        the same film and Start plays the one the banner named. Without a key no pick is
        made and the describer shows 'Random pick'. Since COMING-UP-PICK-AHEAD
        (22/10/2026) the Coming up rows ask with each occurrence's key, so the pick is
        drawn when the occurrence first shows there; picks are kept in list_picks.json
        so a restart does not draw again, and a kept one is checked by _pick_still_good
        before it is used (see _draw_list_pick).
        The in turn mode needs no key: its place only moves when _play_slot records a
        play, so the banner and the playback name the same item anyway.
        With the slot's sequels option on, either random draw offers only the next
        item of each series (see _list_series and functional_lists.series_pool), and
        the choice carries a 'series' entry so _play_slot can record that series'
        place.
        A whole show or season row is, as in the skin's own Port modes, every one of
        its episodes in order for the two in order modes, and ONE entry in a random
        draw that plays one of its episodes: an unwatched one for the unwatched mode
        while there is one, any of them otherwise. A row whose show or season has
        gone from the library, or whose episodes the skin's Remove Watched has all
        taken out, is left out.
        </remarks>
        """
        items = slot.list_items
        if slot.list_mode in functional_lists.RANDOM_MODES:
            if key is None:
                return None
            with self._picks_lock:
                kept = self._list_picks.get(key)
                if kept is not None and self._pick_still_good(slot, key, kept):
                    return kept
                return self._draw_list_pick(slot, key)
        items = self._flat_items(items)
        if not items:
            return None
        if slot.list_mode == functional_lists.MODE_TURN:
            from resources.lib import slots as slot_store
            index = functional_lists.turn_index(items, slot_store.get_list_progress(slot.id))
            return self._list_choice(items[index], index)
        for index, item in enumerate(items):
            if not self._list_item_watched(item):
                return self._list_choice(item, index)
        return None

    def _pick_still_good(self, slot, key, pick):
        """
        <summary>
        Whether a kept random pick can still be played for its occurrence.
        </summary>
        <param name="slot">The random list slot.</param>
        <param name="key">The occurrence key the pick was drawn for.</param>
        <param name="pick">The kept choice dict.</param>
        <returns>True to keep it; False to draw again.</returns>
        <remarks>
        COMING-UP-PICK-AHEAD, 22/10/2026. A pick drawn days ahead can go stale: the
        list was edited and the row it came from (the item itself, or the whole show
        or season it was drawn from) is no longer at that place in it, or, for the
        unwatched mode, the item has been watched since. A snoozed occurrence keeps
        its pick whatever, so the banner that comes back names the same film. The
        answer is remembered in _picks_checked until the next library change or slot
        save clears it, so the Coming up publisher does not ask the library every
        tick.
        </remarks>
        """
        if key in self.snoozed or key in self._picks_checked:
            return True
        items = slot.list_items
        index = pick.get('index')
        good = (isinstance(index, int) and 0 <= index < len(items)
                and list(functional_lists.item_identity(items[index])) == list(pick.get('row') or []))
        if good and slot.list_mode == functional_lists.MODE_RANDOM:
            item = dict(pick.get('item') or {})
            item.pop('engage_playcount', None)
            good = bool(item) and not self._list_item_watched(item)
        if good:
            self._picks_checked.add(key)
        else:
            self._log('Slot {}: the pick kept for {} is no longer good, drawing again'.format(
                slot.index + 1, key))
        return good

    def _draw_list_pick(self, slot, key):
        """
        <summary>
        Draw, keep and return a random list slot's pick for one occurrence.
        </summary>
        <param name="slot">The random list slot.</param>
        <param name="key">The occurrence key.</param>
        <returns>The choice dict, or None when the list has nothing to play.</returns>
        <remarks>
        Called with _picks_lock held; the draw itself is the one _resolve_list_item
        describes. COMING-UP-PICK-AHEAD, 22/10/2026: each occurrence draws on its
        own, as at the banner, so two coming showings can draw the same item. In the
        unwatched mode that sorts itself out: once the first is watched the later
        pick is stale (_pick_still_good) and drawn again. The pick
        carries the list item it came from, for _pick_still_good, and is written to
        list_picks.json straight away.
        </remarks>
        """
        items = slot.list_items
        groups = {i: self._group_episodes(it) for i, it in enumerate(items)
                  if functional_lists.is_group(it)}
        pool = [i for i in range(len(items)) if groups.get(i, True)]
        if not pool:
            return None
        unwatched = []
        if slot.list_mode == functional_lists.MODE_RANDOM:
            unwatched = [i for i in pool if not self._list_item_watched(items[i])]
            pool = unwatched or pool
        series = {}
        if slot.list_sequels != functional_lists.SEQUELS_OFF:
            from resources.lib import slots as slot_store
            series = self._list_series(items, slot.list_sequels)
            # The unwatched draw takes each series' earliest unwatched film;
            # a draw over everything walks each series from where it got to.
            progress = None if unwatched else slot_store.get_series_progress(slot.id)
            pool = functional_lists.series_pool(items, pool, series, progress) or pool
        index = random.choice(pool)
        if index in groups:
            episodes = groups[index]
            if slot.list_mode == functional_lists.MODE_RANDOM:
                episodes = [ep for ep in episodes if not self._list_item_watched(ep)] or episodes
            picked = random.choice(episodes)
        else:
            picked = items[index]
        choice = self._list_choice(picked, index)
        choice['item'] = picked
        choice['row'] = list(functional_lists.item_identity(items[index]))
        choice['identity'] = list(choice['identity'])
        at = functional_lists.series_position(items, series, index)
        choice['series'] = list(at) if at else at
        self._list_picks[key] = choice
        self._picks_checked.add(key)
        from resources.lib import slots as slot_store
        slot_store.set_list_picks(self._list_picks)
        self._log('Slot {}: drew "{}" for {}'.format(slot.index + 1, choice['label'], key))
        return choice

    def _group_episodes(self, item):
        """
        <summary>
        The episodes a whole show or season row stands for, in order.
        </summary>
        <param name="item">A tvshow or season row from the skin's list.</param>
        <returns>List item dicts from functional_lists.episode_item, sorted by season
        then episode; empty when the show or season is no longer in the library.</returns>
        <remarks>
        Worked out the way the skin's own service does it, so Engage and the skin's
        Port agree on what a row holds: a whole show leaves out season 0 (Specials), a
        season row is that season in full, and any episode in the row's skip list
        (taken out by the skin's Remove Watched) is dropped. Asked of the library each
        time, so episodes added since the row was made are included.
        </remarks>
        """
        params = {'properties': ['title', 'file', 'season', 'episode', 'showtitle',
                                 'art', 'thumbnail', 'playcount']}
        if item.get('dbtype') == 'tvshow':
            params['tvshowid'] = int(item['dbid'])
            season = None
        else:
            data = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'method': 'VideoLibrary.GetSeasonDetails',
                'params': {'seasonid': int(item['dbid']), 'properties': ['tvshowid', 'season']},
                'id': 1
            })))
            details = data.get('result', {}).get('seasondetails') or {}
            if not details.get('tvshowid') or details.get('season') is None:
                return []
            params['tvshowid'] = int(details['tvshowid'])
            season = int(details['season'])
            params['season'] = season
        data = json.loads(xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'method': 'VideoLibrary.GetEpisodes', 'params': params, 'id': 1
        })))
        rows = data.get('result', {}).get('episodes') or []
        skip = set(item.get('skip') or [])
        episodes = []
        for row in rows:
            try:
                numbers = (int(row.get('season') or 0), int(row.get('episode') or 0))
            except (ValueError, TypeError):
                numbers = (0, 0)
            if season is None and numbers[0] <= 0:
                continue
            if functional_lists.episode_key(*numbers) in skip:
                continue
            episodes.append((numbers, functional_lists.episode_item(row, item)))
        episodes.sort(key=lambda entry: entry[0])
        return [ep for _, ep in episodes]

    def _flat_items(self, items):
        """
        <summary>
        A list's items with every whole show or season row replaced by its episodes.
        </summary>
        <param name="items">The list's items as the skin has them.</param>
        <returns>A new list of playable items, in list order and episode order.</returns>
        """
        flat = []
        for item in items:
            if functional_lists.is_group(item):
                flat.extend(self._group_episodes(item))
            else:
                flat.append(item)
        return flat

    def _list_row_labels(self, slot):
        """
        <summary>
        What the Coming up rows say for the next showings of a list slot, first to last.
        </summary>
        <param name="slot">A list slot whose list_items _reload_slots has filled.</param>
        <returns>Up to NEXT_LIST_COUNT labels. A random slot gives its list's name for
        every showing, because what it plays is drawn when the banner comes up. The in
        order unwatched mode gives the unwatched items in list order, so the second
        showing names the item after the first; once those run out the list is all
        watched and nothing more is given. The in turn mode walks on from the item
        played last, wrapping round.</returns>
        <remarks>
        Cached per slot, signed like _next_item_cached and cleared with it, because
        the unwatched mode asks the library about every item. A lookup failure is
        logged and cached as no labels, so the rows fall back to the slot's label.
        </remarks>
        """
        signature = self._slot_signature(slot)
        cached = self._next_rows_cache.get(slot.id)
        if cached is not None and cached[0] == signature:
            return cached[1]
        labels = []
        try:
            if slot.list_mode in functional_lists.RANDOM_MODES:
                labels = [slot.list_title] * NEXT_LIST_COUNT
            else:
                items = self._flat_items(slot.list_items)
                if items and slot.list_mode == functional_lists.MODE_TURN:
                    from resources.lib import slots as slot_store
                    start = functional_lists.turn_index(items, slot_store.get_list_progress(slot.id))
                    labels = [functional_lists.item_label(items[(start + n) % len(items)])
                              for n in range(NEXT_LIST_COUNT)]
                elif items:
                    for item in items:
                        if len(labels) == NEXT_LIST_COUNT:
                            break
                        if not self._list_item_watched(item):
                            labels.append(functional_lists.item_label(item))
        except Exception as e:
            self._log('Slot {}: Coming up rows for the skin failed: {}'.format(
                slot.index + 1, e), xbmc.LOGWARNING)
            labels = []
        self._next_rows_cache[slot.id] = (signature, labels)
        return labels

    def _spec_runtime(self, spec):
        """
        <summary>
        How long one library item runs, from its play spec.
        </summary>
        <param name="spec">A Player.Open item such as {'movieid': 3}, or None.</param>
        <returns>Whole seconds, or 0 when the spec is a plain file, a plugin path or
        None, or when the library holds no duration for the item.</returns>
        <remarks>
        A film, episode or music video gives its runtime, falling back to the
        duration of its first video stream when the scraper left the runtime empty;
        a song gives its duration.
        </remarks>
        """
        lookups = {
            'movieid': ('VideoLibrary.GetMovieDetails', 'moviedetails'),
            'episodeid': ('VideoLibrary.GetEpisodeDetails', 'episodedetails'),
            'musicvideoid': ('VideoLibrary.GetMusicVideoDetails', 'musicvideodetails'),
            'songid': ('AudioLibrary.GetSongDetails', 'songdetails'),
        }
        for id_key, (method, result_key) in lookups.items():
            if spec and id_key in spec:
                fields = ['duration'] if id_key == 'songid' else ['runtime', 'streamdetails']
                data = json.loads(xbmc.executeJSONRPC(json.dumps({
                    'jsonrpc': '2.0', 'method': method,
                    'params': {id_key: spec[id_key], 'properties': fields}, 'id': 1
                })))
                return self._details_runtime(data.get('result', {}).get(result_key) or {})
        return 0

    def _details_runtime(self, details):
        """
        <summary>
        The running time held in one library details dict.
        </summary>
        <param name="details">A movie, episode, music video or song dict from JSON-RPC.</param>
        <returns>Whole seconds, 0 when there is none.</returns>
        """
        for value in (details.get('runtime'), details.get('duration')):
            try:
                if int(value or 0) > 0:
                    return int(value)
            except (TypeError, ValueError):
                pass
        video = ((details.get('streamdetails') or {}).get('video') or [{}])[0] or {}
        try:
            return max(0, int(video.get('duration') or 0))
        except (TypeError, ValueError):
            return 0

    def _lookup_runtimes(self, slot):
        """
        <summary>
        The running time of each coming showing of a slot, first to last, asked of
        the library.
        </summary>
        <param name="slot">The slot.</param>
        <returns>Up to NEXT_LIST_COUNT whole seconds, 0 where a showing's length is
        unknown; empty when none can be known.</returns>
        <remarks>
        Each showing plays what the one before left next, so the nth showing is the
        nth item the slot would play: a TV favourite's nth unwatched episode, in the
        order _get_next_unwatched uses; a film favourite's film every time; a
        sequence's nth unwatched item; an in order list's nth unwatched item, or the
        nth from where an in turn list got to. A random list cannot be known until
        the pick is drawn (see _publish_next), and any other favourite (a folder, a
        playlist, a plugin, a file) has no library length, so they give nothing.
        </remarks>
        """
        count = NEXT_LIST_COUNT
        if slot.kind == 'list':
            if slot.list_mode in functional_lists.RANDOM_MODES:
                return []
            items = self._flat_items(slot.list_items)
            if not items:
                return []
            if slot.list_mode == functional_lists.MODE_TURN:
                from resources.lib import slots as slot_store
                start = functional_lists.turn_index(items, slot_store.get_list_progress(slot.id))
                chosen = [items[(start + n) % len(items)] for n in range(count)]
            else:
                chosen = []
                for item in items:
                    if len(chosen) == count:
                        break
                    if not self._list_item_watched(item):
                        chosen.append(item)
            return [self._spec_runtime(functional_lists.play_spec(item)) for item in chosen]
        if slot.kind == 'sequence':
            runtimes = []
            for entry in slot.items:
                if len(runtimes) == count:
                    break
                item_id = entry.get('id')
                if not item_id:
                    continue
                if entry.get('type', 'movie') == 'episode':
                    method, id_key, result_key = ('VideoLibrary.GetEpisodeDetails', 'episodeid',
                                                  'episodedetails')
                else:
                    method, id_key, result_key = ('VideoLibrary.GetMovieDetails', 'movieid',
                                                  'moviedetails')
                data = json.loads(xbmc.executeJSONRPC(json.dumps({
                    'jsonrpc': '2.0', 'method': method,
                    'params': {id_key: item_id,
                               'properties': ['playcount', 'runtime', 'streamdetails']},
                    'id': 1
                })))
                details = data.get('result', {}).get(result_key)
                if details and int(details.get('playcount', 0) or 0) == 0:
                    runtimes.append(self._details_runtime(details))
            return runtimes
        fav = next((f for f in self._get_favourites_raw()
                    if f.get('title', '').lower() == slot.favourite.lower()), None)
        if not fav:
            return []
        tvshowid = self._extract_tvshowid(fav)
        if tvshowid is not None:
            data = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'method': 'VideoLibrary.GetEpisodes',
                'params': {'tvshowid': tvshowid,
                           'properties': ['season', 'episode', 'runtime', 'streamdetails'],
                           'filter': {'field': 'playcount', 'operator': 'is', 'value': '0'}},
                'id': 1
            })))
            episodes = data.get('result', {}).get('episodes', []) or []
            episodes.sort(key=lambda e: (e.get('season', 0) == 0, e.get('season', 0),
                                         e.get('episode', 0)))
            return [self._details_runtime(ep) for ep in episodes[:count]]
        movieid = self._extract_movieid(fav)
        if movieid is not None:
            return [self._spec_runtime({'movieid': movieid})] * count
        return []

    def _slot_runtimes(self, slot):
        """
        <summary>
        _lookup_runtimes with a per slot cache, for the next-slot publisher.
        </summary>
        <param name="slot">The slot.</param>
        <returns>The same list _lookup_runtimes returns.</returns>
        <remarks>
        Signed like _next_item_cached and cleared with it, so a library change, a
        watched episode or a slot save looks again. A lookup failure is logged and
        cached as nothing known, and the rows then go out without a Runtime.
        </remarks>
        """
        signature = self._slot_signature(slot)
        cached = self._next_runtime_cache.get(slot.id)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            runtimes = self._lookup_runtimes(slot)
        except Exception as e:
            self._log('Slot {}: Coming up running times for the skin failed: {}'.format(
                slot.index + 1, e), xbmc.LOGWARNING)
            runtimes = []
        self._next_runtime_cache[slot.id] = (signature, runtimes)
        return runtimes

    def _row_runtime(self, slot, nth, key):
        """
        <summary>
        The Runtime of one numbered Coming up row, in whole seconds.
        </summary>
        <param name="slot">The slot due in that row.</param>
        <param name="nth">How many earlier rows the same slot already has, 0 for its
        first.</param>
        <param name="key">The row's occurrence key.</param>
        <returns>Seconds, 0 when unknown.</returns>
        <remarks>
        A random list slot draws its pick for the occurrence here, ahead of the banner
        (COMING-UP-PICK-AHEAD), through _resolve_list_item, which keeps it; the
        pick's length is then remembered on the pick itself, so it is asked for once.
        </remarks>
        """
        if slot.kind == 'list' and slot.list_mode in functional_lists.RANDOM_MODES:
            try:
                pick = self._resolve_list_item(slot, key)
            except Exception as e:
                self._log('Slot {}: drawing the pick ahead failed: {}'.format(
                    slot.index + 1, e), xbmc.LOGWARNING)
                pick = None
            if not pick:
                return 0
            if 'runtime' not in pick:
                try:
                    pick['runtime'] = self._spec_runtime(pick.get('spec'))
                except Exception as e:
                    self._log('Slot {}: running time of the random pick failed: {}'.format(
                        slot.index + 1, e), xbmc.LOGWARNING)
                    pick['runtime'] = 0
            return pick['runtime']
        runtimes = self._slot_runtimes(slot)
        return runtimes[nth] if nth < len(runtimes) else 0

    def _row_label(self, slot, nth):
        """
        <summary>
        The label of one numbered Coming up row.
        </summary>
        <param name="slot">The slot due in that row.</param>
        <param name="nth">How many earlier rows the same slot already has, 0 for its
        first.</param>
        <returns>The slot's label, or for a list slot the entry from _list_row_labels,
        falling back to the slot's label when there is none.</returns>
        """
        if slot.kind != 'list':
            return slot.label
        labels = self._list_row_labels(slot)
        return labels[nth] if nth < len(labels) else slot.label

    def _list_series(self, items, how):
        """
        <summary>
        Which list items belong to a series, and their order within it.
        </summary>
        <param name="items">The list's items.</param>
        <param name="how">functional_lists.SEQUELS_LIST to order by list position, or
        SEQUELS_DATE to order films by release date and episodes by season and
        episode.</param>
        <returns>{index: (group, rank)} for every item in a series; standalone items
        are left out. Group is 'set:&lt;setid&gt;' for a film in a Kodi movie set and
        'show:&lt;name&gt;' for an episode.</returns>
        <remarks>
        Films are asked of the library for their set, so a film missing from its set
        in Kodi is standalone here and can come up at any time; that is logged through
        _debug rather than guessed from titles. Plain files and anything else
        are always standalone. A film with no release date sorts after the dated ones
        of its set, and an episode without numbers after the numbered ones. A single
        item of a series is still recorded; it has nothing to hold back.
        </remarks>
        """
        series = {}
        by_date = how == functional_lists.SEQUELS_DATE
        for index, item in enumerate(items):
            spec = functional_lists.play_spec(item) or {}
            if 'movieid' in spec:
                data = json.loads(xbmc.executeJSONRPC(json.dumps({
                    'jsonrpc': '2.0', 'method': 'VideoLibrary.GetMovieDetails',
                    'params': {'movieid': spec['movieid'],
                               'properties': ['setid', 'premiered', 'year']}, 'id': 1
                })))
                details = data.get('result', {}).get('moviedetails') or {}
                setid = int(details.get('setid') or 0)
                if not setid:
                    self._debug('List item "{}" is in no movie set'.format(item.get('label')))
                    continue
                when = details.get('premiered') or str(details.get('year') or '') or '~'
                series[index] = ('set:{}'.format(setid), ((when,) if by_date else ()) + (index,))
            elif item.get('dbtype') == 'episode' and item.get('show'):
                try:
                    numbers = (int(item.get('season')), int(item.get('episode')))
                except (ValueError, TypeError):
                    numbers = (10 ** 6, 10 ** 6)
                series[index] = ('show:{}'.format(str(item['show']).lower()),
                                 (numbers if by_date else ()) + (index,))
        return series

    def _list_choice(self, item, index):
        """
        <summary>
        The play spec, banner line and poster for one list item, with where it sits.
        </summary>
        <param name="item">One list item dict.</param>
        <param name="index">Its position in the list, recorded for the in turn mode.</param>
        <returns>{'spec', 'label', 'art', 'identity', 'index'}; art is the thumb the skin
        captured, which may be ''.</returns>
        """
        return {'spec': functional_lists.play_spec(item),
                'label': functional_lists.item_label(item),
                'art': item.get('thumb') or '',
                'identity': functional_lists.item_identity(item),
                'index': index}

    def _play_list_item(self, choice):
        """
        <summary>
        Play a chosen list item through Player.Open, by library id or by file path.
        </summary>
        <param name="choice">A dict from _resolve_list_item.</param>
        """
        response = xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'method': 'Player.Open',
            'params': {'item': choice['spec']}, 'id': 1
        }))
        self._log('Player.Open list item response: {}'.format(response))

    def _play_slot(self, slot, key=None):
        """
        <summary>
        Play whatever this slot points at, for a sequence, the next unwatched
        item in the list; for a Functional list, the item _resolve_list_item
        chooses; for a favourite, the next unwatched episode (TV) or the
        favourite itself (movie / folder / playlist / etc).
        </summary>
        <param name="slot">The slot to play.</param>
        <param name="key">The occurrence key, so a random list slot plays the item its
        banner named. Every caller has one; it defaults to None only so a list slot
        played without it still plays something.</param>
        """
        if slot.kind == 'list':
            item = self._resolve_list_item(slot, key or occurrence_key(slot, datetime.now()))
            if item:
                self._log('Slot {}: playing "{}" from list "{}" ({})'.format(
                    slot.index + 1, item['label'], slot.list_name, slot.list_mode))
                self._play_list_item(item)
                if slot.list_mode == functional_lists.MODE_TURN:
                    from resources.lib import slots as slot_store
                    slot_store.set_list_progress(slot.id, item['identity'], item['index'])
                    # The published line names the item just played until the
                    # cache is dropped; the next tick then shows the one after.
                    self._next_item_cache.pop(slot.id, None)
                    self._next_rows_cache.pop(slot.id, None)
                    self._next_runtime_cache.pop(slot.id, None)
                if item.get('series'):
                    from resources.lib import slots as slot_store
                    group, identity, position = item['series']
                    slot_store.set_series_progress(slot.id, group, identity, position)
            else:
                self._log('Slot {}: list all watched'.format(slot.index + 1))
                xbmcgui.Dialog().notification(
                    'Engage',
                    '{}, all watched!'.format(slot.label),
                    xbmcgui.NOTIFICATION_INFO, 6000
                )
            return

        if slot.kind == 'sequence':
            item = self._resolve_next_item(slot)
            if item:
                self._log('Slot {}: playing next unwatched "{}"'.format(
                    slot.index + 1, item['label']))
                self._play_item(item)
            else:
                self._log('Slot {}: sequence all watched'.format(slot.index + 1))
                xbmcgui.Dialog().notification(
                    'Engage',
                    '{}, all watched!'.format(slot.label),
                    xbmcgui.NOTIFICATION_INFO, 6000
                )
            return

        episode = self._resolve_episode_info(slot)
        if episode and episode.get('episodeid'):
            self._log('Slot {}: playing next unwatched {}'.format(
                slot.index + 1, self._episode_label(episode)))
            self._play_episode(episode)
        else:
            self._log('Slot {}: no resolvable episode, activating favourite "{}"'.format(
                slot.index + 1, slot.favourite))
            self._activate_favourite(slot.favourite)

    def _apply_prompt_action(self, slot, key, action, snooze_minutes):
        """
        <summary>
        Translate a prompt result into scheduler state changes.
        </summary>
        """
        if action == 'skip':
            return  # GUI wasn't ready; caller leaves the slot un-warned to retry
        now = datetime.now()
        if action == 'open':
            self._play_slot(slot, key)
            self.triggered[key] = True
            self._log('Slot {} opened by user'.format(slot.index + 1))
        elif action == 'snooze':
            self.snoozed[key] = now + timedelta(minutes=snooze_minutes)
            self._log('Slot {} snoozed for {} minutes'.format(slot.index + 1, snooze_minutes))
        elif action == 'queue':
            if any(k == key for _, k in self.queued_next):
                self._log('Slot {} already queued, ignoring'.format(slot.index + 1))
                return
            # Always join the back of the queue, so a second (or third) queued
            # slot waits its turn instead of displacing the one before it.
            self.queued_next.append((slot, key))
            place = len(self.queued_next)
            if xbmc.Player().isPlaying():
                if place == 1:
                    message = '{} queued, starts when current playback ends.'.format(slot.label)
                else:
                    message = '{} queued, number {} in line.'.format(slot.label, place)
                xbmcgui.Dialog().notification(
                    'Engage', message, xbmcgui.NOTIFICATION_INFO, 5000
                )
                self._log('Slot {} queued at position {} for after current playback'.format(
                    slot.index + 1, place))
            else:
                # Nothing is playing, so no playback-end event is coming to
                # drain the queue. Start the head of it now, which is this slot
                # unless earlier ones are still waiting; they keep their place.
                self._log('Queue Next chosen but nothing is playing, starting the queue now')
                self._start_next_queued()
        elif action == 'stop_requeue':
            self._stop_and_requeue(slot, key)
        elif action == 'cancel':
            self.cancelled[key] = True
            self._log('Slot {} cancelled by user'.format(slot.index + 1))

    def _stop_and_requeue(self, slot, key):
        """
        <summary>
        Stop what's playing now, remember it (and its position), play the
        slot, then resume the interrupted item when the slot finishes.
        </summary>
        <param name="slot">The slot to play.</param>
        <param name="key">Its occurrence key.</param>
        <remarks>
        The stop issued here reaches _on_playback_ended like a stop by the user, and
        used to fire whatever was waiting in queued_next, which this slot then
        replaced 800 ms later, losing the queued slot. _stopping_on_purpose is raised
        around the stop so that callback leaves the queue alone; it is lowered again
        after the settle, whether or not the callback came, so a later real stop is
        never swallowed.
        </remarks>
        """
        player = xbmc.Player()
        if player.isPlaying():
            info = self._capture_now_playing()
            if info:
                self.resume_after = info
                self._resume_armed = False
                self._log('Will resume {} at {:.0f}s after slot {}'.format(
                    info.get('describe', '?'), info.get('time', 0), slot.index + 1))
                xbmcgui.Dialog().notification(
                    'Engage',
                    'Will resume your show after {}.'.format(slot.label),
                    xbmcgui.NOTIFICATION_INFO, 5000
                )
            else:
                self._log('Could not capture resume info', xbmc.LOGWARNING)
                self.resume_after = None
            self._stopping_on_purpose = True
            try:
                player.stop()
                xbmc.sleep(800)  # let the stop settle before opening the slot
            finally:
                self._stopping_on_purpose = False
        self._play_slot(slot, key)
        self.triggered[key] = True

    def _capture_now_playing(self):
        """
        <summary>
        Capture the currently-playing item by library identity (so resume
        keeps full metadata + watched tracking) rather than its resolved stream
        URL. Returns a dict for _resume_playback, or None.
        </summary>
        """
        try:
            players = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'method': 'Player.GetActivePlayers', 'id': 1
            }))).get('result', []) or []
            if not players:
                return None
            pid = players[0].get('playerid', 1)

            item = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'method': 'Player.GetItem',
                'params': {'playerid': pid, 'properties': ['file', 'title', 'showtitle']},
                'id': 1
            }))).get('result', {}).get('item', {}) or {}

            props = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'method': 'Player.GetProperties',
                'params': {'playerid': pid, 'properties': ['time']},
                'id': 1
            }))).get('result', {}) or {}

            t = props.get('time', {}) or {}
            seconds = (t.get('hours', 0) * 3600 + t.get('minutes', 0) * 60
                       + t.get('seconds', 0))
            resume_time = max(0.0, seconds - 5)  # back up 5s for context

            info = {'time': resume_time}
            item_type = item.get('type', '')
            item_id = item.get('id', -1)
            # Prefer library identity, plays through the library with full
            # metadata and updates watched state on completion.
            if item_id and item_id > 0 and item_type in ('episode', 'movie', 'musicvideo'):
                info['library'] = {'{}id'.format(item_type): item_id}
                info['describe'] = '{} #{}'.format(item_type, item_id)
            else:
                # Plugin/other (e.g. Jellyfin), replay the item's own path so it
                # goes back through the plugin (which reports watched state),
                # NOT the resolved stream URL from getPlayingFile().
                path = item.get('file') or ''
                if not path:
                    return None
                info['file'] = path
                info['describe'] = item.get('label') or path
            return info
        except Exception as e:
            self._log('capture_now_playing failed: {}'.format(e), xbmc.LOGWARNING)
            return None

    def _on_playback_started(self):
        """
        <summary>
        Called when playback starts. Arms the resume so that the NEXT stop/end
        (i.e. when the slot itself finishes) triggers the resume, not the stop
        we issued on the original item.
        </summary>
        """
        if self.resume_after and not self._resume_armed:
            self._resume_armed = True
            self._log('Resume armed, will restore after this playback ends')

    def _on_playback_ended(self):
        """
        <summary>
        Called by the player monitor when current playback stops/ends.
        </summary>
        <remarks>
        A stop that _stop_and_requeue issued itself is ignored: the slot it is about
        to play is not "what was playing ending", and a queued slot must not be fired
        by it.
        </remarks>
        """
        if self._stopping_on_purpose:
            self._stopping_on_purpose = False
            self._log('Playback stopped by Engage itself, the queue is left alone')
            return
        # Resume-after takes priority over a queued slot.
        if self.resume_after and self._resume_armed:
            info = self.resume_after
            self.resume_after = None
            self._resume_armed = False
            import threading
            threading.Thread(target=self._resume_playback, args=(info,), daemon=True).start()
            return

        self._start_next_queued()

    def _start_next_queued(self):
        """
        <summary>
        Play the slot at the front of the queue, if there is one. The rest
        keep their order and fire as each playback in turn ends.
        </summary>
        """
        if not self.queued_next:
            return
        slot, key = self.queued_next.pop(0)
        self._log('Firing queued slot {} ({} still waiting)'.format(
            slot.index + 1, len(self.queued_next)))
        self._play_slot(slot, key)
        self.triggered[key] = True

    def _resume_playback(self, info):
        """
        <summary>
        Resume a previously-interrupted item, seeking back to its position.
        Runs on its own thread so the player callback isn't blocked.
        </summary>
        <remarks>
        Opens via library id (episode/movie) where possible so the item plays
        with full metadata and watched tracking; otherwise replays its plugin
        path. Falls back to a raw file play only as a last resort.
        </remarks>
        """
        self._log('Resuming interrupted playback: {}'.format(info.get('describe', '?')))

        params = {'item': None}
        if 'library' in info:
            params['item'] = info['library']
            # Kodi saved a bookmark when we stopped the item, so ask for a
            # native resume too. Belt and braces: if the seek below were ever
            # missed, playback still starts near the right spot, not at 0:00.
            params['options'] = {'resume': True}
        elif info.get('file'):
            params['item'] = {'file': info['file']}
        else:
            return

        response = xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'method': 'Player.Open',
            'params': params, 'id': 1
        }))
        self._log('Resume Player.Open response: {}'.format(response))

        # Wait for the player to be genuinely up, not just queued.
        # isPlaying() goes true before the stream is seekable and a seek
        # issued in that window is silently dropped, which played the item
        # from scratch. A real duration means the demuxer is ready. Plugin
        # streams (e.g. Jellyfin) can take a while to resolve, hence 20s.
        player = xbmc.Player()
        for _ in range(200):
            if player.isPlaying():
                try:
                    if player.getTotalTime() > 0:
                        break
                except RuntimeError:
                    pass
            xbmc.sleep(100)
        else:
            self._log('Resume: player never became ready, no seek',
                      xbmc.LOGWARNING)
            return

        # Seek and confirm it landed; retry while the player warms up.
        target = info.get('time', 0)
        if target <= 0:
            return
        for _ in range(10):
            try:
                player.seekTime(target)
                xbmc.sleep(500)
                if not player.isPlaying():
                    return
                if abs(player.getTime() - target) < 4:
                    self._log('Resumed and sought to {:.0f}s'.format(target))
                    return
            except Exception as e:
                self._log('Resume seek attempt failed: {}'.format(e),
                          xbmc.LOGWARNING)
                xbmc.sleep(500)
        self._log('Resume seek did not stick after retries', xbmc.LOGWARNING)

    def _recent_watch_hours(self):
        """
        <summary>
        The 'skip a slot watched in the last N hours' setting, 0 meaning off.
        </summary>
        <returns>Hours as an int, never negative.</returns>
        <remarks>
        Read on every use so a change in the settings screen applies to the next tick
        without a restart. A failed read falls back to RECENT_WATCH_FALLBACK_HOURS.
        </remarks>
        """
        try:
            hours = xbmcaddon.Addon(ADDON_ID).getSettingInt('recent_watch_hours')
        except Exception:
            hours = RECENT_WATCH_FALLBACK_HOURS
        return max(0, int(hours))

    def _watched_recently(self, slot, now):
        """
        <summary>
        True when something this slot covers was played within the recent watch window,
        meaning the slot has already been seen to and should stay quiet today.
        </summary>
        <param name="slot">The slot about to prompt or auto-play.</param>
        <param name="now">The tick's current time.</param>
        <returns>True to skip the slot for today, False to carry on as normal.</returns>
        <remarks>
        Relies on the library's lastplayed stamp, which Kodi writes when playback of a
        library item stops, whether or not it ran to the end. Anything the library cannot
        answer for (a folder, playlist, plugin or plain file favourite) counts as not
        watched, so the banner still appears. A lookup failure is logged and treated the
        same way: a missed skip costs one banner, a missed prompt costs the show.
        </remarks>
        """
        hours = self._recent_watch_hours()
        if hours <= 0:
            return False
        since = now - timedelta(hours=hours)
        try:
            last = self._last_played_for_slot(slot, since)
        except Exception as e:
            self._log('Slot {}: recent watch lookup failed, prompting anyway: {}'.format(
                slot.index + 1, e), xbmc.LOGWARNING)
            return False
        if last is None or last < since:
            return False
        self._log('Slot {}: "{}" was played at {}, inside the {}h recent watch window, skipping today'.format(
            slot.index + 1, slot.label, last.strftime('%H:%M on %d/%m/%Y'), hours))
        return True

    def _last_played_for_slot(self, slot, since):
        """
        <summary>
        The most recent lastplayed stamp among the library items this slot covers.
        </summary>
        <param name="slot">The slot to look up.</param>
        <param name="since">Start of the recent watch window. A sequence stops at the
        first item stamped at or after it, because the caller only needs to know one
        exists.</param>
        <returns>A datetime, or None when nothing was ever played or the slot is not
        something the library can answer for.</returns>
        <remarks>
        A sequence is checked item by item in list order, and so is a Functional list,
        counting only its library movies and episodes, a whole show or season row
        through its episodes. A TV show favourite looks at
        every episode of the show, because the episode watched early is not the one the
        banner would offer next. A movie favourite stored as a videodb path is looked up
        by id. A favourite that is a folder, playlist, plugin or plain file path is not
        covered and returns None.
        </remarks>
        """
        if slot.kind == 'sequence':
            return self._last_played_in_items(slot.items, since)
        if slot.kind == 'list':
            refs = [functional_lists.library_ref(it) for it in self._flat_items(slot.list_items)]
            return self._last_played_in_items([r for r in refs if r], since)
        favs = self._get_favourites_raw()
        fav = next((f for f in favs if f.get('title', '').lower() == slot.favourite.lower()), None)
        if not fav:
            return None
        tvshowid = self._extract_tvshowid(fav)
        if tvshowid is not None:
            return self._last_played_in_show(tvshowid)
        movieid = self._extract_movieid(fav)
        if movieid is not None:
            return self._last_played_of_item({'type': 'movie', 'id': movieid})
        return None

    def _last_played_in_show(self, tvshowid):
        """
        <summary>
        The newest lastplayed stamp across every episode of a TV show.
        </summary>
        <param name="tvshowid">The library id of the show.</param>
        <returns>A datetime, or None when no episode has been played.</returns>
        <remarks>
        Fetches every episode with only the lastplayed property and takes the maximum
        here, rather than asking Kodi to sort, so the answer does not depend on how
        unplayed episodes sort. Even a long running show is a few tens of kilobytes, and
        this runs a handful of times a day per slot at most.
        </remarks>
        """
        response = xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'method': 'VideoLibrary.GetEpisodes',
            'params': {'tvshowid': tvshowid, 'properties': ['lastplayed']},
            'id': 1
        }))
        episodes = json.loads(response).get('result', {}).get('episodes', []) or []
        stamps = [parse_kodi_datetime(e.get('lastplayed')) for e in episodes]
        stamps = [s for s in stamps if s is not None]
        return max(stamps) if stamps else None

    def _last_played_of_item(self, item):
        """
        <summary>
        The lastplayed stamp of one library movie or episode.
        </summary>
        <param name="item">A dict with 'type' ('movie' or 'episode') and the library 'id'.</param>
        <returns>A datetime, or None when the item is missing from the library or unplayed.</returns>
        """
        if item.get('type') == 'episode':
            method, id_key, result_key = 'VideoLibrary.GetEpisodeDetails', 'episodeid', 'episodedetails'
        else:
            method, id_key, result_key = 'VideoLibrary.GetMovieDetails', 'movieid', 'moviedetails'
        response = xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'method': method,
            'params': {id_key: item['id'], 'properties': ['lastplayed']}, 'id': 1
        }))
        details = json.loads(response).get('result', {}).get(result_key) or {}
        return parse_kodi_datetime(details.get('lastplayed'))

    def _last_played_in_items(self, items, since):
        """
        <summary>
        The newest lastplayed stamp among a sequence's or a list's library items,
        stopping at the first one inside the window.
        </summary>
        <param name="items">{'type', 'id'} dicts: a sequence's items, or a list slot's
        items converted by functional_lists.library_ref.</param>
        <param name="since">Start of the recent watch window.</param>
        <returns>A datetime, or None when no item has been played.</returns>
        <remarks>
        One JSON-RPC call per item until a hit, so a long binge order that has not been
        touched costs one call per item. Items without an id are skipped, as they are
        everywhere else.
        </remarks>
        """
        newest = None
        for entry in items:
            if not entry.get('id'):
                continue
            stamp = self._last_played_of_item(entry)
            if stamp is None:
                continue
            if stamp >= since:
                return stamp
            if newest is None or stamp > newest:
                newest = stamp
        return newest

    def _handle_warning(self, slot, scheduled_dt, key=None):
        """
        <summary>
        Show the Engage banner prompt. Returns (action, snooze_minutes).
        </summary>
        <param name="slot">The slot that is due.</param>
        <param name="scheduled_dt">When it is due, for the countdown and the toast.</param>
        <param name="key">The occurrence key, so a random list slot's banner names the
        item that will then play.</param>
        <remarks>
        action is one of: 'open', 'snooze', 'queue', 'cancel'. (The startup
        GUI-readiness wait is handled once in _wait_for_gui; we deliberately do
        NOT gate on the home window here, that would wrongly defer prompts
        whenever the user is browsing any menu.)

        The banner layout is resolved here, each time, from the slot's own choice
        and the master setting (slots.resolve_banner_style), so changing the master
        in the settings dialog changes every slot left on 'default' from the next
        banner on, without touching slots.json.
        </remarks>
        """
        from resources.lib.prompt import show_engage_prompt
        from resources.lib import slots as slot_store
        addon_path = xbmcaddon.Addon(ADDON_ID).getAddonInfo('path')
        banner_style = slot_store.resolve_banner_style(
            slot.banner_style, slot_store.master_banner_style())

        # Work out the sub-line + poster for the banner, fresh from the library
        # so the banner never offers an episode that was watched since the
        # publisher last looked.
        episode_label, thumb = self._describe_slot(slot, key)

        # Brief notification so the user knows something just popped.
        name = slot.label if not episode_label else '{} ({})'.format(slot.label, episode_label)
        seconds_left = (scheduled_dt - datetime.now()).total_seconds()
        if seconds_left >= 0:
            minutes_left = max(1, int(round(seconds_left / 60)))
            plural = '' if minutes_left == 1 else 's'
            notif_msg = '{} starts in {} minute{}.'.format(name, minutes_left, plural)
        else:
            notif_msg = '{} should have started at {}.'.format(
                name, scheduled_dt.strftime('%H:%M'))
        xbmcgui.Dialog().notification(
            'Engage', notif_msg, xbmcgui.NOTIFICATION_INFO, 4000
        )

        # Only the Full Monty shows the synopsis and the rest, so only it pays
        # for the extra library lookup.
        details = None
        if banner_style == slot_store.BANNER_FULLMONTY:
            details = self._banner_details(slot, key)

        action, snooze_minutes = show_engage_prompt(
            addon_path, slot.label, episode_label, scheduled_dt, thumb,
            slot.timeout_action, banner_style, details
        )
        self._log('Prompt for slot {} ({} banner) returned action={}, snooze={}m'.format(
            slot.index + 1, banner_style, action, snooze_minutes))
        return action, snooze_minutes

    # What each kind of library item is asked for on the Full Monty banner.
    FULL_MONTY_QUERIES = {
        'movie': ('VideoLibrary.GetMovieDetails', 'movieid', 'moviedetails',
                  ['plot', 'art', 'year', 'runtime', 'genre', 'rating', 'mpaa', 'tagline']),
        'episode': ('VideoLibrary.GetEpisodeDetails', 'episodeid', 'episodedetails',
                    ['plot', 'art', 'runtime', 'rating', 'firstaired']),
        'tvshow': ('VideoLibrary.GetTVShowDetails', 'tvshowid', 'tvshowdetails',
                   ['plot', 'art', 'year', 'genre', 'rating', 'mpaa']),
    }

    def _banner_item_ref(self, slot, key=None):
        """
        <summary>
        The library item a slot's banner is about, as (kind, id).
        </summary>
        <param name="slot">The slot that is due.</param>
        <param name="key">The occurrence key, so a random list slot names the item
        its banner already picked.</param>
        <returns>('movie', id), ('episode', id) or ('tvshow', id), or None for a
        favourite that is not a library movie or show, an item played by its file
        path, or a finished sequence.</returns>
        <remarks>
        The same resolvers _describe_slot uses, in the same order, so the details
        are those of the item the banner names. A TV favourite with every episode
        watched falls back to the show itself.
        </remarks>
        """
        if slot.kind == 'list':
            nxt = self._resolve_list_item(slot, key)
            spec = (nxt or {}).get('spec') or {}
            if spec.get('movieid'):
                return ('movie', spec['movieid'])
            if spec.get('episodeid'):
                return ('episode', spec['episodeid'])
            return None
        if slot.kind == 'sequence':
            nxt = self._resolve_next_item(slot)
            return (nxt['type'], nxt['id']) if nxt else None
        favs = self._get_favourites_raw()
        fav = next((f for f in favs if f.get('title', '').lower() == slot.favourite.lower()), None)
        if not fav:
            return None
        tvshowid = self._extract_tvshowid(fav)
        if tvshowid is not None:
            episode = self._get_next_unwatched(tvshowid)
            if episode and episode.get('episodeid'):
                return ('episode', episode['episodeid'])
            return ('tvshow', tvshowid)
        movieid = self._extract_movieid(fav)
        return ('movie', movieid) if movieid is not None else None

    def _banner_details(self, slot, key=None):
        """
        <summary>
        The Full Monty's synopsis, fanart, information line and tagline for a slot.
        </summary>
        <param name="slot">The slot that is due.</param>
        <param name="key">The occurrence key, passed to _banner_item_ref.</param>
        <returns>What prompt.full_monty_details returns, or an empty dict when the
        item is not in the library or the lookup fails.</returns>
        <remarks>
        One or two JSON-RPC calls, made only when the Full Monty is the banner
        being shown. Any failure is logged and gives an empty dict, so the banner
        still opens with its title, poster and buttons.
        </remarks>
        """
        from resources.lib.prompt import full_monty_details
        try:
            ref = self._banner_item_ref(slot, key)
            if not ref:
                return {}
            kind, item_id = ref
            method, id_key, result_key, properties = self.FULL_MONTY_QUERIES[kind]
            response = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'method': method,
                'params': {id_key: item_id, 'properties': properties}, 'id': 1
            })))
            details = response.get('result', {}).get(result_key)
            return full_monty_details(kind, details) if details else {}
        except Exception as e:
            self._log('Full Monty details for slot {} failed: {}'.format(slot.index + 1, e),
                      xbmc.LOGWARNING)
            return {}

    def _describe_slot(self, slot, key=None):
        """
        <summary>
        The episode line and poster for a slot: the next unwatched episode for a TV
        favourite, the next unwatched item for a sequence or an in order list, the
        item picked for a random list, or the favourite's own thumbnail for a plain
        favourite.
        </summary>
        <param name="slot">The slot to describe.</param>
        <param name="key">The occurrence key. Only a random list slot uses it: with a
        key it picks and remembers the item, without one it answers 'Random pick'.</param>
        <returns>A tuple (episode_label, thumb). episode_label is '' for a movie, folder
        or other non-TV favourite and 'All watched' for a finished sequence; thumb may
        be ''.</returns>
        <remarks>
        Always asks the library, one to three JSON-RPC calls. The banner calls it at
        prompt time; the next-slot publisher calls it through _next_item_cached so the
        skin line is formatted exactly like the banner's without querying every tick.
        </remarks>
        """
        if slot.kind == 'list':
            nxt = self._resolve_list_item(slot, key)
            if nxt:
                episode_label, thumb = nxt['label'], nxt['art']
            elif slot.list_mode in functional_lists.RANDOM_MODES:
                episode_label, thumb = 'Random pick', ''
            else:
                episode_label, thumb = 'All watched', ''
        elif slot.kind == 'sequence':
            nxt = self._resolve_next_item(slot)
            episode_label = nxt['label'] if nxt else 'All watched'
            thumb = nxt.get('art', '') if nxt else ''
        else:
            episode = self._resolve_episode_info(slot)
            episode_label = self._episode_label(episode)
            thumb = self._episode_art(episode) if episode else self._favourite_thumb(slot.favourite)
        return episode_label, thumb

    def _slot_signature(self, slot):
        """
        <summary>
        What a slot points at, for telling a cached description from a stale one.
        </summary>
        <param name="slot">The slot.</param>
        <returns>A hashable tuple of its kind, favourite, sequence type and item ids,
        and for a list slot its name, mode and the identity and skip list of every
        item in the list.</returns>
        """
        return (slot.kind, slot.favourite, slot.seq_type,
                tuple(entry.get('id') for entry in slot.items),
                slot.list_name, slot.list_mode,
                tuple((functional_lists.item_identity(it), tuple(it.get('skip') or ()))
                      for it in slot.list_items))

    def _next_item_cached(self, slot):
        """
        <summary>
        _describe_slot with a per-slot cache, for the next-slot publisher.
        </summary>
        <param name="slot">The slot to describe.</param>
        <returns>The same (episode_label, thumb) tuple _describe_slot returns.</returns>
        <remarks>
        The cache entry carries a signature of what the slot points at (kind, favourite,
        sequence type and item ids, and for a list slot its name, mode and the identity
        of every item in it), so an edited slot or an edited list is re-resolved even if the save
        notification that normally clears the cache was missed. A lookup failure is
        logged and cached as an empty line rather than retried every tick; the next
        library change or slot save clears it. The banner never uses this cache.
        </remarks>
        """
        signature = self._slot_signature(slot)
        cached = self._next_item_cache.get(slot.id)
        if cached is not None and cached[0] == signature:
            return cached[1], cached[2]
        try:
            episode_label, thumb = self._describe_slot(slot)
        except Exception as e:
            self._log('Slot {}: next item lookup for the skin failed: {}'.format(
                slot.index + 1, e), xbmc.LOGWARNING)
            episode_label, thumb = '', ''
        self._next_item_cache[slot.id] = (signature, episode_label, thumb)
        return episode_label, thumb

    def _catchup_hours(self):
        """
        <summary>
        The catch-up window setting in hours, 0 meaning off.
        </summary>
        <returns>Hours as an int, never negative; CATCHUP_FALLBACK_HOURS when the
        setting cannot be read.</returns>
        """
        try:
            hours = xbmcaddon.Addon(ADDON_ID).getSettingInt('catchup_hours')
        except Exception:
            hours = CATCHUP_FALLBACK_HOURS
        return max(0, int(hours))

    def _next_occurrence(self, slot, now):
        """
        <summary>
        The next occurrence of a slot that the scheduler has not yet dealt with.
        </summary>
        <param name="slot">An enabled slot.</param>
        <param name="now">The current time.</param>
        <returns>A tuple (due, key) with the occurrence's datetime and its tracker key,
        or (None, None) when there is none within NEXT_LOOKAHEAD_DAYS.</returns>
        <remarks>
        The first entry of _occurrences, which holds the rules.
        </remarks>
        """
        found = self._occurrences(slot, now)
        return found[0] if found else (None, None)

    def _occurrences(self, slot, now):
        """
        <summary>
        Every occurrence of a slot within the look ahead window that the scheduler has
        not yet dealt with, earliest first.
        </summary>
        <param name="slot">An enabled slot.</param>
        <param name="now">The current time.</param>
        <returns>A list of (due, key) tuples, the occurrence's datetime and its tracker
        key, empty when there is none within NEXT_LOOKAHEAD_DAYS.</returns>
        <remarks>
        Walks yesterday, today and the next NEXT_LOOKAHEAD_DAYS days, keeping each day
        Slot.runs_on accepts, at the slot's hour and minute. So a monthly or yearly slot
        appears only once its day is within the look ahead window, as _get_scheduled_dt
        would see it. An occurrence is skipped when its key is in triggered or
        cancelled, so a slot played, skipped as recently watched or cancelled today
        does not show as next. An occurrence whose time has passed is still offered
        while the scheduler would still prompt for it: within the catch-up window, or
        two minutes when that is off, or while a snooze taken on it is running. Beyond
        that it is gone and the following occurrence is next. Yesterday is walked for
        the same reason _due_occurrences looks at it: a late slot inside the catch-up
        window is still the next thing to happen after midnight.
        </remarks>
        """
        hours = self._catchup_hours()
        if hours > 0:
            live_for = timedelta(hours=hours)
        else:
            live_for = timedelta(minutes=NEXT_GRACE_MINUTES)
        found = []
        for offset in range(-1, NEXT_LOOKAHEAD_DAYS + 1):
            day = (now + timedelta(days=offset)).date()
            if not slot.runs_on(day):
                continue
            due = datetime(day.year, day.month, day.day, slot.hour, slot.minute)
            key = occurrence_key(slot, due)
            if key in self.triggered or key in self.cancelled:
                continue
            if due < now and now > due + live_for and key not in self.snoozed:
                continue
            found.append((due, key))
        return found

    def _next_state(self, slot, due, key, now):
        """
        <summary>
        Where an occurrence stands for the skin's State property.
        </summary>
        <param name="slot">The slot.</param>
        <param name="due">The occurrence's datetime.</param>
        <param name="key">Its tracker key.</param>
        <param name="now">The current time.</param>
        <returns>'snoozed' while a snooze is running, 'overdue' once the time has
        passed, 'warning' inside the warning window, otherwise 'upcoming'.</returns>
        """
        if key in self.snoozed:
            return 'snoozed'
        if now > due:
            return 'overdue'
        if now >= due - timedelta(minutes=slot.warning):
            return 'warning'
        return 'upcoming'

    def _publish_next(self, reason, slots=None):
        """
        <summary>
        Publish the next slot due as Engage.Next.* properties on the Home window and
        the next NEXT_LIST_COUNT occurrences as Engage.Next.<n>.* rows, or clear them
        all when no enabled slot has an occurrence coming up.
        </summary>
        <param name="reason">Why this publish is happening, for the log.</param>
        <param name="slots">The enabled Slot list when the caller already has it; read
        afresh from the store otherwise.</param>
        <remarks>
        Called at the end of every tick, after a slot save and after a library change.
        Does nothing until _wait_for_gui has finished, so Kodi's start-up is never
        touched. Every occurrence of every enabled slot within the look ahead window
        is gathered and sorted by due time, slots due at the same minute keeping their
        list order. The earliest is the single set: Label is the slot's label, Episode
        the banner's episode line (empty for a non-TV favourite), When the words from
        when_label; Day, Date (dd/mm/yyyy), Time (HH:MM), Thumb, SlotId, State and
        Count (enabled slots) are extras. The first NEXT_LIST_COUNT occurrences are the
        numbered rows, each with its own Label and When and no episode line, so a
        skin's list shows titles only, plus Due and Runtime (see _row_runtime) for a
        skin that works out queued start times; a number with no occurrence is cleared. A row's
        Label is the slot's label, except for a Functional list slot, where it is the
        list's name for a random slot and the item due to play for an in order one
        (see _row_label), so only list slots ask the library for the rows. Each property is written only
        when its value differs from what the window already holds, see
        _write_next_properties. Any failure is logged and swallowed: the skin block is
        a convenience and must never cost a prompt.
        </remarks>
        """
        if not self._gui_ready:
            return
        with self._publish_lock:
            try:
                now = datetime.now()
                if slots is None:
                    slots = self._reload_slots()
                # sorted() is stable, so occurrences due at the same minute keep
                # their slot list order, as the single set always has.
                upcoming = sorted(
                    ((due, key, slot) for slot in slots for due, key in self._occurrences(slot, now)),
                    key=lambda entry: entry[0])
                if not upcoming:
                    self._write_next_properties({}, reason)
                    return
                due, key, slot = upcoming[0]
                episode_label, thumb = self._next_item_cached(slot)
                values = {
                    'Label': slot.label,
                    'Episode': episode_label,
                    'When': when_label(due, now),
                    'Day': DAY_NAMES[due.weekday()],
                    'Date': '{:02d}/{:02d}/{:04d}'.format(due.day, due.month, due.year),
                    'Time': '{:02d}:{:02d}'.format(due.hour, due.minute),
                    'Thumb': thumb,
                    'SlotId': str(slot.id),
                    'State': self._next_state(slot, due, key, now),
                    'Count': str(len(slots)),
                }
                shown = {}
                for number, (row_due, row_key, row_slot) in enumerate(upcoming[:NEXT_LIST_COUNT], start=1):
                    nth = shown.get(row_slot.id, 0)
                    shown[row_slot.id] = nth + 1
                    values['{}.Label'.format(number)] = self._row_label(row_slot, nth)
                    values['{}.When'.format(number)] = when_label(row_due, now)
                    values['{}.Due'.format(number)] = row_due.strftime('%Y-%m-%dT%H:%M')
                    seconds = self._row_runtime(row_slot, nth, row_key)
                    values['{}.Runtime'.format(number)] = str(seconds) if seconds else ''
                self._write_next_properties(values, reason)
            except Exception:
                import traceback
                self._log('Publishing the next slot failed:\n{}'.format(
                    traceback.format_exc()), xbmc.LOGWARNING)

    def _write_next_properties(self, values, reason):
        """
        <summary>
        Bring the Engage.Next.* Home window properties in line with values, touching
        only the ones that differ.
        </summary>
        <param name="values">Property name (without the prefix) to value, numbered
        rows as '3.Label' and so on. A missing or empty value clears that property; an
        empty dict clears them all, numbered rows included.</param>
        <param name="reason">Why, for the log line.</param>
        <remarks>
        The window's current value is read back and compared before every write,
        rather than trusting a cache, because bursts of redundant Home window writes
        are implicated in a Kodi start-up crash seen by the Functional skin's own
        service and this must stay quiet when nothing has changed. Properties are
        visited in next_property_order: the single set Label last and the numbered
        rows from the highest down to 1, each Label last, when publishing; the reverse,
        Label first and row 1 first, when clearing. So a skin's block, keyed on Label,
        and its list, keyed on row 1 and on each row's own label, never show a half
        updated set. A change to any Label or When is logged at info, the rest at debug.
        </remarks>
        """
        window = xbmcgui.Window(HOME_WINDOW_ID)
        changed = []
        for name in next_property_order(values):
            prop = NEXT_PROPERTY_PREFIX + name
            value = values.get(name) or ''
            if window.getProperty(prop) == value:
                continue
            if value:
                window.setProperty(prop, value)
            else:
                window.clearProperty(prop)
            changed.append(name)
        if not changed:
            return
        summary = 'Next slot for skins ({}): {}'.format(
            reason, ', '.join('{}={!r}'.format(n, values.get(n) or '') for n in changed))
        if any(name.endswith(('Label', 'When')) for name in changed):
            self._log(summary)
        else:
            self._debug(summary)

    def _clear_next(self):
        """
        <summary>
        Clear every Engage.Next.* property, for when the service stops.
        </summary>
        <remarks>
        Runs whatever state the GUI is in, because a skin must not keep showing a slot
        from a service that is no longer there. Failures are swallowed: Kodi may be
        tearing down the GUI at the same time.
        </remarks>
        """
        try:
            with self._publish_lock:
                self._write_next_properties({}, 'service stopping')
        except Exception as e:
            self._log('Could not clear the next slot properties: {}'.format(e), xbmc.LOGWARNING)

    def _on_notification(self, sender, method, data):
        """
        <summary>
        React to a Kodi announcement: a slot save from the Manage slots dialog, or a
        video library change, both of which can move the next slot or its episode line.
        </summary>
        <param name="sender">Who announced it; ADDON_ID for the dialog's save.</param>
        <param name="method">'Other.' + SLOTS_SAVED_MESSAGE for a save, or a
        VideoLibrary.* method.</param>
        <param name="data">The announcement's JSON payload as a string.</param>
        <remarks>
        Every handled event clears the episode cache. A save, a finished scan or clean,
        and an OnUpdate carrying a playcount (an episode or film just marked watched)
        republish straight away on a short-lived thread, so the announcer is not held
        up and no GUI lock is taken from its thread. Other OnUpdate and OnRemove events
        arrive once per item during a scan, so they only clear the cache and leave the
        republish to the next tick.
        </remarks>
        """
        if sender == ADDON_ID and method == 'Other.' + SLOTS_SAVED_MESSAGE:
            self._next_item_cache.clear()
            self._next_rows_cache.clear()
            self._next_runtime_cache.clear()
            self._picks_checked.clear()
            self._publish_async('slots saved')
            return
        if method in ('VideoLibrary.OnScanFinished', 'VideoLibrary.OnCleanFinished'):
            self._next_item_cache.clear()
            self._next_rows_cache.clear()
            self._next_runtime_cache.clear()
            self._picks_checked.clear()
            self._publish_async(method)
        elif method in ('VideoLibrary.OnUpdate', 'VideoLibrary.OnRemove'):
            self._next_item_cache.clear()
            self._next_rows_cache.clear()
            self._next_runtime_cache.clear()
            self._picks_checked.clear()
            if method == 'VideoLibrary.OnUpdate' and '"playcount"' in (data or ''):
                self._publish_async('watched state changed')

    def _on_settings_changed(self):
        """
        <summary>
        Start or stop the add-on API when its setting is changed.
        </summary>
        <remarks>
        Ignored until the GUI is up: run() starts the API then, and must be the first
        to write the Home window (see __init__).
        </remarks>
        """
        if self._gui_ready:
            self._sync_api()

    def _sync_api(self):
        """
        <summary>
        Bring the add-on API in line with its setting (api_server.sync).
        </summary>
        <remarks>
        Imported here rather than at the top so the scheduler's tests need no HTTP
        server. Any failure is logged and swallowed: the API is an extra and must never
        stop slots firing.
        </remarks>
        """
        try:
            from resources.lib import api_server
            api_server.sync()
        except Exception:
            import traceback
            self._log('Add-on API could not start:\n{}'.format(traceback.format_exc()), xbmc.LOGERROR)

    def _stop_api(self):
        """
        <summary>
        Stop the add-on API if it is running, logging rather than raising on failure.
        </summary>
        """
        try:
            from resources.lib import api_server
            api_server.stop()
        except Exception:
            import traceback
            self._log('Add-on API did not stop cleanly:\n{}'.format(traceback.format_exc()), xbmc.LOGWARNING)

    def _publish_async(self, reason):
        """
        <summary>
        Run _publish_next on its own daemon thread.
        </summary>
        <param name="reason">Passed through to _publish_next.</param>
        """
        threading.Thread(target=self._publish_next, args=(reason,), daemon=True).start()

    def _check_test_mode(self):
        """
        <summary>
        Honour the one-shot test_mode setting: when it is on, clear it and play every enabled
        slot in turn, two seconds apart, with a [TEST] toast for each.
        </summary>
        <remarks>
        The setting is cleared before anything plays, so one toggle in the settings screen
        fires exactly once. A Kodi shutdown mid-run stops the loop.
        </remarks>
        """
        addon = xbmcaddon.Addon(ADDON_ID)
        if not addon.getSettingBool('test_mode'):
            return

        self._log('Test mode activated, triggering all enabled slots')
        addon.setSettingBool('test_mode', False)

        slots = self._reload_slots()
        for slot in slots:
            xbmcgui.Dialog().notification(
                'Engage',
                '[TEST] {}, activating favourite: {}'.format(slot.label, slot.favourite),
                xbmcgui.NOTIFICATION_INFO,
                3000
            )
            self._play_slot(slot)
            if self.monitor.waitForAbort(2):
                return

    def _wait_for_gui(self):
        """
        <summary>
        Give Kodi a good rest before we ever show a prompt.
        </summary>
        <remarks>
        At start='login' the service fires within milliseconds of login, while
        the skin/home window is still loading. A WindowXMLDialog shown that
        early renders but never gets an input grab or render loop, it freezes.
        So we wait for the home window to appear AND ensure at least a 20s
        settle before the first check. (As Father Ted would say, the GUI's
        just resting.)
        </remarks>
        """
        # Wait for the home window to become visible (up to ~60s as a backstop).
        for _ in range(60):
            if self.monitor.abortRequested():
                return
            if xbmc.getCondVisibility('Window.IsVisible(home)'):
                break
            if self.monitor.waitForAbort(1):
                return
        # A solid rest so the skin, library and dialogs are fully ready.
        self._log('GUI is up, resting {}s before first check'.format(STARTUP_REST_SECONDS))
        self.monitor.waitForAbort(STARTUP_REST_SECONDS)
        # Only now may the Home window be written to (see __init__).
        self._gui_ready = True

    def run(self):
        """
        <summary>
        The service main loop: wait for the GUI, then call _tick every poll_interval seconds
        until Kodi shuts down.
        </summary>
        <remarks>
        poll_interval is read from settings on every pass, a value under 5 falling back to
        30. An exception inside a tick is logged with its traceback and the loop carries
        on, because the service is expected to run for days between restarts. However
        the loop ends, the Engage.Next.* Home window properties are cleared on the way
        out so a skin does not keep showing a slot nobody is watching for. The add-on API
        starts once the GUI is up, for the same Home window reason, and stops on the way
        out.
        </remarks>
        """
        self._log('Scheduler loop starting')
        self._wait_for_gui()
        if self._gui_ready:
            self._sync_api()

        try:
            while not self.monitor.abortRequested():
                # Any error in a tick must never kill the service, log it and
                # keep polling. A media centre can run for days between restarts.
                poll_interval = 30
                try:
                    addon = xbmcaddon.Addon(ADDON_ID)
                    poll_interval = addon.getSettingInt('poll_interval')
                    if poll_interval < 5:
                        poll_interval = 30
                    self._tick()
                except Exception:
                    import traceback
                    self._log('Scheduler tick failed:\n{}'.format(traceback.format_exc()), xbmc.LOGERROR)

                if self.monitor.waitForAbort(poll_interval):
                    break
        finally:
            self._stop_api()
            self._clear_next()

        self._log('Scheduler loop ended')

    def _tick(self):
        """
        <summary>
        One pass over all slots, then a refresh of the published next slot. Called every
        poll_interval seconds.
        </summary>
        """
        self._check_test_mode()

        now = datetime.now()
        slots = self._reload_slots()
        self._debug('Checking {} enabled slots at {}'.format(len(slots), now.strftime('%H:%M:%S')))

        for slot in slots:
            try:
                self._process_slot(slot, now)
            except Exception:
                import traceback
                self._log('Slot {} processing failed:\n{}'.format(
                    slot.index + 1, traceback.format_exc()), xbmc.LOGERROR)

        # After the pass, so a snooze that ran out since the last tick has been
        # acted on before its entry could be dropped as stale.
        self._purge_trackers(now)

        # Last, so whatever this tick played, snoozed or cancelled is reflected.
        self._publish_next('tick', slots)

    def _purge_trackers(self, now):
        """
        <summary>
        Drop per occurrence state the scheduler can no longer act on.
        </summary>
        <param name="now">The tick's time.</param>
        <remarks>
        Today's keys always stay. Yesterday's stay while the catch-up window can still
        reach an occurrence from yesterday, which is until midnight plus the window,
        so a catch-up banner answered at 00:30 is not shown again at 00:31. A snooze
        stays while its expiry is ahead whatever its date, so a snooze taken at 23:30
        for an hour still comes back at 00:30; the snooze branch of _process_occurrence
        removes it once it has been acted on. A random list pick stays while its key
        is still snoozed, so the banner that comes back names the same film. Before
        this everything not from today went at the first tick after midnight. Runs
        after the slot pass, not before it, so an expired snooze is brought back by
        _process_occurrence before this could count it as spent.
        </remarks>
        """
        keep_dates = {now.strftime('%Y-%m-%d')}
        hours = self._catchup_hours()
        if hours > 0:
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if now < midnight + timedelta(hours=hours):
                keep_dates.add((now - timedelta(days=1)).strftime('%Y-%m-%d'))
        for key, until in list(self.snoozed.items()):
            if key[:10] not in keep_dates and until <= now:
                del self.snoozed[key]
        for tracker in (self.triggered, self.warned, self.cancelled):
            for key in [k for k in tracker if k[:10] not in keep_dates]:
                del tracker[key]
        # COMING-UP-PICK-AHEAD: a pick for a day still to come stays; only one
        # whose day has gone (and is not snoozed) is dropped, and the file then
        # rewritten. ISO dates sort as strings.
        earliest = min(keep_dates)
        with self._picks_lock:
            spent = [k for k in self._list_picks if k[:10] < earliest and k not in self.snoozed]
            for key in spent:
                del self._list_picks[key]
            if spent:
                from resources.lib import slots as slot_store
                slot_store.set_list_picks(self._list_picks)

    def _process_slot(self, slot, now):
        """
        <summary>
        Evaluate a single slot against the current time.
        </summary>
        <param name="slot">An enabled slot from _reload_slots.</param>
        <param name="now">The tick's time.</param>
        <remarks>
        Every occurrence _due_occurrences returns is put through _process_occurrence,
        yesterday's (when still live) before today's.
        </remarks>
        """
        for scheduled_dt, key in self._due_occurrences(slot, now):
            self._process_occurrence(slot, now, scheduled_dt, key)

    def _process_occurrence(self, slot, now, scheduled_dt, key):
        """
        <summary>
        Evaluate one occurrence of a slot against the current time: open the warning
        window, bring back a snooze, catch up a missed one, or play it.
        </summary>
        <param name="slot">The slot.</param>
        <param name="now">The tick's time.</param>
        <param name="scheduled_dt">When this occurrence is due.</param>
        <param name="key">Its tracker key, from occurrence_key.</param>
        <remarks>
        A slot skipped because its show was watched recently is recorded in triggered,
        the same tracker a played slot uses, so it stays quiet until its key is purged.
        An autoopen slot ("Skip the banner, just play at the set time") gets no banner
        in its warning window: nothing happens until the set time, when it plays. Any
        queued_next entry carrying its key is dropped first, because a banner that was
        left to time out to Queue Next would otherwise play the slot a second time
        when the current playback ends. A banner for an occurrence reached with no
        warning window (warning 0, or Kodi started right at the time) counts down
        LATE_BANNER_SECONDS from now, so its timeout action can still fire; the
        catch-up banner for an occurrence hours late keeps waiting for the user.
        </remarks>
        """
        if key in self.triggered or key in self.cancelled:
            return

        warning_dt = scheduled_dt - timedelta(minutes=slot.warning)
        if now < warning_dt:
            return  # nothing happens before the warning window opens
        if slot.autoopen and now < scheduled_dt:
            return  # no banner for a slot that plays itself at the set time

        snooze_until = self.snoozed.get(key)

        # Watched early? Then the slot is done for the day. The library is only
        # asked when a banner or auto-play is about to happen: the first time
        # the slot is reached today, or a snooze running out. Never on every
        # tick once the banner has been answered.
        if key not in self.warned or (snooze_until is not None and now >= snooze_until):
            if self._watched_recently(slot, now):
                self.triggered[key] = True
                return

        # Handle snooze first, an expired snooze must re-prompt even if
        # we're now past scheduled_dt + 2min (snooze can outlast that window).
        if snooze_until is not None:
            if now < snooze_until:
                self._debug('Slot {} snoozed until {}'.format(slot.index + 1, snooze_until.strftime('%H:%M')))
                return
            # Bring the banner back with a fresh short countdown rather than
            # playing immediately, the user can start, snooze again, queue,
            # or cancel; ignoring it auto-plays when the countdown hits zero.
            del self.snoozed[key]
            self._log('Snooze expired for slot {}, re-showing prompt'.format(slot.index + 1))
            action, snooze_min = self._handle_warning(
                slot, now + timedelta(seconds=LATE_BANNER_SECONDS), key)
            self._apply_prompt_action(slot, key, action, snooze_min)
            return

        # Slot time already passed (Kodi was likely off at the time). Within
        # the configurable catch-up window we still prompt, the banner shows
        # how overdue it is and waits for the user (no auto-play). Beyond the
        # window we skip silently so a week of downtime doesn't spam prompts.
        if now > scheduled_dt + timedelta(minutes=2):
            if key in self.warned:
                return
            catchup_hours = self._catchup_hours()
            if catchup_hours > 0 and now <= scheduled_dt + timedelta(hours=catchup_hours):
                # Cap how many times a missed-slot banner appears (persists
                # across restarts) so it stops nagging.
                from resources.lib import slots as slot_store
                try:
                    max_prompts = xbmcaddon.Addon(ADDON_ID).getSettingInt('catchup_max_prompts')
                except Exception:
                    max_prompts = 3
                if max_prompts < 1:
                    max_prompts = 3
                shown = slot_store.get_catchup_count(key)
                if shown >= max_prompts:
                    self._debug('Slot {} catch-up cap ({}) reached, skipping'.format(
                        slot.index + 1, max_prompts))
                    self.warned[key] = True
                    return
                self._log('Catch-up: slot {} was due at {}, prompting ({}/{})'.format(
                    slot.index + 1, scheduled_dt.strftime('%H:%M'), shown + 1, max_prompts))
                action, snooze_min = self._handle_warning(slot, scheduled_dt, key)
                if action != 'skip':
                    slot_store.bump_catchup_count(key, now.strftime('%Y-%m-%d'))
                    self._apply_prompt_action(slot, key, action, snooze_min)
                    self.warned[key] = True
            else:
                self._debug('Slot {} outside catch-up window, skipping'.format(slot.index + 1))
                self.warned[key] = True
            return

        # In the warning window
        if warning_dt <= now < scheduled_dt:
            if key not in self.warned:
                self._debug('Warning window for slot {}'.format(slot.index + 1))
                action, snooze_min = self._handle_warning(slot, scheduled_dt, key)
                self._apply_prompt_action(slot, key, action, snooze_min)
                if action != 'skip':
                    self.warned[key] = True

        # Scheduled time reached
        elif now >= scheduled_dt:
            if slot.autoopen:
                # A banner left to time out to Queue Next would otherwise play
                # this occurrence again when the current playback ends.
                queued = [(s, k) for s, k in self.queued_next if k == key]
                if queued:
                    self.queued_next = [(s, k) for s, k in self.queued_next if k != key]
                    self._log('Slot {} was queued for this occurrence too, dropping the queue entry'.format(
                        slot.index + 1))
                xbmcgui.Dialog().notification(
                    'Engage',
                    '{} is ready.'.format(slot.label),
                    xbmcgui.NOTIFICATION_INFO,
                    5000
                )
                self._play_slot(slot, key)
                self.triggered[key] = True
                self._log('Slot {} auto-opened at scheduled time'.format(slot.index + 1))
            elif key not in self.warned:
                # No warning window was shown (warning 0, or Kodi started right
                # at the time): give the banner a short countdown from now so
                # its timeout action still fires rather than waiting for ever.
                countdown_to = max(scheduled_dt, now + timedelta(seconds=LATE_BANNER_SECONDS))
                action, snooze_min = self._handle_warning(slot, countdown_to, key)
                self._apply_prompt_action(slot, key, action, snooze_min)
                if action != 'skip':
                    self.warned[key] = True


class _EngagePlayerMonitor(xbmc.Player):
    """
    <summary>
    Subclass of xbmc.Player so we get OnPlayBackStopped / OnPlayBackEnded
    callbacks. Used to fire 'Queue Next' slots when current playback ends.
    </summary>
    """

    def __init__(self, scheduler):
        """
        <summary>
        Remember the scheduler whose playback hooks the callbacks forward to.
        </summary>
        <param name="scheduler">The owning EngageScheduler.</param>
        """
        super().__init__()
        self._scheduler = scheduler

    def onAVStarted(self):
        """
        <summary>
        Kodi callback once audio or video is actually rendering; forwards to the scheduler's
        _on_playback_started so a pending resume is armed.
        </summary>
        """
        self._scheduler._on_playback_started()

    def onPlayBackEnded(self):
        """
        <summary>
        Kodi callback when playback reaches the end of the item; forwards to _on_playback_ended.
        </summary>
        """
        self._scheduler._on_playback_ended()

    def onPlayBackStopped(self):
        """
        <summary>
        Kodi callback when playback is stopped by the user; treated the same as the item ending.
        </summary>
        """
        self._scheduler._on_playback_ended()


class _EngageMonitor(xbmc.Monitor):
    """
    <summary>
    The service's xbmc.Monitor: the abort wait the loop sleeps on, plus the
    notifications that refresh the next slot published for skins.
    </summary>
    <remarks>
    Kodi calls onNotification on its own thread for every announcement, so the
    work is handed to the scheduler, which decides what matters and republishes
    off this thread. The Manage slots dialog's save arrives here as
    'Other.' + SLOTS_SAVED_MESSAGE from ADDON_ID (see slots.save_slots).
    </remarks>
    """

    def __init__(self, scheduler):
        """
        <summary>
        Remember the scheduler the notifications are forwarded to.
        </summary>
        <param name="scheduler">The owning EngageScheduler.</param>
        """
        super().__init__()
        self._scheduler = scheduler

    def onNotification(self, sender, method, data):
        """
        <summary>
        Kodi callback for every announcement; forwards to the scheduler's _on_notification.
        </summary>
        <param name="sender">The announcing component or add-on id.</param>
        <param name="method">Flag and message, such as 'VideoLibrary.OnUpdate'.</param>
        <param name="data">The payload as a JSON string.</param>
        """
        self._scheduler._on_notification(sender, method, data)

    def onSettingsChanged(self):
        """
        <summary>
        Kodi callback after the add-on's settings are saved; forwards to the scheduler
        so the add-on API follows its setting without a restart.
        </summary>
        """
        self._scheduler._on_settings_changed()
