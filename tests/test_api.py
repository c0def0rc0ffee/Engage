#!/usr/bin/env python3
"""
<summary>
Unit test for Engage's add-on API: the slot rules and API shape (slot_rules.py), every
route and error code (api_server.handle), the ETag rules, the schema agreeing with the
rules, and a real loopback server for the Host, token, size and discovery checks.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero on the first failure
reported. No Kodi is needed: the xbmc modules are stubbed in sys.modules before the
add-on is imported, the Home window is a dict, the slot store writes into a temporary
folder, and JSON-RPC answers from a small fake library.

Usage:
    python3 tests/test_api.py
</remarks>
"""

import http.client
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, 'service.engage')
DATA_DIR = tempfile.mkdtemp(prefix='engage-api-test-')

# --- Kodi stubs, installed before the add-on modules are imported -----------

BUILTINS = []

FAVOURITES = [
    {'title': 'Star Trek: Voyager', 'type': 'window', 'window': 'videos',
     'windowparameter': 'videodb://tvshows/titles/1/', 'thumbnail': 'voyager.png'},
    {'title': 'Weather', 'type': 'script', 'path': 'script.weather'},
]
MOVIES = [{'movieid': 1, 'title': 'Alien', 'year': 1979},
          {'movieid': 2, 'title': 'Aliens', 'year': 1986}]
EPISODES = [
    {'episodeid': 12, 'showtitle': 'Flash', 'season': 1, 'episode': 2, 'title': 'B', 'firstaired': ''},
    {'episodeid': 11, 'showtitle': 'Flash', 'season': 1, 'episode': 1, 'title': 'A', 'firstaired': ''},
]
JSONRPC_CALLS = []


def fake_jsonrpc(request):
    """
    <summary>
    Answer the JSON-RPC calls the option sources make from the fake library.
    </summary>
    """
    req = json.loads(request)
    JSONRPC_CALLS.append(req)
    method = req['method']
    if method == 'Favourites.GetFavourites':
        return json.dumps({'result': {'favourites': FAVOURITES}})
    if method == 'VideoLibrary.GetMovies':
        value = (req['params'].get('filter') or {}).get('value', '')
        return json.dumps({'result': {'movies': [m for m in MOVIES if value.lower() in m['title'].lower()]}})
    if method == 'VideoLibrary.GetEpisodes':
        value = req['params']['filter']['value']
        return json.dumps({'result': {'episodes': [e for e in EPISODES
                                                   if value.lower() in e['showtitle'].lower()]}})
    raise AssertionError('unexpected JSON-RPC call: {}'.format(method))


class _Window:
    """<summary>Stand-in for xbmcgui.Window: one shared property dict.</summary>"""
    props = {}

    def __init__(self, window_id):
        self.window_id = window_id

    def getProperty(self, key):
        return self.props.get(key, '')

    def setProperty(self, key, value):
        self.props[key] = value

    def clearProperty(self, key):
        self.props.pop(key, None)


class _Addon:
    """<summary>Stand-in for xbmcaddon.Addon with the settings the API reads.</summary>"""
    settings = {'api_enabled': True, 'show_all_favourites': False, 'debug': 'false',
                'poll_interval': '30', 'catchup_hours': '6', 'recent_watch_hours': '4'}

    def __init__(self, addon_id=None):
        pass

    def getSettingBool(self, key):
        return bool(self.settings[key])

    def getSetting(self, key):
        return str(self.settings.get(key, ''))

    def setSetting(self, key, value):
        self.settings[key] = value

    def getAddonInfo(self, key):
        return ADDON_DIR if key == 'path' else '9.9.9'


xbmc = types.ModuleType('xbmc')
xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
xbmc.log = lambda msg, level=1: None
xbmc.executebuiltin = lambda cmd: BUILTINS.append(cmd)
xbmc.executeJSONRPC = fake_jsonrpc
xbmc.Monitor = object
xbmc.Player = object

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

from resources.lib import api_server  # noqa: E402
from resources.lib import functional_lists  # noqa: E402
from resources.lib import slot_rules  # noqa: E402
from resources.lib import slots as store  # noqa: E402
from resources.lib.slot_rules import SlotError, from_api, to_api  # noqa: E402

