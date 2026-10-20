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
import tempfile
import threading
import time
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


# How the banner is laid out when a slot's prompt pops up. 'standard' is the
# original 100px strip with text buttons; 'thin' is the slim strip with icon
# buttons and the poster hanging below it, the same size as before. A slot may
# say 'default' to follow the master setting (settings.xml, banner_style),
# which is resolved at popup time so changing the master changes every such
# slot without rewriting slots.json.
BANNER_STANDARD = 'standard'
BANNER_THIN = 'thin'
BANNER_DEFAULT = 'default'
# What the master falls back to when its setting is missing or unreadable, so
# an install that has never seen the setting keeps the original banner.
DEFAULT_BANNER_STYLE = BANNER_STANDARD
# What a slot without the key does: follow the master.
DEFAULT_SLOT_BANNER_STYLE = BANNER_DEFAULT
# Order here is the order the pickers show them in. The master offers the two
# real layouts; a slot offers those plus 'default'.
BANNER_STYLES = [BANNER_STANDARD, BANNER_THIN]
SLOT_BANNER_STYLES = [BANNER_DEFAULT, BANNER_STANDARD, BANNER_THIN]
BANNER_STYLE_LABELS = {
    BANNER_DEFAULT: 'Use the default banner style',
    BANNER_STANDARD: 'Standard, the full height banner with text buttons',
    BANNER_THIN: 'Thin, a slim banner with icon buttons',
}
# Short form for the slot list and the edit menu.
BANNER_STYLE_SHORT = {
    BANNER_DEFAULT: 'Default',
    BANNER_STANDARD: 'Standard',
    BANNER_THIN: 'Thin',
}
# The settings.xml enum stores the index of its values list ("0" or "1"), in
# this order. Kept beside the enum's values attribute so the two cannot drift
# without a reader noticing.
BANNER_STYLE_SETTING_ORDER = [BANNER_STANDARD, BANNER_THIN]


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


def banner_style_of(slot):
    """
    <summary>
    The banner style this slot asks for: 'standard', 'thin' or 'default'.
    </summary>
    <param name="slot">A slot dict from the store.</param>
    <returns>One of SLOT_BANNER_STYLES; 'default' when the key is missing or
    holds something unknown.</returns>
    <remarks>
    This is the slot's own choice, not the layout that will be used; see
    resolve_banner_style for that. Slots saved before this setting existed carry
    no key and follow the master.
    </remarks>
    """
    style = (slot.get('banner_style') or '').strip().lower()
    return style if style in SLOT_BANNER_STYLES else DEFAULT_SLOT_BANNER_STYLE


def parse_banner_style_setting(raw):
    """
    <summary>
    Turn the master setting's stored value into a banner style.
    </summary>
    <param name="raw">What getSetting('banner_style') returned.</param>
    <returns>One of BANNER_STYLES; DEFAULT_BANNER_STYLE for anything unreadable.</returns>
    <remarks>
    Kodi stores an enum setting as the index of the chosen entry, as a string,
    so "0" and "1" are mapped through BANNER_STYLE_SETTING_ORDER. The style
    names themselves are accepted too, in any case, so a backup written by hand
    or a future select type setting reads the same way. Empty, which is what an
    install upgraded from before the setting existed reports until it is first
    opened, gives the default.
    </remarks>
    """
    text = (raw if isinstance(raw, str) else str(raw or '')).strip().lower()
    if text in BANNER_STYLES:
        return text
    try:
        index = int(text)
    except ValueError:
        return DEFAULT_BANNER_STYLE
    if 0 <= index < len(BANNER_STYLE_SETTING_ORDER):
        return BANNER_STYLE_SETTING_ORDER[index]
    return DEFAULT_BANNER_STYLE


def master_banner_style():
    """
    <summary>
    The master banner style from the add-on settings, read fresh each call.
    </summary>
    <returns>One of BANNER_STYLES; DEFAULT_BANNER_STYLE when the setting cannot be read.</returns>
    <remarks>
    Read at popup time rather than cached, so a change in the settings dialog
    applies to the next banner without a restart.
    </remarks>
    """
    try:
        import xbmcaddon
        return parse_banner_style_setting(xbmcaddon.Addon(ADDON_ID).getSetting('banner_style'))
    except Exception as e:
        _log('could not read banner_style setting, using {}: {}'.format(DEFAULT_BANNER_STYLE, e),
             xbmc.LOGWARNING)
        return DEFAULT_BANNER_STYLE


