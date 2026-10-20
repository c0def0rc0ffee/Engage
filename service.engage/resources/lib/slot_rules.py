"""
<summary>
The rules a slot must meet, and the shape a slot takes over the add-on API.
</summary>
<remarks>
The Manage slots dialog enforces these rules through its pickers: it never offers
a day that does not exist, a time past 23:59 or a sequence with nothing in it. The
API (api_server.py) has no pickers, so the same rules are written out here and every
write that arrives over HTTP passes through from_api.

The API shape is the stored shape with three differences. 'hour' and 'minute'
travel as one 'time' string, 'HH:MM'. The legacy 'day' and 'movies' fields are read
through slots.days_of and slots.items_of and never sent. Only the fields that matter
for the slot's kind and repeat are included, matching the show_when rules in
schema(), plus two read only fields, 'title' and 'summary', for a list view.

A write only changes the fields it sends; everything else keeps its stored value.
That is what lets a field written by a newer dialog, which this module does not
know, survive an edit made over the API, and it is why the fields a form hides
(the other repeat's days, say) are left alone exactly as the dialog leaves them.

Nothing here touches Kodi except summary text, which borrows the dialog's own
wording from slot_manager.
</remarks>
"""

import copy
import re
from datetime import date, datetime, timedelta

from resources.lib import functional_lists
from resources.lib import slots as store

# A design choice, not something the dialog enforces: Kodi's keyboard has no
# limit, but nobody wants a label longer than this in a banner.
LABEL_MAX = 100

# The range the dialog's warning picker offers.
WARNING_MAX = 30

KINDS = ['favourite', 'sequence', 'list']
KIND_LABELS = {
    'favourite': 'A favourite, one show or film',
    'sequence': 'A sequence, films or episodes in a set order',
    'list': 'A Functional skin list',
}

SEQ_TYPES = ['movie', 'binge']
SEQ_TYPE_LABELS = {
    'movie': 'Movie sequence, films in order',
    'binge': 'Binge order, episodes in order',
}

# The one item type each sequence type may hold.
ITEM_TYPE_FOR = {'movie': 'movie', 'binge': 'episode'}

# Display fields kept on a sequence item, with the JSON type each must have. Any
# other key on an item is dropped; 'type' and 'id' are checked separately.
ITEM_FIELDS = {
    'movie': {'title': str, 'year': int},
    'episode': {'show': str, 'season': int, 'episode': int, 'title': str, 'firstaired': str},
}

# The same wording as the dialog's repeat picker.
REPEAT_LABELS = {
    store.REPEAT_WEEKLY: 'Weekly, on chosen days',
    store.REPEAT_EVERY_WEEKS: 'Every few weeks, e.g. every other Tuesday',
    store.REPEAT_MONTHLY: 'Monthly, on a day of the month',
    store.REPEAT_YEARLY: 'Yearly, on a date',
}

# Sent on reads, ignored on writes.
READ_ONLY = ('id', 'title', 'summary', 'next', 'next_due')

# How far ahead next_due looks: past a yearly slot's next date wherever today falls.
NEXT_DUE_DAYS = 400

_TIME = re.compile(r'^(\d{1,2}):(\d{2})$')
_ISO_DATE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')


class SlotError(ValueError):
    """
    <summary>
    A slot that breaks a rule, with a message fit to show the user as it is.
    </summary>
    <remarks>
    api_server turns this into a 422 'validation' error, carrying field so the
    dashboard can mark the input at fault.
    </remarks>
    """

    def __init__(self, message, field=None):
        """
        <summary>
        Keep the message and the field it is about.
        </summary>
        <param name="message">Plain UK English, shown to the user unchanged.</param>
        <param name="field">The API field at fault, or None when it is the slot as a whole.</param>
        """
        super().__init__(message)
        self.message = message
        self.field = field


# --- Reading a stored slot -------------------------------------------------

def _kind_of(slot):
    """
    <summary>
    The slot's kind, 'favourite' for a missing or unknown one, as the scheduler reads it.
    </summary>
    <param name="slot">A stored slot dict.</param>
    <returns>One of KINDS.</returns>
    """
    kind = slot.get('kind', 'favourite')
    return kind if kind in KINDS else 'favourite'


