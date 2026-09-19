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
        'kind': 'favourite',   # 'favourite' or 'sequence'
        'favourite': favourite,
        'days': [day],         # one or more weekday names
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
    Atomically write the slot list.
    </summary>
    """
    payload = {'version': 1, 'slots': slots}
    path = _file_path()
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


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
BACKUP_SETTING_IDS = ['debug', 'poll_interval', 'catchup_hours', 'show_all_favourites']


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
    import xbmcaddon
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

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

    _log('restored backup ({} slots) from {}'.format(len(slots), file_path))
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