# --- Helpers ------------------------------------------------------------------

FAILURES = []


def check(condition, message):
    """<summary>Record a failure without stopping, so one run reports them all.</summary>"""
    if not condition:
        FAILURES.append(message)
        print('FAIL: {}'.format(message))


def equal(actual, expected, what):
    """<summary>check() for equality, printing both sides on failure.</summary>"""
    check(actual == expected, '{}: expected {!r}, got {!r}'.format(what, expected, actual))


def refused(data, field, what, existing=None):
    """
    <summary>
    Check from_api refuses a slot, naming the field at fault.
    </summary>
    """
    try:
        from_api(data, existing)
    except SlotError as e:
        equal(e.field, field, '{}: field named'.format(what))
        check(bool(e.message), '{}: has a message'.format(what))
        return
    check(False, '{}: was accepted'.format(what))


def api_error(method, path, body=None, if_match=None, query=None):
    """
    <summary>
    Run handle() expecting an error; returns (status, code), or (None, None) if it
    succeeded.
    </summary>
    """
    try:
        api_server.handle(method, path, query or {}, body, if_match)
    except api_server.ApiError as e:
        return e.status, e.code
    return None, None


def current_etag():
    """<summary>The ETag of slots.json as it stands.</summary>"""
    return api_server.etag_of(store.load_slots())


def fav(**extra):
    """<summary>A minimal valid favourite slot body, with overrides.</summary>"""
    body = {'kind': 'favourite', 'favourite': 'Star Trek: Voyager', 'enabled': True,
            'days': ['Thursday'], 'time': '19:30'}
    body.update(extra)
    return body


def write_lists(lists):
    """<summary>Write a Functional lists.json where functional_lists looks for it.</summary>"""
    path = functional_lists.lists_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'last': '', 'lists': lists}, f)


# --- slot_rules -----------------------------------------------------------------

def test_rules_create():
    """<summary>A new favourite slot: defaults filled, time split, days ordered.</summary>"""
    print('running test_rules_create')
    slot = from_api(fav(days=['Friday', 'Monday', 'Friday']))
    equal(slot['kind'], 'favourite', 'kind')
    equal(slot['favourite'], 'Star Trek: Voyager', 'favourite')
    equal((slot['hour'], slot['minute']), (19, 30), 'time stored as hour and minute')
    equal(slot['days'], ['Monday', 'Friday'], 'days in calendar order, repeats dropped')
    equal(slot['warning'], 5, 'warning default from new_slot')
    equal(slot['id'], 0, 'id left for the caller')

    refused({'kind': 'favourite'}, 'favourite', 'favourite slot with no favourite')
    refused(fav(time='24:00'), 'time', 'time past 23:59')
    refused(fav(time='7.30'), 'time', 'time in the wrong form')
    refused(fav(days=[]), 'days', 'no days')
    refused(fav(days=['Funday']), 'days', 'unknown weekday')
    refused(fav(enabled=1), 'enabled', 'a number for a true or false field')
    refused(fav(warning=31), 'warning', 'warning beyond the picker')
    refused(fav(warning=True), 'warning', 'true for a number')
    refused(fav(label='x' * 101), 'label', 'label too long')
    refused(fav(timeout_action='explode'), 'timeout_action', 'unknown timeout action')
    refused(fav(kind='film'), 'kind', 'unknown kind')
    refused(fav(date='2026-02-30'), 'date', 'a date that does not exist')
    refused(['not', 'an', 'object'], None, 'a body that is not an object')

    slot = from_api(fav(id=99, title='ignored', summary='ignored', colour='blue'))
    equal(slot['id'], 0, 'id in the body is ignored')
    check('colour' not in slot and 'title' not in slot, 'unknown and read only keys not stored')
    equal(from_api(fav(date=' 2026-12-25 '))['date'], '2026-12-25', 'date trimmed and kept')
    equal(from_api(fav(date=''))['date'], '', 'empty date clears it')