def _seq_type_of(slot):
    """
    <summary>
    The sequence type, 'movie' for a missing or unknown one, as the scheduler reads it.
    </summary>
    <param name="slot">A stored slot dict.</param>
    <returns>One of SEQ_TYPES.</returns>
    """
    seq_type = slot.get('seq_type', 'movie')
    return seq_type if seq_type in SEQ_TYPES else 'movie'


def _int_or(value, default):
    """
    <summary>
    A stored number as an int, or the default when it is missing or not a number.
    </summary>
    <param name="value">The stored value.</param>
    <param name="default">Used when the value will not convert.</param>
    <returns>An int.</returns>
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _calendar_days(days):
    """
    <summary>
    Weekday names in calendar order, Monday first, with repeats and unknown names dropped.
    </summary>
    <param name="days">Weekday names in any order.</param>
    <returns>A new list.</returns>
    """
    return [d for d in store.DAY_NAMES if d in days]


def _readable(slot):
    """
    <summary>
    Every API field of a stored slot, each read the way the scheduler reads it.
    </summary>
    <param name="slot">A stored slot dict, possibly old or hand edited.</param>
    <returns>A dict keyed by API field name, holding API shaped values.</returns>
    <remarks>
    The defaults are the scheduler's (Slot.__init__), not new_slot's, so a slot with a
    key missing is described as it actually behaves: a slot without 'enabled', for
    instance, does not run, so it reads as disabled.
    </remarks>
    """
    return {
        'label': str(slot.get('label') or ''),
        'enabled': bool(slot.get('enabled', False)),
        'kind': _kind_of(slot),
        'favourite': str(slot.get('favourite') or ''),
        'seq_type': _seq_type_of(slot),
        'items': list(store.items_of(slot)),
        'list_name': str(slot.get('list_name') or ''),
        'list_mode': functional_lists.mode_of(slot),
        'list_sequels': functional_lists.sequels_of(slot),
        'repeat': store.repeat_of(slot),
        'days': _calendar_days(store.days_of(slot)),
        'week_interval': store.week_interval_of(slot),
        'week_start': store.week_start_of(slot).isoformat(),
        'month': store.month_of(slot),
        'month_day': store.month_day_of(slot),
        'date': str(slot.get('date') or '').strip(),
        'time': '{:02d}:{:02d}'.format(_int_or(slot.get('hour', 19), 19),
                                       _int_or(slot.get('minute', 0), 0)),
        'warning': _int_or(slot.get('warning', 5), 5),
        'timeout_action': store.timeout_action_of(slot),
        'banner_style': store.banner_style_of(slot),
        'autoopen': bool(slot.get('autoopen', False)),
        'snooze': bool(slot.get('snooze', True)),
    }


def visible_fields(values):
    """
    <summary>
    The API fields that matter for a slot, in form order.
    </summary>
    <param name="values">The slot's API values; only 'kind', 'list_mode' and 'repeat'
    are read.</param>
    <returns>A list of field names.</returns>
    <remarks>
    This is the code twin of the show_when rules in schema(). The tests check the two
    agree, because a field shown by one and hidden by the other would either never be
    sent or be sent and silently not stored.
    </remarks>
    """
    keys = ['enabled', 'kind']
    kind = values['kind']
    if kind == 'favourite':
        keys.append('favourite')
    elif kind == 'sequence':
        keys += ['seq_type', 'items']
    else:
        keys += ['list_name', 'list_mode']
        if values['list_mode'] in functional_lists.RANDOM_MODES:
            keys.append('list_sequels')
    keys += ['label', 'repeat']
    repeat = values['repeat']
    if repeat == store.REPEAT_WEEKLY:
        keys.append('days')
    elif repeat == store.REPEAT_EVERY_WEEKS:
        keys += ['days', 'week_interval', 'week_start']
    elif repeat == store.REPEAT_MONTHLY:
        keys.append('month_day')
    else:
        keys += ['month', 'month_day']
    keys += ['date', 'time', 'warning', 'timeout_action', 'banner_style', 'autoopen', 'snooze']
    return keys


def title_of(slot):
    """
    <summary>
    The name a slot goes by in a list: its label, else what it plays.
    </summary>
    <param name="slot">A stored slot dict.</param>
    <returns>Never empty.</returns>
    """
    values = _readable(slot)
    if values['label']:
        return values['label']
    if values['kind'] == 'list':
        return values['list_name'] or '(no list)'
    if values['kind'] == 'sequence':
        return 'Sequence'
    return values['favourite'] or '(no favourite)'


def summary_of(slot):
    """
    <summary>
    When a slot runs, in the dialog's own words, such as 'Weekly on Mon,Wed at 19:30'.
    </summary>
    <param name="slot">A stored slot dict.</param>
    <returns>The text.</returns>
    <remarks>
    slot_manager is imported here, not at the top, because it pulls in the dialog
    modules and nothing else in this file needs them.
    </remarks>
    """
    from resources.lib import slot_manager
    return '{} at {}'.format(slot_manager._schedule_text(slot), _readable(slot)['time'])


def next_due(slot, now):
    """
    <summary>
    When a slot next runs, from now on.
    </summary>
    <param name="slot">A stored slot dict.</param>
    <param name="now">The current time.</param>
    <returns>The datetime of the first occurrence at or after the current minute
    within NEXT_DUE_DAYS, or None when there is none (a one shot date already
    gone, say).</returns>
    <remarks>
    Uses the scheduler's own Slot.runs_on, so the dashboard's order is the
    scheduler's calendar. It does not know what the scheduler has already done
    today: a slot played or cancelled earlier this evening still reads as due at
    its time while that time is ahead. For sorting a list that is close enough,
    and it needs no access to the running service. Imported here, not at the
    top, because the scheduler pulls in the Kodi player modules.
    </remarks>
    """
    from resources.lib.scheduler import Slot
    wrapped = Slot(slot)
    floor = now.replace(second=0, microsecond=0)
    for offset in range(NEXT_DUE_DAYS):
        day = floor.date() + timedelta(days=offset)
        if not wrapped.runs_on(day):
            continue
        due = datetime(day.year, day.month, day.day, wrapped.hour, wrapped.minute)
        if due >= floor:
            return due
    return None


def next_text(due, now):
    """
    <summary>
    The list line for a slot's next occurrence, in the skin's Coming up words.
    </summary>
    <param name="due">What next_due returned.</param>
    <param name="now">The current time.</param>
    <returns>'Next: Tonight 20:00' and the like, or 'Not due in the next year'.</returns>
    """
    if due is None:
        return 'Not due in the next year'
    from resources.lib.scheduler import when_label
    return 'Next: {}'.format(when_label(due, now))


def to_api(slot, now=None):
    """
    <summary>
    A stored slot in its API shape.
    </summary>
    <param name="slot">A stored slot dict.</param>
    <param name="now">The current time, for the next occurrence; datetime.now()
    when None.</param>
    <returns>A new dict: 'id', the visible fields, then 'title', 'summary',
    'next' and 'next_due'.</returns>
    <remarks>
    'next' is the readable next occurrence the list shows under the title.
    'next_due' is the same moment as 'YYYY-MM-DDTHH:MM', empty when there is
    none: a sort key for the "Next to run" order /info declares, not in the
    schema, so JellyStat never shows or sends it (spec 5.1).
    </remarks>
    """
    now = now or datetime.now()
    values = _readable(slot)
    record = {'id': slot.get('id', 0)}
    for key in visible_fields(values):
        record[key] = copy.deepcopy(values[key])
    record['title'] = title_of(slot)
    record['summary'] = summary_of(slot)
    due = next_due(slot, now)
    record['next'] = next_text(due, now)
    record['next_due'] = due.strftime('%Y-%m-%dT%H:%M') if due else ''
    return record


# --- Checking one field of a write -----------------------------------------
# Each checker takes the sent value and the slot being built, and stores the value
# in its stored form or raises SlotError. Cross field rules wait for _settle, which
# runs once every sent field is in.

def _is_int(value):
    """
    <summary>
    True for a JSON integer. JSON true and false arrive as bool, a subclass of int, so
    they are ruled out by name.
    </summary>
    <param name="value">A value parsed from JSON.</param>
    <returns>True or False.</returns>
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _bool_checker(key, label):
    """
    <summary>
    A checker for a true or false field.
    </summary>
    <param name="key">The field, stored under the same name.</param>
    <param name="label">The field's name in the error message.</param>
    <returns>The checker function.</returns>
    """
    def check(value, slot):
        if not isinstance(value, bool):
            raise SlotError('{} must be true or false.'.format(label), key)
        slot[key] = value
    return check


