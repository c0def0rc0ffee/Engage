"""
<summary>
JSON-backed slot store for Engage.
</summary>
<remarks>
Slots live in addon_data/service.engage/slots.json instead of settings.xml,
so the user can add/remove any number of them at runtime. One-time migration
pulls any previously configured slotN_* settings across.
</remarks>
"""

import json
import os
import xml.etree.ElementTree as ET

import xbmc
import xbmcvfs

ADDON_ID = 'service.engage'
DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

# Message save_slots broadcasts with NotifyAll once slots.json is on disk. The
# Manage slots dialog runs in its own interpreter, so this is how the running
# service learns that a slot changed and republishes the next slot for skins
# (scheduler.py). Kodi delivers it to Monitor.onNotification as
# method 'Other.' + this string with ADDON_ID as the sender.
SLOTS_SAVED_MESSAGE = 'slots_saved'

# What a slot does when the banner's countdown reaches zero and nobody has
# touched it. Same identifiers the banner's buttons return, so the scheduler
# handles a timed-out banner through exactly the same path as a button press.
# Snooze is deliberately not offered: it would re-show the banner, time out
# again, snooze again, and never settle.
TIMEOUT_QUEUE = 'queue'
TIMEOUT_OPEN = 'open'
TIMEOUT_STOP_RESUME = 'stop_requeue'
TIMEOUT_CANCEL = 'cancel'
DEFAULT_TIMEOUT_ACTION = TIMEOUT_QUEUE

# Order here is the order the picker shows them in.
TIMEOUT_ACTIONS = [TIMEOUT_QUEUE, TIMEOUT_OPEN, TIMEOUT_STOP_RESUME, TIMEOUT_CANCEL]
TIMEOUT_ACTION_LABELS = {
    TIMEOUT_QUEUE: 'Queue it, play when the current item finishes',
    TIMEOUT_OPEN: 'Start it now',
    TIMEOUT_STOP_RESUME: 'Stop what is playing, play it, resume after',
    TIMEOUT_CANCEL: 'Do nothing, skip it',
}
# Short form for the slot list and the edit menu.
TIMEOUT_ACTION_SHORT = {
    TIMEOUT_QUEUE: 'Queue next',
    TIMEOUT_OPEN: 'Start now',
    TIMEOUT_STOP_RESUME: 'Stop & resume after',
    TIMEOUT_CANCEL: 'Do nothing',
}


# How a slot repeats. Weekly uses the 'days' list; every few weeks uses 'days',
# 'week_interval' and 'week_start'; monthly uses 'month_day'; yearly uses
# 'month' and 'month_day'. A one-shot 'date' overrides them all.
# Slots saved before this setting existed carry no key and are weekly.
REPEAT_WEEKLY = 'weekly'
REPEAT_EVERY_WEEKS = 'every_weeks'
REPEAT_MONTHLY = 'monthly'
REPEAT_YEARLY = 'yearly'
DEFAULT_REPEAT = REPEAT_WEEKLY
# Order here is the order the picker shows them in.
REPEATS = [REPEAT_WEEKLY, REPEAT_EVERY_WEEKS, REPEAT_MONTHLY, REPEAT_YEARLY]
# The gaps an every few weeks slot can have, in weeks.
WEEK_INTERVALS = [2, 3, 4]
# The Monday an every few weeks slot counts from when its own is missing or
# unreadable, which only a hand edited slots.json can cause; the picker always
# stores one.
FALLBACK_WEEK_START = '2024-01-01'
MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
               'August', 'September', 'October', 'November', 'December']


def repeat_of(slot):
    """
    <summary>
    How this slot repeats: 'weekly', 'monthly' or 'yearly'.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>One of REPEATS; an unknown or missing value gives DEFAULT_REPEAT.</returns>
    """
    repeat = (slot.get('repeat') or '').strip()
    return repeat if repeat in REPEATS else DEFAULT_REPEAT