def test_rules_repeats():
    """<summary>Every few weeks, monthly and yearly rules.</summary>"""
    print('running test_rules_repeats')
    slot = from_api(fav(repeat='every_weeks', week_interval=3, week_start='2026-10-15'))
    equal(slot['week_start'], '2026-10-12', 'week_start moved back to its Monday')
    equal(slot['week_interval'], 3, 'week_interval kept')
    refused(fav(repeat='every_weeks', week_interval=3), 'week_start',
            'every few weeks without a first week')
    refused(fav(repeat='every_weeks', week_start='2026-10-12', week_interval=5),
            'week_interval', 'interval not offered')

    equal(from_api(fav(repeat='monthly', month_day=31))['month_day'], 31, 'monthly 31st allowed')
    refused(fav(repeat='monthly', month_day=0), 'month_day', 'monthly 0th')
    equal(from_api(fav(repeat='yearly', month=2, month_day=29))['month'], 2,
          'yearly 29th February allowed, as in the dialog')
    refused(fav(repeat='yearly', month=4, month_day=31), 'month_day', 'yearly 31st April')
    refused(fav(repeat='yearly', month=13, month_day=1), 'month', 'month 13')


def test_rules_sequences_and_lists():
    """<summary>Sequence items, labels and list slot rules.</summary>"""
    print('running test_rules_sequences_and_lists')
    movie = {'type': 'movie', 'id': 1, 'title': 'Alien', 'year': 1979, 'junk': 'x'}
    slot = from_api({'kind': 'sequence', 'label': 'Alien films', 'items': [movie], 'time': '20:00'})
    equal(slot['seq_type'], 'movie', 'seq_type defaults to movie')
    equal(slot['items'], [{'type': 'movie', 'id': 1, 'title': 'Alien', 'year': 1979}],
          'item cleaned to its known fields')
    refused({'kind': 'sequence', 'items': [movie]}, 'label', 'sequence without a name')
    refused({'kind': 'sequence', 'label': 'S', 'items': []}, 'items', 'empty sequence')
    refused({'kind': 'sequence', 'label': 'S', 'seq_type': 'binge', 'items': [movie]},
            'items', 'a film in a binge order')
    refused({'kind': 'sequence', 'label': 'S', 'items': [{'type': 'movie', 'id': 0}]},
            'items', 'an item with no library id')
    refused({'kind': 'sequence', 'label': 'S', 'items': [{'type': 'movie', 'id': 1, 'year': '1979'}]},
            'items', 'an item display field of the wrong type')

    slot = from_api({'kind': 'list', 'list_name': ' Halloween ', 'list_mode': 'random',
                     'list_sequels': 'date'})
    equal(slot['list_name'], 'Halloween', 'list name trimmed')
    equal(slot['list_sequels'], 'date', 'sequels kept')
    refused({'kind': 'list'}, 'list_name', 'list slot with no list')
    refused({'kind': 'list', 'list_name': 'L', 'list_mode': 'shuffle'}, 'list_mode', 'unknown mode')


def test_rules_edit_keeps_unsent():
    """<summary>An edit changes only what it sends, and migrates legacy fields it touches.</summary>"""
    print('running test_rules_edit_keeps_unsent')
    stored = {'id': 4, 'kind': 'favourite', 'favourite': 'Old', 'enabled': True, 'day': 'Tuesday',
              'hour': 8, 'minute': 5, 'future_key': {'kept': True}, 'list_mode': 'nonsense'}
    slot = from_api({'time': '21:15'}, stored)
    equal(slot['future_key'], {'kept': True}, 'a key this module does not know survives')
    equal(slot['day'], 'Tuesday', 'legacy day left alone when days are not sent')
    equal(slot['favourite'], 'Old', 'favourite kept')
    equal((slot['hour'], slot['minute']), (21, 15), 'time changed')
    equal(slot['list_mode'], 'nonsense', 'a stale field for another kind is not checked')
    equal(stored['hour'], 8, 'the stored dict passed in is not changed')

    slot = from_api({'days': ['Wednesday']}, stored)
    check('day' not in slot, 'legacy day dropped once days are written')

    legacy = {'id': 5, 'kind': 'sequence', 'label': 'Old films', 'enabled': True,
              'movies': [{'id': 7, 'title': 'Heat', 'year': 1995}]}
    slot = from_api({'enabled': False}, legacy)
    equal(slot['items'], [{'type': 'movie', 'id': 7, 'title': 'Heat', 'year': 1995}],
          'legacy movies turned into items')
    check('movies' not in slot, 'legacy movies dropped')