def _choice_checker(key, label, choices):
    """
    <summary>
    A checker for a field whose value must be one of a fixed set.
    </summary>
    <param name="key">The field, stored under the same name.</param>
    <param name="label">The field's name in the error message.</param>
    <param name="choices">The allowed values.</param>
    <returns>The checker function.</returns>
    """
    def check(value, slot):
        if value not in choices or isinstance(value, bool):
            raise SlotError('{} is not one of the choices offered.'.format(label), key)
        slot[key] = value
    return check


def _int_checker(key, label, low, high):
    """
    <summary>
    A checker for a whole number field within a range.
    </summary>
    <param name="key">The field, stored under the same name.</param>
    <param name="label">The field's name in the error message.</param>
    <param name="low">The smallest value allowed.</param>
    <param name="high">The largest value allowed.</param>
    <returns>The checker function.</returns>
    """
    def check(value, slot):
        if not _is_int(value) or not low <= value <= high:
            raise SlotError('{} must be a whole number from {} to {}.'.format(label, low, high), key)
        slot[key] = value
    return check


def _parse_date(value, key, label):
    """
    <summary>
    A 'YYYY-MM-DD' string as a date, refusing anything that is not a real day.
    </summary>
    <param name="value">The sent value.</param>
    <param name="key">The field, for the error.</param>
    <param name="label">The field's name in the error message.</param>
    <returns>The date.</returns>
    <exception cref="SlotError">When it is not a string in that form, or names a day
    that does not exist, such as 2026-02-30.</exception>
    """
    match = _ISO_DATE.match(value.strip()) if isinstance(value, str) else None
    if not match:
        raise SlotError('{} must be a date written as YYYY-MM-DD.'.format(label), key)
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        raise SlotError('{} is not a real date.'.format(label), key)