def resolve_banner_style(slot_style, master_style):
    """
    <summary>
    The layout a banner actually uses: the slot's own choice when it has one,
    else the master.
    </summary>
    <param name="slot_style">The slot's choice, as banner_style_of returns it.</param>
    <param name="master_style">The master, as master_banner_style returns it.</param>
    <returns>One of BANNER_STYLES.</returns>
    <remarks>
    Both arguments are checked again here, so an unknown value on either side
    falls through: an unknown slot choice follows the master and an unknown
    master gives DEFAULT_BANNER_STYLE.
    </remarks>
    """
    if slot_style in BANNER_STYLES:
        return slot_style
    if master_style in BANNER_STYLES:
        return master_style
    return DEFAULT_BANNER_STYLE


def _log(msg, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to kodi.log under the 'Engage slots:' prefix.
    </summary>
    <param name="msg">Text to log.</param>
    <param name="level">Kodi log level, LOGINFO by default.</param>
    """
    xbmc.log('Engage slots: {}'.format(msg), level)


# One lock for every writer in this interpreter: the API's handler threads, the
# scheduler thread and the player callback thread all take it. The Manage slots
# dialog runs in its own interpreter with its own copy of this module, so the
# lock cannot reach it; the unique temporary names _write_json uses are what keep
# a save from that side and a save from here from ever producing a half written
# file, because each writer renames its own complete file into place.
_WRITE_LOCK = threading.Lock()

# Files whose unreadable copy could not be moved aside. save paths refuse to
# write over one of these, so a broken slots.json is never silently replaced.
_BLOCKED_PATHS = set()

# Which files the user has already been told about this run, so the toast
# shows once rather than on every tick.
_BROKEN_NOTIFIED = set()


def _write_json(path, payload, indent=None):
    """
    <summary>
    Write a JSON document to path through a uniquely named temporary file in the
    same folder and an atomic rename, under the process wide write lock.
    </summary>
    <param name="path">The file to write.</param>
    <param name="payload">The value to serialise.</param>
    <param name="indent">Passed to json.dump; None for the compact form.</param>
    <exception cref="OSError">When the temporary file cannot be written or renamed;
    the temporary file is removed first so nothing is left behind.</exception>
    <exception cref="RuntimeError">When path is one whose unreadable copy could not
    be moved aside (see _quarantine), so writing would destroy it.</exception>
    <remarks>
    tempfile.mkstemp gives each writer its own file, which is the whole point:
    two writers sharing one fixed name could truncate each other and rename a
    half written file over the real one. The mode is set to 0644 because mkstemp
    creates files readable by the owner only and the old write used the umask.
    </remarks>
    """
    if path in _BLOCKED_PATHS:
        raise RuntimeError('{} is unreadable and could not be moved aside, refusing to write over it'.format(path))
    with _WRITE_LOCK:
        handle, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + '.', suffix='.tmp',
                                       dir=os.path.dirname(path))
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=indent)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


def _read_json_file(path):
    """
    <summary>
    Read a JSON file, telling a missing file from an unreadable one.
    </summary>
    <param name="path">The file to read.</param>
    <returns>(data, problem): data is the parsed value and problem '' when it read;
    data is None and problem 'missing' when there is no such file; data is None
    and problem the error text when the file exists but cannot be read or parsed.</returns>
    """
    if not os.path.exists(path):
        return None, 'missing'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f), ''
    except (OSError, IOError, ValueError) as e:
        return None, str(e) or type(e).__name__


def _quarantine(path, why):
    """
    <summary>
    Move an unreadable file aside as path.broken-&lt;timestamp&gt; so it is kept for
    inspection and a later save cannot write over it. Nothing is ever deleted.
    </summary>
    <param name="path">The unreadable file.</param>
    <param name="why">What was wrong with it, for the log.</param>
    <returns>The path it was moved to, or None when the move failed, in which case
    the path is added to _BLOCKED_PATHS and _write_json refuses to touch it.</returns>
    """
    stamp = time.strftime('%Y%m%d-%H%M%S')
    aside = '{}.broken-{}'.format(path, stamp)
    n = 0
    while os.path.exists(aside):
        n += 1
        aside = '{}.broken-{}-{}'.format(path, stamp, n)
    try:
        os.replace(path, aside)
    except OSError as e:
        _BLOCKED_PATHS.add(path)
        _log('{} is unreadable ({}) and could not be moved aside ({}); saves to it are refused'.format(
            path, why, e), xbmc.LOGERROR)
        return None
    _BLOCKED_PATHS.discard(path)
    _log('{} was unreadable ({}); moved aside to {} and starting afresh'.format(path, why, aside),
         xbmc.LOGERROR)
    return aside


def _notify_once(key, message):
    """
    <summary>
    Show an error toast the first time key is seen this run; later calls with the
    same key are silent.
    </summary>
    <param name="key">What the toast is about, such as the file path.</param>
    <param name="message">The text shown.</param>
    <remarks>
    xbmcgui is imported here because nothing else in this module needs the GUI,
    and a failure to show the toast is logged rather than raised: the caller has
    already logged the real problem.
    </remarks>
    """
    if key in _BROKEN_NOTIFIED:
        return
    _BROKEN_NOTIFIED.add(key)
    try:
        import xbmcgui
        xbmcgui.Dialog().notification('Engage', message, xbmcgui.NOTIFICATION_ERROR, 8000)
    except Exception as e:
        _log('could not show the notification: {}'.format(e), xbmc.LOGWARNING)


def _load_dict(path):
    """
    <summary>
    A JSON object file as a dict, empty when the file is missing, and empty after
    an unreadable or wrongly shaped file has been moved aside.
    </summary>
    <param name="path">The file to read.</param>
    <returns>The dict.</returns>
    <remarks>
    For the side files (catchup.json, list_progress.json) starting afresh costs at
    most a repeated banner or a repeated item, so no toast is shown; the move and
    the reason are in kodi.log.
    </remarks>
    """
    data, problem = _read_json_file(path)
    if problem == 'missing':
        return {}
    if problem:
        _quarantine(path, problem)
        return {}
    if not isinstance(data, dict):
        _quarantine(path, 'not a JSON object')
        return {}
    return data


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
    <param name="key">The occurrence key, as scheduler.occurrence_key makes it.</param>
    <returns>The count, 0 when there is none or the file was unreadable and has
    been moved aside (see _load_dict).</returns>
    """
    try:
        return int(_load_dict(_catchup_path()).get(key, 0))
    except (ValueError, TypeError):
        return 0


def bump_catchup_count(key, today_str):
    """
    <summary>
    Increment the catch-up counter for this occurrence. Prunes entries from
    earlier days so the file doesn't grow. Returns the new count.
    </summary>
    <param name="key">The occurrence key being counted.</param>
    <param name="today_str">Today as 'YYYY-MM-DD'; keys from other days are dropped,
    except the one being counted, which may be yesterday's when the catch-up window
    runs past midnight.</param>
    <returns>The new count for key.</returns>
    <remarks>
    Written through _write_json, so the file is never half written and a failed
    write is logged and swallowed: the worst case is one extra banner.
    </remarks>
    """
    data = _load_dict(_catchup_path())
    # Drop any keys not from today (keys look like 'YYYY-MM-DD-<id>-<HHMM>').
    data = {k: v for k, v in data.items() if k.startswith(today_str) or k == key}
    try:
        data[key] = int(data.get(key, 0)) + 1
    except (ValueError, TypeError):
        data[key] = 1
    try:
        _write_json(_catchup_path(), data)
    except (OSError, IOError, RuntimeError) as e:
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
    An unreadable file is moved aside by _load_dict and reads as no progress.
    </remarks>
    """
    entry = _load_dict(_list_progress_path()).get(str(slot_id))
    return entry if isinstance(entry, dict) else None


def set_list_progress(slot_id, identity, index):
    """
    <summary>
    Record the item an 'every item in turn' list slot has just played.
    </summary>
    <param name="slot_id">The slot's id.</param>
    <param name="identity">functional_lists.item_identity of the item played.</param>
    <param name="index">Its position in the list at the time.</param>
    <remarks>
    Written through _write_json (a uniquely named temporary file, a rename and the
    write lock), like slots.json. A failed write is logged and swallowed: the worst
    case is the same item playing again next time. Entries for deleted slots are
    left in place; they are a few bytes and are harmless if a later slot reuses the
    id, which then just carries on from there.
    </remarks>
    """
    path = _list_progress_path()
    data = _load_dict(path)
    data[str(slot_id)] = {'identity': list(identity), 'index': int(index)}
    try:
        _write_json(path, data)
    except (OSError, IOError, RuntimeError) as e:
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
    are keyed by slot id and so can never collide with it. An unreadable file is
    moved aside by _load_dict and reads as no progress.
    </remarks>
    """
    series = _load_dict(_list_progress_path()).get('series')
    entry = series.get(str(slot_id)) if isinstance(series, dict) else None
    return entry if isinstance(entry, dict) else {}


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
    Written through _write_json, like set_list_progress. A failed write is logged
    and swallowed: the worst case is the same film of that series coming up again.
    </remarks>
    """
    path = _list_progress_path()
    data = _load_dict(path)
    series = data.get('series') if isinstance(data.get('series'), dict) else {}
    entry = series.get(str(slot_id)) if isinstance(series.get(str(slot_id)), dict) else {}
    entry[group] = {'identity': list(identity), 'index': int(index)}
    series[str(slot_id)] = entry
    data['series'] = series
    try:
        _write_json(path, data)
    except (OSError, IOError, RuntimeError) as e:
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
        'banner_style': DEFAULT_SLOT_BANNER_STYLE,  # one of SLOT_BANNER_STYLES
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
    <returns>The slots. Empty when there is no slots.json yet, and empty after a
    slots.json that exists but cannot be read has been moved aside.</returns>
    <remarks>
    A missing file and an unreadable file are different things. The first is a
    fresh install. The second used to read as an empty list with a warning, after
    which the next add from the dialog or the API wrote a one slot file over the
    top and every other slot was gone. Now the unreadable file is moved aside as
    slots.json.broken-&lt;timestamp&gt; (never deleted), the error goes to kodi.log, the
    user is told once, and this returns an empty list, so a later save starts a new
    file beside the kept one. When the move itself fails the path is blocked and
    save_slots raises rather than overwrite it.
    </remarks>
    """
    migrate_from_settings_if_needed()
    path = _file_path()
    data, problem = _read_json_file(path)
    if problem == 'missing':
        return []
    if not problem:
        if isinstance(data, dict) and isinstance(data.get('slots', []), list):
            _BLOCKED_PATHS.discard(path)
            return data.get('slots') or []
        problem = 'not an Engage slots document'
    aside = _quarantine(path, problem)
    if aside:
        _notify_once(path, 'The slots file could not be read. It was kept as {}; see kodi.log.'.format(
            os.path.basename(aside)))
    else:
        _notify_once(path, 'The slots file could not be read and slots cannot be saved; see kodi.log.')
    return []


def save_slots(slots):
    """
    <summary>
    Atomically write the slot list, then tell the running service about it.
    </summary>
    <param name="slots">The full list of slot dicts to persist.</param>
    <exception cref="RuntimeError">When slots.json is unreadable and could not be
    moved aside; writing would destroy the only copy (see load_slots).</exception>
    <remarks>
    The write goes through _write_json: a uniquely named temporary file in the
    same folder renamed over slots.json under the write lock, so a crash mid-write
    leaves the old file intact and two writers never share a temporary file. Once
    the rename has landed the save is announced with NotifyAll so the service,
    which runs in a separate interpreter, can refresh the Home window properties
    skins read. Every save path (add, edit, duplicate, delete, restore) comes
    through here, which is why the announcement lives in the store and not in the
    dialog code.
    </remarks>
    """
    payload = {'version': 1, 'slots': slots}
    _write_json(_file_path(), payload, indent=2)
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
                      'show_all_favourites', 'banner_style']


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