def test_to_api():
    """<summary>The API shape: visible fields only, time joined, legacy read through.</summary>"""
    print('running test_to_api')
    stored = {'id': 3, 'kind': 'favourite', 'favourite': 'Voyager', 'day': 'Sunday', 'hour': 7,
              'minute': 5, 'enabled': True, 'list_name': 'stale'}
    record = to_api(stored)
    equal(record['id'], 3, 'id')
    equal(record['time'], '07:05', 'time joined and padded')
    equal(record['days'], ['Sunday'], 'legacy day read as days')
    equal(record['title'], 'Voyager', 'title falls back to the favourite')
    check('list_name' not in record and 'week_start' not in record, 'fields for other kinds hidden')
    check(record['summary'].endswith('at 07:05'), 'summary carries the time: {}'.format(record['summary']))
    equal(to_api({'id': 1})['enabled'], False, 'a slot without enabled reads as off, as it runs')

    record = to_api({'id': 2, 'kind': 'list', 'list_name': 'L', 'list_mode': 'random',
                     'repeat': 'every_weeks', 'week_start': '2026-10-14'})
    check('list_sequels' in record, 'sequels shown for a random list slot')
    equal(record['week_start'], '2026-10-12', 'week_start reported as its Monday')
    check('list_sequels' not in to_api({'id': 2, 'kind': 'list', 'list_name': 'L'}),
          'sequels hidden for an in order list slot')

    # A record read from the API goes straight back unchanged.
    stored = from_api(fav(repeat='yearly', month=12, month_day=25, label='Christmas'))
    stored['id'] = 8
    again = from_api(to_api(stored), stored)
    equal(again, stored, 'a read record sent back changes nothing')


def test_schema_matches_rules():
    """<summary>The schema's fields, show_when rules and checkers agree with the code.</summary>"""
    print('running test_schema_matches_rules')
    fields = slot_rules.schema()['slots']['fields']
    keys = [f['key'] for f in fields]
    equal(len(keys), len(set(keys)), 'no field listed twice')
    writable = {f['key'] for f in fields if f['type'] != 'readonly'}
    equal(writable, set(slot_rules._CHECKS), 'every writable field has a checker and no more')
    known_types = {'string', 'integer', 'boolean', 'choice', 'multichoice', 'time', 'date',
                   'picks', 'readonly'}
    for f in fields:
        check(f['type'] in known_types, '{} has a spec field type'.format(f['key']))

    # Every combination of the fields show_when reads: the schema and visible_fields
    # must show the same fields.
    for kind in slot_rules.KINDS:
        for mode in functional_lists.MODES:
            for repeat in store.REPEATS:
                values = {'kind': kind, 'list_mode': mode, 'repeat': repeat}
                shown = set()
                for f in fields:
                    rule = f.get('show_when') or {}
                    if f['type'] != 'readonly' and all(values.get(k) in v for k, v in rule.items()):
                        shown.add(f['key'])
                equal(shown, set(slot_rules.visible_fields(values)),
                      'schema and visible_fields agree for {}'.format(values))


# --- api_server.handle -----------------------------------------------------------