def _check_label(value, slot):
    """
    <summary>
    The slot's own name: any text up to LABEL_MAX characters, trimmed, empty allowed.
    </summary>
    <param name="value">The sent value.</param>
    <param name="slot">The slot being built.</param>
    """
    if not isinstance(value, str):
        raise SlotError('Name must be text.', 'label')
    value = value.strip()
    if len(value) > LABEL_MAX:
        raise SlotError('Name can be at most {} characters.'.format(LABEL_MAX), 'label')
    slot['label'] = value


def _text_checker(key, label):
    """
    <summary>
    A checker for a name that points at something else (a favourite, a list), trimmed.
    </summary>
    <param name="key">The field, stored under the same name.</param>
    <param name="label">The field's name in the error message.</param>
    <returns>The checker function.</returns>
    <remarks>
    Whether the favourite or list still exists is not checked: either can be renamed
    in Kodi at any time, and the scheduler already skips a slot pointing at nothing,
    with a warning in kodi.log.
    </remarks>
    """
    def check(value, slot):
        if not isinstance(value, str):
            raise SlotError('{} must be text.'.format(label), key)
        slot[key] = value.strip()
    return check


def _check_item(item, position):
    """
    <summary>
    One sequence item, cleaned to its type, id and known display fields.
    </summary>
    <param name="item">The sent item.</param>
    <param name="position">Its place in the list, from 1, for the error message.</param>
    <returns>A new dict.</returns>
    <exception cref="SlotError">When it is not an object, has no valid type or id, or a
    display field has the wrong type.</exception>
    """
    if not isinstance(item, dict):
        raise SlotError('Item {} is not a film or episode.'.format(position), 'items')
    item_type = item.get('type')
    if item_type not in ITEM_FIELDS:
        raise SlotError('Item {} is not a film or episode.'.format(position), 'items')
    item_id = item.get('id')
    if not _is_int(item_id) or item_id < 1:
        raise SlotError('Item {} has no library id.'.format(position), 'items')
    cleaned = {'type': item_type, 'id': item_id}
    for key, kind in ITEM_FIELDS[item_type].items():
        if key not in item:
            continue
        value = item[key]
        if not isinstance(value, kind) or isinstance(value, bool):
            raise SlotError('Item {} has a bad {}.'.format(position, key), 'items')
        cleaned[key] = value
    return cleaned


def _check_items(value, slot):
    """
    <summary>
    A sequence's ordered items. Replaces the legacy 'movies' field, as the dialog does.
    </summary>
    <param name="value">The sent value.</param>
    <param name="slot">The slot being built.</param>
    <remarks>
    Whether the items suit the sequence type is a cross field rule, left to _settle.
    </remarks>
    """
    if not isinstance(value, list):
        raise SlotError('Items must be a list.', 'items')
    slot['items'] = [_check_item(item, n) for n, item in enumerate(value, 1)]
    slot.pop('movies', None)