def week_interval_of(slot):
    """
    <summary>
    How many weeks apart an every few weeks slot runs.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>One of WEEK_INTERVALS; a missing or unknown value gives 2.</returns>
    """
    try:
        weeks = int(slot.get('week_interval') or 2)
    except (ValueError, TypeError):
        return 2
    return weeks if weeks in WEEK_INTERVALS else 2


def week_start_of(slot):
    """
    <summary>
    The Monday of the first week an every few weeks slot runs in.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>A date, always a Monday: the stored 'week_start' moved back to its
    Monday, or FALLBACK_WEEK_START when it is missing or not a YYYY-MM-DD date.</returns>
    """
    from datetime import date, timedelta
    try:
        day = date(*[int(p) for p in str(slot.get('week_start') or '').split('-')])
    except (ValueError, TypeError):
        day = date(*[int(p) for p in FALLBACK_WEEK_START.split('-')])
    return day - timedelta(days=day.weekday())


def in_week_cycle(day, week_start, interval):
    """
    <summary>
    Whether a date falls in one of an every few weeks slot's on weeks.
    </summary>
    <param name="day">The date to test.</param>
    <param name="week_start">The Monday of an on week, from week_start_of.</param>
    <param name="interval">Weeks between on weeks, from week_interval_of.</param>
    <returns>True when the date's week is a whole number of intervals from the start
    week, before or after it.</returns>
    <remarks>
    Only the week is tested; the caller checks the weekday against the slot's days.
    Weeks before the start count too, which only matters for a start week chosen in
    the future: the slot then also runs in the on weeks leading up to it, keeping the
    same rhythm.
    </remarks>
    """
    monday = day.toordinal() - day.weekday()
    return ((monday - week_start.toordinal()) // 7) % interval == 0


def month_day_of(slot):
    """
    <summary>
    The day of the month a monthly or yearly slot runs on, 1 to 31.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>The stored day, or 1 when it is missing or out of range.</returns>
    <remarks>
    The stored day is kept as chosen even when a month is shorter; the scheduler
    moves it to that month's last day at run time (see last_day_of_month), so the
    31st runs on the 30th in April and on the 31st again in May.
    </remarks>
    """
    try:
        day = int(slot.get('month_day') or 1)
    except (ValueError, TypeError):
        return 1
    return day if 1 <= day <= 31 else 1


def month_of(slot):
    """
    <summary>
    The month a yearly slot runs in, 1 (January) to 12.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>The stored month, or 1 when it is missing or out of range.</returns>
    """
    try:
        month = int(slot.get('month') or 1)
    except (ValueError, TypeError):
        return 1
    return month if 1 <= month <= 12 else 1


def last_day_of_month(year, month):
    """
    <summary>
    The number of days in a month, leap years included.
    </summary>
    <param name="year">The year, needed for February.</param>
    <param name="month">The month, 1 to 12.</param>
    <returns>28 to 31.</returns>
    """
    import calendar
    return calendar.monthrange(year, month)[1]


def ordinal(n):
    """
    <summary>
    A day number with its English suffix: 1st, 2nd, 3rd, 4th, 11th, 21st.
    </summary>
    <param name="n">A positive int.</param>
    <returns>The number and suffix as one string.</returns>
    """
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return '{}{}'.format(n, suffix)


def timeout_action_of(slot):
    """
    <summary>
    What this slot does when the banner countdown runs out.
    </summary>
    <remarks>
    Slots saved before this setting existed carry no key and take the default.
    The older 'autoopen' flag is a different thing (it skips the banner
    altogether at the scheduled time), so it is not consulted here.
    </remarks>
    """
    action = (slot.get('timeout_action') or '').strip()
    return action if action in TIMEOUT_ACTIONS else DEFAULT_TIMEOUT_ACTION


def _log(msg, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to kodi.log under the 'Engage slots:' prefix.
    </summary>
    <param name="msg">Text to log.</param>
    <param name="level">Kodi log level, LOGINFO by default.</param>
    """
    xbmc.log('Engage slots: {}'.format(msg), level)


def _data_dir():
    """
    <summary>
    The add-on's profile data folder as a real path, created if it does not exist yet.
    </summary>
    <returns>The folder path, with the trailing separator Kodi's translatePath keeps.</returns>
    """
    path = xbmcvfs.translatePath('special://profile/addon_data/{}/'.format(ADDON_ID))
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def _file_path():
    """
    <summary>
    Full path of slots.json inside the data folder.
    </summary>
    <returns>The path.</returns>
    """
    return os.path.join(_data_dir(), 'slots.json')


def _catchup_path():
    """
    <summary>
    Full path of catchup.json, the per-occurrence count of missed-slot banners shown.
    </summary>
    <returns>The path.</returns>
    """
    return os.path.join(_data_dir(), 'catchup.json')


def get_catchup_count(key):
    """
    <summary>
    How many times the overdue/catch-up banner has been shown for this
    occurrence (persists across restarts).
    </summary>
    """
    try:
        with open(_catchup_path(), 'r', encoding='utf-8') as f:
            return int(json.load(f).get(key, 0))
    except (OSError, IOError, ValueError):
        return 0


def bump_catchup_count(key, today_str):
    """
    <summary>
    Increment the catch-up counter for this occurrence. Prunes entries from
    earlier days so the file doesn't grow. Returns the new count.
    </summary>
    """
    try:
        with open(_catchup_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, IOError, ValueError):
        data = {}
    # Drop any keys not from today (keys look like 'YYYY-MM-DD-<id>-<HHMM>').
    data = {k: v for k, v in data.items() if k.startswith(today_str)}
    data[key] = int(data.get(key, 0)) + 1
    try:
        with open(_catchup_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except (OSError, IOError) as e:
        _log('could not write catchup.json: {}'.format(e), xbmc.LOGWARNING)
    return data[key]


def _list_progress_path():
    """
    <summary>
    Full path of list_progress.json, where 'every item in turn' list slots remember
    the item they played last.
    </summary>
    <returns>The path.</returns>
    """
    return os.path.join(_data_dir(), 'list_progress.json')


def get_list_progress(slot_id):
    """
    <summary>
    What an 'every item in turn' list slot played last.
    </summary>
    <param name="slot_id">The slot's id.</param>
    <returns>{'identity': [...], 'index': n}, or None when it has not played yet or
    the file cannot be read.</returns>
    <remarks>
    Kept out of slots.json on purpose: the service writes this, and a write to
    slots.json from the service could race a save from the Manage slots dialog,
    which runs in its own interpreter. Keyed by slot id as a string, as JSON needs.
    </remarks>
    """
    try:
        with open(_list_progress_path(), 'r', encoding='utf-8') as f:
            entry = json.load(f).get(str(slot_id))
        return entry if isinstance(entry, dict) else None
    except (OSError, IOError, ValueError, AttributeError):
        return None


def set_list_progress(slot_id, identity, index):
    """
    <summary>
    Record the item an 'every item in turn' list slot has just played.
    </summary>
    <param name="slot_id">The slot's id.</param>
    <param name="identity">functional_lists.item_identity of the item played.</param>
    <param name="index">Its position in the list at the time.</param>
    <remarks>
    Written through a .tmp file and a rename, like slots.json. A failed write is
    logged and swallowed: the worst case is the same item playing again next time.
    Entries for deleted slots are left in place; they are a few bytes and are
    harmless if a later slot reuses the id, which then just carries on from there.
    </remarks>
    """
    path = _list_progress_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, IOError, ValueError):
        data = {}
    data[str(slot_id)] = {'identity': list(identity), 'index': int(index)}
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except (OSError, IOError) as e:
        _log('could not write list_progress.json: {}'.format(e), xbmc.LOGWARNING)


def get_series_progress(slot_id):
    """
    <summary>
    What each series in a random list slot played last, when the slot keeps sequels
    in order.
    </summary>
    <param name="slot_id">The slot's id.</param>
    <returns>{group: {'identity': [...], 'index': n}}, empty when nothing has played
    yet or the file cannot be read.</returns>
    <remarks>
    Kept in list_progress.json under a 'series' key beside the in turn entries, which
    are keyed by slot id and so can never collide with it.
    </remarks>
    """
    try:
        with open(_list_progress_path(), 'r', encoding='utf-8') as f:
            entry = (json.load(f).get('series') or {}).get(str(slot_id))
        return entry if isinstance(entry, dict) else {}
    except (OSError, IOError, ValueError, AttributeError):
        return {}


def set_series_progress(slot_id, group, identity, index):
    """
    <summary>
    Record the item one series in a random list slot has just played.
    </summary>
    <param name="slot_id">The slot's id.</param>
    <param name="group">The series, as functional_lists.series_position names it.</param>
    <param name="identity">functional_lists.item_identity of the item played.</param>
    <param name="index">Its position in the series at the time.</param>
    <remarks>
    Written through a .tmp file and a rename. A failed write is logged and
    swallowed: the worst case is the same film of that series coming up again.
    </remarks>
    """
    path = _list_progress_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, IOError, ValueError):
        data = {}
    series = data.get('series') if isinstance(data.get('series'), dict) else {}
    entry = series.get(str(slot_id)) if isinstance(series.get(str(slot_id)), dict) else {}
    entry[group] = {'identity': list(identity), 'index': int(index)}
    series[str(slot_id)] = entry
    data['series'] = series
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except (OSError, IOError) as e:
        _log('could not write list_progress.json: {}'.format(e), xbmc.LOGWARNING)


def new_slot(favourite='', day='Monday'):
    """
    <summary>
    Return a fresh slot dict with sensible defaults. id is assigned on save.
    </summary>
    """
    return {
        'id': 0,
        'enabled': True,
        'label': '',
        'kind': 'favourite',   # 'favourite', 'sequence' or 'list'
        'favourite': favourite,
        'days': [day],         # one or more weekday names
        'repeat': DEFAULT_REPEAT,  # one of REPEATS
        'month_day': 1,        # monthly and yearly: day of the month
        'month': 1,            # yearly: month, 1 to 12
        'date': '',
        'hour': 19,
        'minute': 0,
        'warning': 5,
        'autoopen': False,
        'snooze': True,
        'timeout_action': DEFAULT_TIMEOUT_ACTION,
    }


def new_sequence(label='', day='Monday', seq_type='movie'):
    """
    <summary>
    Return a fresh sequence slot.
    </summary>
    <remarks>
    seq_type 'movie' = ordered movie list; 'binge' = ordered episode list
    across shows (air-date order). Items live in 'items', each a dict with a
    'type' ('movie' or 'episode'), an 'id', plus display fields.
    </remarks>
    """
    s = new_slot(day=day)
    s['kind'] = 'sequence'
    s['label'] = label
    s['seq_type'] = seq_type
    s['items'] = []
    return s


def new_list_slot(list_name, mode, day='Monday'):
    """
    <summary>
    Return a fresh slot that plays from one of the Functional skin's lists.
    </summary>
    <param name="list_name">The list's name as the skin shows it.</param>
    <param name="mode">One of functional_lists.MODES: 'next' (first unwatched in
    list order), 'turn' (every item in turn), 'random' (a random unwatched item) or
    'random_any' (any item at random).</param>
    <param name="day">The first weekday it runs on.</param>
    <returns>The slot dict, with an empty label so the list name is shown.</returns>
    <remarks>
    The list is referenced by name only; its items stay in the skin's lists.json
    and are read at play time. Renaming or deleting the list in the skin leaves
    the slot pointing at nothing, and the scheduler then skips it with a warning.
    </remarks>
    """
    s = new_slot(day=day)
    s['kind'] = 'list'
    s['list_name'] = list_name
    s['list_mode'] = mode
    return s


def days_of(slot):
    """
    <summary>
    Return a slot's weekday list, converting the legacy single 'day' field.
    </summary>
    """
    days = slot.get('days')
    if not days:
        d = slot.get('day')
        days = [d] if d else ['Monday']
    return days


def items_of(slot):
    """
    <summary>
    Return a sequence slot's ordered items, converting the legacy 'movies'
    field (pre-1.7.1 movie sequences) to the unified 'items' shape on the fly.
    </summary>
    """
    items = slot.get('items')
    if items is None:
        items = [{'type': 'movie', 'id': m.get('id'), 'title': m.get('title'),
                  'year': m.get('year')}
                 for m in (slot.get('movies') or [])]
    return items


def next_id(slots):
    """
    <summary>
    The next free slot id: one more than the highest id in the list, 1 for an empty list.
    </summary>
    <param name="slots">The current slot dicts.</param>
    <returns>A positive int.</returns>
    <remarks>
    Deleting the slot with the highest id frees that id for the next one added.
    </remarks>
    """
    return max([s.get('id', 0) for s in slots] or [0]) + 1


def load_slots():
    """
    <summary>
    Return the list of slot dicts. Runs settings migration on first call.
    </summary>
    """
    migrate_from_settings_if_needed()
    try:
        with open(_file_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('slots', []) or []
    except (OSError, IOError, ValueError) as e:
        _log('failed to read slots.json: {}'.format(e), xbmc.LOGWARNING)
        return []


def save_slots(slots):
    """
    <summary>
    Atomically write the slot list, then tell the running service about it.
    </summary>
    <param name="slots">The full list of slot dicts to persist.</param>
    <remarks>
    The write goes to a .tmp file first and is renamed over slots.json, so a
    crash mid-write leaves the old file intact. Once the rename has landed the
    save is announced with NotifyAll so the service, which runs in a separate
    interpreter, can refresh the Home window properties skins read. Every save
    path (add, edit, duplicate, delete, restore) comes through here, which is
    why the announcement lives in the store and not in the dialog code.
    </remarks>
    """
    payload = {'version': 1, 'slots': slots}
    path = _file_path()
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)
    _announce_saved()


def _announce_saved():
    """
    <summary>
    Broadcast SLOTS_SAVED_MESSAGE to every Kodi announcement listener.
    </summary>
    <remarks>
    Best effort: the save has already succeeded, so a failure here is logged
    and swallowed. When the service is not running nobody listens and the
    message is simply dropped; the service reads slots.json afresh on every
    tick anyway, so nothing is lost.
    </remarks>
    """
    try:
        xbmc.executebuiltin('NotifyAll({},{})'.format(ADDON_ID, SLOTS_SAVED_MESSAGE))
    except Exception as e:
        _log('could not announce the save: {}'.format(e), xbmc.LOGWARNING)


def get_slot(slots, slot_id):
    """
    <summary>
    Find a slot dict by id.
    </summary>
    <param name="slots">The slot dicts to search.</param>
    <param name="slot_id">The id wanted.</param>
    <returns>The dict, or None when no slot has that id.</returns>
    """
    return next((s for s in slots if s.get('id') == slot_id), None)


# --- Backup / restore -------------------------------------------------------

# General settings that are worth backing up alongside the slots.
BACKUP_SETTING_IDS = ['debug', 'poll_interval', 'catchup_hours', 'recent_watch_hours',
                      'show_all_favourites']


def build_backup():
    """
    <summary>
    Return a dict capturing all user data: slots + general settings.
    </summary>
    """
    import xbmcaddon
    addon = xbmcaddon.Addon(ADDON_ID)
    settings = {}
    for sid in BACKUP_SETTING_IDS:
        try:
            settings[sid] = addon.getSetting(sid)
        except Exception:
            pass
    return {
        'type': 'engage-backup',
        'format': 1,
        'slots': load_slots(),
        'settings': settings,
    }


def write_backup(file_path):
    """
    <summary>
    Write a backup JSON to an absolute path. Returns slot count written.
    </summary>
    """
    backup = build_backup()
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(backup, f, indent=2)
    _log('wrote backup ({} slots) to {}'.format(len(backup['slots']), file_path))
    return len(backup['slots'])


def restore_backup(file_path):
    """
    <summary>
    Restore slots + settings from a backup JSON. Returns slot count restored.
    Raises ValueError if the file isn't a recognised Engage backup.
    </summary>
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    count = restore_backup_data(data)
    _log('restored backup ({} slots) from {}'.format(count, file_path))
    return count


def restore_backup_data(data):
    """
    <summary>
    Restore slots + settings from an already parsed backup document. Returns slot count
    restored. Raises ValueError if it isn't a recognised Engage backup.
    </summary>
    <param name="data">The document, as build_backup makes it.</param>
    <remarks>
    Shared by restore_backup (a file chosen in Kodi) and the add-on API's POST
    /restore, which checks the document's shape more closely before calling this.
    </remarks>
    """
    import xbmcaddon
    if not isinstance(data, dict) or data.get('type') != 'engage-backup':
        raise ValueError('Not an Engage backup file')

    slots = data.get('slots', []) or []
    save_slots(slots)

    addon = xbmcaddon.Addon(ADDON_ID)
    for sid, val in (data.get('settings') or {}).items():
        try:
            addon.setSetting(sid, val)
        except Exception:
            pass

    return len(slots)


def _read_old_settings_values():
    """
    <summary>
    Read raw values from the addon's saved settings.xml (works even for
    setting ids that are no longer defined in resources/settings.xml).
    </summary>
    """
    path = os.path.join(_data_dir(), 'settings.xml')
    values = {}
    if not os.path.exists(path):
        return values
    try:
        tree = ET.parse(path)
        for el in tree.getroot().findall('setting'):
            sid = el.get('id')
            if sid:
                values[sid] = (el.text or '').strip()
    except (ET.ParseError, OSError) as e:
        _log('could not parse old settings.xml for migration: {}'.format(e), xbmc.LOGWARNING)
    return values


def migrate_from_settings_if_needed():
    """
    <summary>
    One-time conversion of legacy slotN_* settings into slots.json.
    </summary>
    """
    if os.path.exists(_file_path()):
        return

    values = _read_old_settings_values()
    slots = []
    for i in range(1, 21):
        fav = values.get('slot{}_favourite'.format(i), '')
        if not fav:
            continue

        def _int(key, default):
            """
            <summary>
            Read one numeric slotN_* value from the legacy settings for the slot being migrated.
            </summary>
            <param name="key">The part of the setting id after slotN_, such as 'time' or 'warning'.</param>
            <param name="default">Returned when the setting is missing or is not a number.</param>
            <returns>The value as an int.</returns>
            <remarks>
            A closure over the loop's slot number i and the values dict read from settings.xml.
            </remarks>
            """
            try:
                return int(values.get('slot{}_{}'.format(i, key), default))
            except (ValueError, TypeError):
                return default

        slot = new_slot(favourite=fav, day=values.get('slot{}_day'.format(i), 'Monday'))
        slot['id'] = next_id(slots)
        slot['enabled'] = values.get('slot{}_enabled'.format(i), 'false') == 'true'
        slot['label'] = values.get('slot{}_label'.format(i), '')
        slot['date'] = values.get('slot{}_date'.format(i), '')
        slot['hour'] = _int('time', 19)
        slot['minute'] = _int('minute', 0)
        slot['warning'] = _int('warning', 5)
        slot['autoopen'] = values.get('slot{}_autoopen'.format(i), 'false') == 'true'
        slot['snooze'] = values.get('slot{}_snooze'.format(i), 'true') == 'true'
        slots.append(slot)

    save_slots(slots)
    _log('migrated {} slot(s) from legacy settings'.format(len(slots)))