def test_routes():
    """<summary>Every route, the ETag rules and the error codes.</summary>"""
    print('running test_routes')
    store.save_slots([])
    BUILTINS.clear()

    status, payload, _ = api_server.handle('GET', '/info', {}, None, None)
    equal((status, payload['api'], payload['id']), (200, 1, 'service.engage'), '/info')
    status, payload, _ = api_server.handle('GET', '/schema', {}, None, None)
    check('slots' in payload['resources'], '/schema has slots')

    status, payload, headers = api_server.handle('GET', '/slots', {}, None, None)
    equal((status, payload), (200, {'items': []}), 'empty list')
    etag = headers['ETag']

    equal(api_error('POST', '/slots', fav()), (428, 'precondition_required'), 'create without If-Match')
    equal(api_error('POST', '/slots', fav(), '"stale"'), (412, 'stale'), 'create with a stale ETag')
    equal(api_error('POST', '/slots', {'kind': 'favourite'}, etag), (422, 'validation'),
          'create an invalid slot')

    status, record, headers = api_server.handle('POST', '/slots', {}, fav(label='Voyager night'), etag)
    equal((status, record['id'], record['label']), (201, 1, 'Voyager night'), 'create')
    check(headers['ETag'] != etag, 'create moves the ETag')
    equal(headers['ETag'], current_etag(), 'the returned ETag is the stored one')
    check(any('slots_saved' in b for b in BUILTINS), 'the save is announced for the service')

    equal(api_error('POST', '/slots', fav(), etag), (412, 'stale'), 'old ETag refused after a change')
    etag = headers['ETag']
    status, record, headers = api_server.handle('POST', '/slots', {}, fav(favourite='Weather'), etag)
    equal(record['id'], 2, 'second slot gets the next id')
    etag = headers['ETag']

    status, record, _ = api_server.handle('GET', '/slots/1', {}, None, None)
    equal((status, record['favourite']), (200, 'Star Trek: Voyager'), 'read one')
    equal(api_error('GET', '/slots/9'), (404, 'not_found'), 'read a missing slot')
    status, record, _ = api_server.handle('GET', '/slots/new', {}, None, None)
    equal((status, record['kind'], record['time']), (200, 'favourite', '19:00'), '/slots/new defaults')
    equal(len(store.load_slots()), 2, '/slots/new saves nothing')

    status, record, headers = api_server.handle('PUT', '/slots/1', {}, {'time': '22:45', 'id': 50}, etag)
    equal((status, record['id'], record['time']), (200, 1, '22:45'), 'edit keeps the path id')
    etag = headers['ETag']
    equal(api_error('PUT', '/slots/9', {'time': '10:00'}, etag), (404, 'not_found'), 'edit a missing slot')
    equal(api_error('PUT', '/slots/1', {'time': 'noon'}, etag), (422, 'validation'), 'invalid edit')

    # The dialog saves meanwhile: the dashboard's ETag is now stale.
    slots = store.load_slots()
    slots[1]['label'] = 'Changed in Kodi'
    store.save_slots(slots)
    equal(api_error('PUT', '/slots/1', {'time': '10:00'}, etag), (412, 'stale'),
          'an edit made in the dialog meanwhile is not overwritten')
    etag = current_etag()

    status, payload, headers = api_server.handle('DELETE', '/slots/2', {}, None, etag)
    equal((status, payload), (204, None), 'delete')
    equal([s['id'] for s in store.load_slots()], [1], 'slot 2 gone')
    equal(api_error('DELETE', '/slots/2', None, headers['ETag']), (404, 'not_found'),
          'delete a missing slot')

    equal(api_error('PATCH', '/slots/1'), (405, 'method_not_allowed'), 'unoffered method')
    equal(api_error('DELETE', '/slots'), (405, 'method_not_allowed'), 'delete the collection')
    equal(api_error('GET', '/nothing'), (404, 'not_found'), 'unknown path')
    equal(api_error('GET', '/options/nothing'), (404, 'not_found'), 'unknown option source')