def _check_days(value, slot):
    """
    <summary>
    One or more weekday names, stored in calendar order. Replaces the legacy single
    'day' field, as the dialog does.
    </summary>
    <param name="value">The sent value.</param>
    <param name="slot">The slot being built.</param>
    """
    if not isinstance(value, list) or not all(isinstance(d, str) for d in value):
        raise SlotError('Days must be a list of weekday names.', 'days')
    unknown = [d for d in value if d not in store.DAY_NAMES]
    if unknown:
        raise SlotError('{} is not a weekday.'.format(unknown[0]), 'days')
    days = _calendar_days(value)
    if not days:
        raise SlotError('Pick at least one day.', 'days')
    slot['days'] = days
    slot.pop('day', None)


def _check_week_start(value, slot):
    """
    <summary>
    Any date in the week of an every few weeks slot's first showing, stored as that
    week's Monday, the form the dialog stores.
    </summary>
    <param name="value">The sent value.</param>
    <param name="slot">The slot being built.</param>
    """
    day = _parse_date(value, 'week_start', 'First showing')
    slot['week_start'] = (day - timedelta(days=day.weekday())).isoformat()


def _check_date(value, slot):
    """
    <summary>
    The one off date, or '' to clear it. A set date overrides the repeat.
    </summary>
    <param name="value">The sent value.</param>
    <param name="slot">The slot being built.</param>
    """
    if value == '':
        slot['date'] = ''
        return
    slot['date'] = _parse_date(value, 'date', 'One off date').isoformat()


def _check_time(value, slot):
    """
    <summary>
    The start time as 'HH:MM', 24 hour, stored as separate 'hour' and 'minute'.
    </summary>
    <param name="value">The sent value.</param>
    <param name="slot">The slot being built.</param>
    """
    match = _TIME.match(value.strip()) if isinstance(value, str) else None
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise SlotError('Time must be written as HH:MM, from 00:00 to 23:59.', 'time')
    slot['hour'], slot['minute'] = int(match.group(1)), int(match.group(2))


_CHECKS = {
    'label': _check_label,
    'enabled': _bool_checker('enabled', 'Enabled'),
    'kind': _choice_checker('kind', 'What it plays', KINDS),
    'favourite': _text_checker('favourite', 'Favourite'),
    'seq_type': _choice_checker('seq_type', 'Sequence type', SEQ_TYPES),
    'items': _check_items,
    'list_name': _text_checker('list_name', 'Functional list'),
    'list_mode': _choice_checker('list_mode', 'What should play', functional_lists.MODES),
    'list_sequels': _choice_checker('list_sequels', 'Sequels in order', functional_lists.SEQUELS),
    'repeat': _choice_checker('repeat', 'Repeats', store.REPEATS),
    'days': _check_days,
    'week_interval': _choice_checker('week_interval', 'How often', store.WEEK_INTERVALS),
    'week_start': _check_week_start,
    'month': _int_checker('month', 'Month', 1, 12),
    'month_day': _int_checker('month_day', 'Day of the month', 1, 31),
    'date': _check_date,
    'time': _check_time,
    'warning': _int_checker('warning', 'Warning minutes', 0, WARNING_MAX),
    'timeout_action': _choice_checker('timeout_action', 'When the countdown ends',
                                      store.TIMEOUT_ACTIONS),
    'banner_style': _choice_checker('banner_style', 'Banner style', store.SLOT_BANNER_STYLES),
    'autoopen': _bool_checker('autoopen', 'Skip the banner'),
    'snooze': _bool_checker('snooze', 'Allow snooze'),
}