def test_backup_restore():
    """<summary>Backup carries a download name; restore checks shape and filters settings.</summary>"""
    print('running test_backup_restore')
    store.save_slots([from_api(fav())])
    slots = store.load_slots()
    slots[0]['id'] = 1
    store.save_slots(slots)

    status, backup, headers = api_server.handle('GET', '/backup', {}, None, None)
    equal((status, backup['type']), (200, 'engage-backup'), 'backup document')
    check(headers['Content-Disposition'].startswith('attachment; filename="engage-backup-'),
          'backup has a download name')
    equal(headers['ETag'], current_etag(), 'backup carries the ETag')

    etag = headers['ETag']
    equal(api_error('POST', '/restore', {'type': 'something'}, etag), (422, 'validation'),
          'restore a document that is not a backup')
    equal(api_error('POST', '/restore', {'type': 'engage-backup', 'slots': [{'id': 1}, {'id': 1}]}, etag),
          (422, 'validation'), 'restore with duplicate ids')
    equal(api_error('POST', '/restore', {'type': 'engage-backup', 'slots': [{}]}, etag),
          (422, 'validation'), 'restore a slot with no id')
    equal(api_error('POST', '/restore', backup), (428, 'precondition_required'), 'restore without If-Match')

    doc = json.loads(json.dumps(backup))
    doc['slots'].append(dict(doc['slots'][0], id=2, label='Second'))
    doc['settings'] = {'debug': 'true', 'api_enabled': 'false'}
    status, payload, _ = api_server.handle('POST', '/restore', {}, doc, etag)
    equal((status, payload), (200, {'restored': 2}), 'restore')
    equal([s['id'] for s in store.load_slots()], [1, 2], 'restored slots stored')
    equal(_Addon.settings['debug'], 'true', 'a backed up setting restored')
    equal(_Addon.settings['api_enabled'], True, 'a setting a backup never holds is not touched')


def test_options():
    """<summary>The option sources ask Kodi and shape the choices.</summary>"""
    print('running test_options')
    payload = api_server.options('favourites', {})
    equal([c['value'] for c in payload['items']], ['Star Trek: Voyager'],
          'favourites filtered to video ones, as in the dialog')
    equal(payload['more'], False, 'no more favourites')
    equal(api_server.options('favourites', {'q': ['zzz']})['items'], [], 'favourites searched')

    write_lists([{'name': 'Halloween', 'items': [{'label': 'A', 'file': 'a.mkv'}]}])
    payload = api_server.options('lists', {})
    equal([(c['value'], c['detail']) for c in payload['items']], [('Halloween', '1 items')], 'lists')

    payload = api_server.options('sequence_items', {'q': ['alien']})
    equal([c['value']['id'] for c in payload['items']], [1, 2], 'films searched')
    equal(payload['items'][0]['value']['type'], 'movie', 'film value is a stored item')

    JSONRPC_CALLS.clear()
    payload = api_server.options('sequence_items', {'seq_type': ['binge']})
    equal(payload, {'items': [], 'more': True}, 'a binge search with no show name asks for one')
    equal(JSONRPC_CALLS, [], 'and does not ask the library')
    payload = api_server.options('sequence_items', {'seq_type': ['binge'], 'q': ['flash']})
    equal([c['value']['id'] for c in payload['items']], [11, 12], 'episodes in season and episode order')
    equal(JSONRPC_CALLS[-1]['params']['filter'], {'field': 'tvshow', 'operator': 'contains', 'value': 'flash'},
          'episodes filtered by show name')
    item = payload['items'][0]['value']
    slot = from_api({'kind': 'sequence', 'seq_type': 'binge', 'label': 'B', 'items': [item]})
    equal(slot['items'][0]['id'], 11, 'an episode choice is accepted as a binge item')

    status, code = api_error('GET', '/options/sequence_items', query={'seq_type': ['tv']})
    equal((status, code), (400, 'bad_request'), 'unknown seq_type')

    old_limit = api_server.OPTIONS_LIMIT
    api_server.OPTIONS_LIMIT = 1
    try:
        payload = api_server.options('sequence_items', {'q': ['alien']})
        equal((len(payload['items']), payload['more']), (1, True), 'capped with more set')
    finally:
        api_server.OPTIONS_LIMIT = old_limit


# --- A real server ---------------------------------------------------------------

def request(port, method, path, token=None, host=None, body=None, headers=None):
    """
    <summary>
    One request to the loopback server; returns (status, parsed body or None, headers).
    </summary>
    """
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    sent = {'Host': host or '127.0.0.1:{}'.format(port)}
    if token is not None:
        sent[api_server.TOKEN_HEADER] = token
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')
        sent['Content-Type'] = 'application/json'
    sent.update(headers or {})
    try:
        conn.putrequest(method, path, skip_host=True)
        for name, value in sent.items():
            conn.putheader(name, value)
        if data is not None and 'Content-Length' not in sent:
            conn.putheader('Content-Length', str(len(data)))
        conn.endheaders(data)
        response = conn.getresponse()
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None), dict(response.getheaders())
    finally:
        conn.close()


def test_server():
    """<summary>Bind, discovery, Host and token checks, size limit, setting and stop.</summary>"""
    print('running test_server')
    store.save_slots([])
    _Window.props.clear()
    _Addon.settings['api_enabled'] = False
    api_server.sync()
    check(api_server.DISCOVERY_PROPERTY not in _Window.props, 'setting off: nothing announced')

    _Addon.settings['api_enabled'] = True
    api_server.sync()
    try:
        announced = json.loads(_Window.props.get(api_server.DISCOVERY_PROPERTY, '{}'))
        equal(sorted(announced), ['api', 'port', 'token', 'version'], 'discovery keys')
        equal(announced.get('api'), 1, 'discovery api version')
        port, token = announced['port'], announced['token']
        check(len(token) >= 40, 'token is long enough')
        api_server.sync()
        equal(json.loads(_Window.props[api_server.DISCOVERY_PROPERTY])['token'], token,
              'a second sync leaves the running server alone')

        status, body, headers = request(port, 'GET', '/info', token)
        equal((status, body['id']), (200, 'service.engage'), 'info over HTTP')
        equal(headers.get('Cache-Control'), 'no-store', 'responses are not cached')

        status, body, _ = request(port, 'GET', '/info')
        equal((status, body['error']['code']), (401, 'unauthorised'), 'no token')
        status, body, _ = request(port, 'GET', '/info', token[:-1] + ('a' if token[-1] != 'a' else 'b'))
        equal(status, 401, 'wrong token')
        status, body, _ = request(port, 'GET', '/info', token, host='evil.example:{}'.format(port))
        equal((status, body['error']['code']), (400, 'bad_request'), 'wrong Host refused')

        status, body, headers = request(port, 'GET', '/slots', token)
        etag = headers['ETag']
        status, body, headers = request(port, 'POST', '/slots', token, body=fav(),
                                        headers={'If-Match': etag})
        equal((status, body['id']), (201, 1), 'create over HTTP')
        status, body, _ = request(port, 'POST', '/slots', token, body=b'{not json',
                                  headers={'If-Match': headers['ETag']})
        equal((status, body['error']['code']), (400, 'bad_request'), 'body that is not JSON')
        status, body, _ = request(port, 'POST', '/slots', token, body=b'{}',
                                  headers={'Content-Length': str(api_server.MAX_BODY + 1)})
        equal((status, body['error']['code']), (413, 'too_large'), 'oversized body refused unread')
        status, body, headers = request(port, 'PUT', '/slots/1', token, body={'time': 'x'},
                                        headers={'If-Match': current_etag()})
        equal((status, body['error'].get('field')), (422, 'time'), 'validation names the field')
        status, body, headers = request(port, 'DELETE', '/slots/1', token,
                                        headers={'If-Match': current_etag()})
        equal((status, body), (204, None), 'delete over HTTP')
    finally:
        _Addon.settings['api_enabled'] = False
        api_server.sync()
    check(api_server.DISCOVERY_PROPERTY not in _Window.props, 'stop withdraws the announcement')
    try:
        request(port, 'GET', '/info', token)
        check(False, 'server still answering after stop')
    except OSError:
        pass
    _Addon.settings['api_enabled'] = True


def main():
    """
    <summary>
    Run every test and return the process exit code: 0 when all passed, 1 otherwise.
    </summary>
    """
    for test in (test_rules_create, test_rules_repeats, test_rules_sequences_and_lists,
                 test_rules_edit_keeps_unsent, test_to_api, test_schema_matches_rules,
                 test_routes, test_backup_restore, test_options, test_server):
        test()
    if FAILURES:
        print('test_api: {} failure(s)'.format(len(FAILURES)))
        return 1
    print('test_api: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