def _settle(slot):
    """
    <summary>
    Check the rules that span fields, on the slot with every sent field applied.
    </summary>
    <param name="slot">The slot being built; changed in place.</param>
    <exception cref="SlotError">When a rule is broken.</exception>
    <remarks>
    Only the fields the slot's kind and repeat use are required, so a favourite slot
    is never refused over a stale list field it carries from an earlier life. The
    required rules are the ones the dialog's add flows enforce: a favourite to point
    at, a named sequence with something in it, a list, at least one day, and for an
    every few weeks slot the week it starts in.
    </remarks>
    """
    values = _readable(slot)
    kind = values['kind']
    slot['kind'] = kind
    if kind == 'favourite' and not values['favourite']:
        raise SlotError('Pick a favourite.', 'favourite')
    if kind == 'sequence':
        if not values['label']:
            raise SlotError('A sequence needs a name.', 'label')
        seq_type = values['seq_type']
        items = values['items']
        if not items:
            raise SlotError('Add at least one {}.'.format(
                'film' if seq_type == 'movie' else 'episode'), 'items')
        wanted = ITEM_TYPE_FOR[seq_type]
        if any(item.get('type') != wanted for item in items):
            raise SlotError('A {} can only hold {}.'.format(
                'movie sequence' if seq_type == 'movie' else 'binge order',
                'films' if seq_type == 'movie' else 'episodes'), 'items')
        slot['seq_type'] = seq_type
        slot['items'] = items
        slot.pop('movies', None)
    if kind == 'list' and not values['list_name']:
        raise SlotError('Pick a Functional list.', 'list_name')

    repeat = values['repeat']
    if repeat == store.REPEAT_EVERY_WEEKS and not slot.get('week_start'):
        raise SlotError('Pick the week of the first showing.', 'week_start')
    if repeat == store.REPEAT_YEARLY:
        month = values['month']
        # 2000 is a leap year, so the 29th of February is allowed, as in the dialog.
        if values['month_day'] > store.last_day_of_month(2000, month):
            raise SlotError('{} has no {}.'.format(
                store.MONTH_NAMES[month - 1], store.ordinal(values['month_day'])), 'month_day')


def from_api(data, existing=None):
    """
    <summary>
    Check a slot sent over the API and return it in its stored form.
    </summary>
    <param name="data">The parsed JSON body.</param>
    <param name="existing">The stored slot being replaced, or None to create a new one,
    which starts from slots.new_slot with no favourite.</param>
    <returns>A new stored slot dict. Its 'id' is the existing one, or 0 for the caller
    to assign.</returns>
    <exception cref="SlotError">When the body is not an object or breaks a rule.</exception>
    <remarks>
    Fields not sent keep their stored values (see the module remarks). Unknown keys are
    ignored, as are the read only 'id', 'title' and 'summary', so a record read from
    the API can be sent straight back.
    </remarks>
    """
    if not isinstance(data, dict):
        raise SlotError('A slot must be a JSON object.')
    if existing is None:
        slot = store.new_slot()
    else:
        slot = copy.deepcopy(existing)
    for key, value in data.items():
        check = _CHECKS.get(key)
        if check is not None:
            check(value, slot)
    _settle(slot)
    return slot


# --- Schema ----------------------------------------------------------------

def _options(choices, labels):
    """
    <summary>
    Static schema options from a list of values and a label for each.
    </summary>
    <param name="choices">The values, in the order to offer them.</param>
    <param name="labels">A dict of value to label, or a list in the same order.</param>
    <returns>[{'value': ..., 'label': ...}].</returns>
    """
    if isinstance(labels, dict):
        labels = [labels[c] for c in choices]
    return [{'value': c, 'label': l} for c, l in zip(choices, labels)]


def schema():
    """
    <summary>
    The 'slots' resource for GET /schema, in the shared spec's field vocabulary.
    </summary>
    <returns>A dict for the 'resources' map.</returns>
    <remarks>
    The show_when rules here and visible_fields must agree; the tests hold them
    together. Option labels are the dialog's own, so a slot reads the same in Kodi
    and on the dashboard.
    </remarks>
    """
    weekly_kinds = [store.REPEAT_WEEKLY, store.REPEAT_EVERY_WEEKS]
    return {
        'slots': {
            'id_type': 'integer',
            'fields': [
                {'key': 'title', 'label': 'Slot', 'type': 'readonly'},
                {'key': 'summary', 'label': 'When', 'type': 'readonly'},
                {'key': 'next', 'label': 'Next', 'type': 'readonly'},
                {'key': 'enabled', 'label': 'Enabled', 'type': 'boolean', 'default': True},
                {'key': 'kind', 'label': 'What it plays', 'type': 'choice', 'required': True,
                 'default': 'favourite', 'options': _options(KINDS, KIND_LABELS)},
                {'key': 'favourite', 'label': 'Favourite', 'type': 'choice', 'required': True,
                 'options_from': 'favourites', 'show_when': {'kind': ['favourite']}},
                {'key': 'seq_type', 'label': 'Sequence type', 'type': 'choice',
                 'default': 'movie', 'options': _options(SEQ_TYPES, SEQ_TYPE_LABELS),
                 'show_when': {'kind': ['sequence']}},
                {'key': 'items', 'label': 'In this order', 'type': 'picks', 'required': True,
                 'options_from': 'sequence_items', 'options_params': {'seq_type': 'seq_type'},
                 'help': 'Search the library to add. A binge order searches by show name.',
                 'show_when': {'kind': ['sequence']}},
                {'key': 'list_name', 'label': 'Functional list', 'type': 'choice',
                 'required': True, 'options_from': 'lists', 'show_when': {'kind': ['list']}},
                {'key': 'list_mode', 'label': 'What should play', 'type': 'choice',
                 'default': functional_lists.DEFAULT_MODE,
                 'options': _options(functional_lists.MODES, functional_lists.MODE_LABELS),
                 'show_when': {'kind': ['list']}},
                {'key': 'list_sequels', 'label': 'Sequels in order?', 'type': 'choice',
                 'default': functional_lists.SEQUELS_OFF,
                 'options': _options(functional_lists.SEQUELS, functional_lists.SEQUEL_LABELS),
                 'show_when': {'kind': ['list'],
                               'list_mode': list(functional_lists.RANDOM_MODES)}},
                {'key': 'label', 'label': 'Name', 'type': 'string', 'max': LABEL_MAX,
                 'help': 'Leave empty to use the favourite or list name. A sequence needs one.'},
                {'key': 'repeat', 'label': 'Repeats', 'type': 'choice',
                 'default': store.DEFAULT_REPEAT,
                 'options': _options(store.REPEATS, REPEAT_LABELS)},
                {'key': 'days', 'label': 'Days', 'type': 'multichoice', 'required': True,
                 'default': ['Monday'], 'options': _options(store.DAY_NAMES, store.DAY_NAMES),
                 'show_when': {'repeat': weekly_kinds}},
                {'key': 'week_interval', 'label': 'How often', 'type': 'choice', 'default': 2,
                 'options': _options(store.WEEK_INTERVALS,
                                     ['Every {} weeks'.format(n) for n in store.WEEK_INTERVALS]),
                 'show_when': {'repeat': [store.REPEAT_EVERY_WEEKS]}},
                {'key': 'week_start', 'label': 'First showing', 'type': 'date', 'required': True,
                 'help': 'Any date in the week of the first showing; the weeks count from there.',
                 'show_when': {'repeat': [store.REPEAT_EVERY_WEEKS]}},
                {'key': 'month', 'label': 'Month', 'type': 'choice', 'default': 1,
                 'options': _options(list(range(1, 13)), store.MONTH_NAMES),
                 'show_when': {'repeat': [store.REPEAT_YEARLY]}},
                {'key': 'month_day', 'label': 'Day of the month', 'type': 'integer',
                 'min': 1, 'max': 31, 'default': 1,
                 'help': 'In a shorter month the last day is used.',
                 'show_when': {'repeat': [store.REPEAT_MONTHLY, store.REPEAT_YEARLY]}},
                {'key': 'date', 'label': 'One off date', 'type': 'date', 'default': '',
                 'help': 'When set, the slot runs on this date only and the repeat is ignored. '
                         'Empty to use the repeat.'},
                {'key': 'time', 'label': 'Time', 'type': 'time', 'required': True,
                 'default': '19:00'},
                {'key': 'warning', 'label': 'Warning minutes', 'type': 'integer',
                 'min': 0, 'max': WARNING_MAX, 'default': 5,
                 'help': 'How long before the start the warning shows; 0 for none.'},
                {'key': 'timeout_action', 'label': 'When the countdown ends', 'type': 'choice',
                 'default': store.DEFAULT_TIMEOUT_ACTION,
                 'options': _options(store.TIMEOUT_ACTIONS, store.TIMEOUT_ACTION_LABELS)},
                {'key': 'banner_style', 'label': 'Banner style', 'type': 'choice',
                 'default': store.DEFAULT_SLOT_BANNER_STYLE,
                 'options': _options(store.SLOT_BANNER_STYLES, store.BANNER_STYLE_LABELS),
                 'help': 'Default follows the Default banner style in the add-on settings.'},
                {'key': 'autoopen', 'label': 'Skip the banner, just play at the set time',
                 'type': 'boolean', 'default': False},
                {'key': 'snooze', 'label': 'Allow snooze', 'type': 'boolean', 'default': True},
            ],
        }
    }
