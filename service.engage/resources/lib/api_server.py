"""
<summary>
Engage's add-on API: a small HTTP server on 127.0.0.1 through which the JellyStat
dashboard lists, creates, edits and deletes slots, and takes or restores a backup.
</summary>
<remarks>
This implements version 1 of the shared add-on API spec (ADDON-API-SPEC.md, kept
beside the Kodi projects). In short:

- It binds to 127.0.0.1 on a port the OS picks, and never to the LAN. JellyStat,
  running in the same Kodi, proxies to it behind its own password, so nothing new is
  reachable from the network.
- It announces itself in the Home window property AddonApi.service.engage as JSON
  holding the port and a token made fresh each start. The token is never written to
  disk or logged. Every request must carry it as X-Addon-Api-Token.
- Every write must carry If-Match with the collection's ETag, a hash of slots.json
  as stored, so an edit made in the Manage slots dialog meanwhile is not silently
  overwritten.

The scheduler calls sync() once the GUI is up, again whenever the add-on's settings
change, and stop() on the way out. It must not start earlier: Home window writes
during Kodi's start up are implicated in a crash (see EngageScheduler.__init__).

Writes go through slots.save_slots, the same path the dialog uses, so the running
service republishes the next slot for skins straight away. handle() does the
routing without a socket, which is what the tests drive.
</remarks>
"""

import hashlib
import hmac
import json
import re
import secrets
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import slot_rules
from resources.lib import slots as store

ADDON_ID = 'service.engage'
API_VERSION = 1
HOME_WINDOW_ID = 10000
DISCOVERY_PROPERTY = 'AddonApi.' + ADDON_ID
SETTING_ID = 'api_enabled'
TOKEN_HEADER = 'X-Addon-Api-Token'

# Larger bodies are refused before they are read. A backup of a few hundred slots
# is well under this.
MAX_BODY = 1024 * 1024

# Most choices an options source returns; beyond it the response says 'more' and the
# dashboard asks the user to narrow the search.
OPTIONS_LIMIT = 200

_SLOT_PATH = re.compile(r'^/slots/(\d+)$')

# The running server, None when stopped. _state_lock guards starting and stopping;
# _store_lock makes read, check, write on slots.json one step for this process. The
# dialog runs in another interpreter and cannot take it, which leaves a window of
# milliseconds between the ETag check and the rename; the spec accepts that.
_server = None
_state_lock = threading.Lock()
_store_lock = threading.Lock()


class ApiError(Exception):
    """
    <summary>
    An error response in the spec's shape: a status, a code and a message for the user.
    </summary>
    """

    def __init__(self, status, code, message, field=None, headers=None):
        """
        <summary>
        Keep everything the error response needs.
        </summary>
        <param name="status">The HTTP status.</param>
        <param name="code">The spec's error code, such as 'stale'.</param>
        <param name="message">Plain UK English, shown to the user unchanged.</param>
        <param name="field">The field at fault, for a validation error.</param>
        <param name="headers">Extra response headers, such as Allow on a 405.</param>
        """
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.headers = headers or {}


def _log(msg, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to kodi.log under the 'Engage API:' prefix.
    </summary>
    <param name="msg">Text to log. Never the token.</param>
    <param name="level">Kodi log level, LOGINFO by default.</param>
    """
    xbmc.log('Engage API: {}'.format(msg), level)


# --- Starting and stopping -------------------------------------------------

def sync():
    """
    <summary>
    Start or stop the server to match the 'Allow JellyStat to manage slots' setting.
    </summary>
    <remarks>
    Safe to call at any time and as often as wanted: starting a running server or
    stopping a stopped one does nothing. A setting that cannot be read leaves the
    server stopped, the cautious answer.
    </remarks>
    """
    try:
        enabled = xbmcaddon.Addon(ADDON_ID).getSettingBool(SETTING_ID)
    except Exception as e:
        _log('could not read the {} setting, leaving the API off: {}'.format(SETTING_ID, e),
             xbmc.LOGWARNING)
        enabled = False
    if enabled:
        start()
    else:
        stop()


def start():
    """
    <summary>
    Bind the server on a free loopback port, serve on a daemon thread and announce it.
    </summary>
    <remarks>
    The announcement goes up only after the bind succeeds, so the property never names
    a port nobody is listening on.
    </remarks>
    """
    global _server
    with _state_lock:
        if _server is not None:
            return
        server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        server.daemon_threads = True
        server.api_token = secrets.token_urlsafe(32)
        threading.Thread(target=server.serve_forever, name='EngageApi', daemon=True).start()
        _server = server
        port = server.server_address[1]
        xbmcgui.Window(HOME_WINDOW_ID).setProperty(DISCOVERY_PROPERTY, json.dumps({
            'api': API_VERSION,
            'port': port,
            'token': server.api_token,
            'version': _addon_version(),
        }))
        _log('listening on 127.0.0.1:{}'.format(port))


def stop():
    """
    <summary>
    Withdraw the announcement and shut the server down.
    </summary>
    <remarks>
    The property is cleared first, so JellyStat stops sending before the socket closes.
    shutdown() waits for the serve loop to finish, which takes at most half a second.
    </remarks>
    """
    global _server
    with _state_lock:
        if _server is None:
            return
        server, _server = _server, None
        try:
            xbmcgui.Window(HOME_WINDOW_ID).clearProperty(DISCOVERY_PROPERTY)
        except Exception as e:
            _log('could not clear {}: {}'.format(DISCOVERY_PROPERTY, e), xbmc.LOGWARNING)
        server.shutdown()
        server.server_close()
        _log('stopped')


def _addon_version():
    """
    <summary>
    The running add-on version, or '' when Kodi will not say.
    </summary>
    """
    try:
        return xbmcaddon.Addon(ADDON_ID).getAddonInfo('version') or ''
    except Exception:
        return ''


# --- Routing ---------------------------------------------------------------

def etag_of(slots):
    """
    <summary>
    The collection's ETag: a short hash of the slot list as stored, in quotes.
    </summary>
    <param name="slots">The slot dicts from load_slots.</param>
    <returns>The ETag header value.</returns>
    <remarks>
    Keys are sorted so the hash depends on what is stored, not the order a dict
    happens to hold it in. Any change from any route, the dialog included, changes it.
    </remarks>
    """
    blob = json.dumps(slots, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return '"{}"'.format(hashlib.sha256(blob).hexdigest()[:20])


def _check_match(slots, if_match):
    """
    <summary>
    Refuse a write that does not carry the current ETag.
    </summary>
    <param name="slots">The slot list as it stands now.</param>
    <param name="if_match">The request's If-Match header, or None.</param>
    <exception cref="ApiError">428 when it is missing, 412 when it is stale.</exception>
    """
    if not if_match:
        raise ApiError(428, 'precondition_required',
                       'This change has to say which version of the slots it was made against.')
    if if_match.strip() != etag_of(slots):
        raise ApiError(412, 'stale',
                       'The slots were changed somewhere else. Reload and try again.')


def _allow(method, allowed):
    """
    <summary>
    Refuse a method the path does not offer.
    </summary>
    <param name="method">The request method.</param>
    <param name="allowed">The methods the path offers.</param>
    <exception cref="ApiError">405, with an Allow header.</exception>
    """
    if method not in allowed:
        raise ApiError(405, 'method_not_allowed', '{} is not offered here.'.format(method),
                       headers={'Allow': ', '.join(allowed)})


def _validation(error):
    """
    <summary>
    A rule broken by a slot, as the spec's 422 error.
    </summary>
    <param name="error">The SlotError raised by slot_rules.</param>
    <returns>The ApiError to raise.</returns>
    """
    return ApiError(422, 'validation', error.message, error.field)


def info():
    """
    <summary>
    The body of GET /info.
    </summary>
    """
    return {
        'api': API_VERSION,
        'id': ADDON_ID,
        'name': 'Engage',
        'version': _addon_version(),
        'resources': [{'name': 'slots', 'label': 'Slots', 'label_one': 'Slot',
                       'title_field': 'title'}],
        'backup': True,
        'restore': True,
    }


def handle(method, path, query, body, if_match):
    """
    <summary>
    Route one request that has already passed the Host and token checks.
    </summary>
    <param name="method">'GET', 'POST', 'PUT' or 'DELETE'.</param>
    <param name="path">The URL path, without the query.</param>
    <param name="query">The parsed query, as parse_qs returns it.</param>
    <param name="body">The parsed JSON body, or None for GET and DELETE.</param>
    <param name="if_match">The If-Match header, or None.</param>
    <returns>(status, payload or None, extra headers).</returns>
    <exception cref="ApiError">For every error response.</exception>
    """
    if path == '/info':
        _allow(method, ['GET'])
        return 200, info(), {}
    if path == '/schema':
        _allow(method, ['GET'])
        return 200, {'resources': slot_rules.schema()}, {}
    if path == '/slots':
        _allow(method, ['GET', 'POST'])
        if method == 'GET':
            slots = store.load_slots()
            return 200, {'items': [slot_rules.to_api(s) for s in slots]}, {'ETag': etag_of(slots)}
        return _create(body, if_match)
    if path == '/slots/new':
        _allow(method, ['GET'])
        return 200, slot_rules.to_api(store.new_slot()), {}
    match = _SLOT_PATH.match(path)
    if match:
        _allow(method, ['GET', 'PUT', 'DELETE'])
        slot_id = int(match.group(1))
        if method == 'GET':
            slots = store.load_slots()
            slot = store.get_slot(slots, slot_id)
            if slot is None:
                raise ApiError(404, 'not_found', 'There is no slot {}.'.format(slot_id))
            return 200, slot_rules.to_api(slot), {'ETag': etag_of(slots)}
        if method == 'PUT':
            return _replace(slot_id, body, if_match)
        return _delete(slot_id, if_match)
    if path.startswith('/options/'):
        _allow(method, ['GET'])
        return 200, options(path[len('/options/'):], query), {}
    if path == '/backup':
        _allow(method, ['GET'])
        backup = store.build_backup()
        name = 'engage-backup-{}.json'.format(datetime.now().strftime('%Y%m%d-%H%M%S'))
        return 200, backup, {'ETag': etag_of(backup['slots']),
                             'Content-Disposition': 'attachment; filename="{}"'.format(name)}
    if path == '/restore':
        _allow(method, ['POST'])
        return _restore(body, if_match)
    raise ApiError(404, 'not_found', 'Nothing is served at {}.'.format(path))


def _create(body, if_match):
    """
    <summary>
    POST /slots: check the new slot, give it the next free id and save.
    </summary>
    <param name="body">The parsed JSON body.</param>
    <param name="if_match">The If-Match header, or None.</param>
    <returns>201 with the stored slot.</returns>
    """
    with _store_lock:
        slots = store.load_slots()
        _check_match(slots, if_match)
        try:
            slot = slot_rules.from_api(body)
        except slot_rules.SlotError as e:
            raise _validation(e)
        slot['id'] = store.next_id(slots)
        slots.append(slot)
        store.save_slots(slots)
    _log('added slot id={} over the API'.format(slot['id']))
    return 201, slot_rules.to_api(slot), {'ETag': etag_of(slots)}


def _replace(slot_id, body, if_match):
    """
    <summary>
    PUT /slots/{id}: check the changes against the stored slot and save it in place.
    </summary>
    <param name="slot_id">The id from the path; an id in the body is ignored.</param>
    <param name="body">The parsed JSON body.</param>
    <param name="if_match">The If-Match header, or None.</param>
    <returns>200 with the stored slot.</returns>
    <remarks>
    The ETag is checked before the id is looked up, so a slot deleted elsewhere since
    the dashboard loaded reads as stale, prompting a reload, rather than as missing.
    </remarks>
    """
    with _store_lock:
        slots = store.load_slots()
        _check_match(slots, if_match)
        index = next((i for i, s in enumerate(slots) if s.get('id') == slot_id), None)
        if index is None:
            raise ApiError(404, 'not_found', 'There is no slot {}.'.format(slot_id))
        try:
            slot = slot_rules.from_api(body, slots[index])
        except slot_rules.SlotError as e:
            raise _validation(e)
        slot['id'] = slot_id
        slots[index] = slot
        store.save_slots(slots)
    _log('changed slot id={} over the API'.format(slot_id))
    return 200, slot_rules.to_api(slot), {'ETag': etag_of(slots)}


def _delete(slot_id, if_match):
    """
    <summary>
    DELETE /slots/{id}: remove the slot and save.
    </summary>
    <param name="slot_id">The id from the path.</param>
    <param name="if_match">The If-Match header, or None.</param>
    <returns>204 with no body and the new ETag.</returns>
    """
    with _store_lock:
        slots = store.load_slots()
        _check_match(slots, if_match)
        if store.get_slot(slots, slot_id) is None:
            raise ApiError(404, 'not_found', 'There is no slot {}.'.format(slot_id))
        slots = [s for s in slots if s.get('id') != slot_id]
        store.save_slots(slots)
    _log('deleted slot id={} over the API'.format(slot_id))
    return 204, None, {'ETag': etag_of(slots)}


def _restore(body, if_match):
    """
    <summary>
    POST /restore: replace every slot and the backed up settings with a backup document.
    </summary>
    <param name="body">The document, as GET /backup returns it.</param>
    <param name="if_match">The If-Match header, or None.</param>
    <returns>200 with the number of slots restored.</returns>
    <remarks>
    The document's shape is checked here: an Engage backup, a list of slot objects each
    with its own positive whole number id, and settings as text. The slots themselves
    are taken as they are, because a backup is Engage's own stored form, the same
    trust the dialog's restore gives a file. Only the settings slots.BACKUP_SETTING_IDS
    names are applied, so a document cannot switch this API off or change anything
    a backup would never have held.
    </remarks>
    """
    if not isinstance(body, dict) or body.get('type') != 'engage-backup':
        raise ApiError(422, 'validation', 'That is not an Engage backup.')
    slots_in = body.get('slots')
    if not isinstance(slots_in, list) or not all(isinstance(s, dict) for s in slots_in):
        raise ApiError(422, 'validation', 'The backup has no list of slots.')
    ids = [s.get('id') for s in slots_in]
    if not all(slot_rules._is_int(i) and i > 0 for i in ids) or len(set(ids)) != len(ids):
        raise ApiError(422, 'validation', 'Every slot in the backup needs its own id.')
    settings = body.get('settings') or {}
    if not isinstance(settings, dict) or not all(isinstance(v, str) for v in settings.values()):
        raise ApiError(422, 'validation', 'The backup settings are not readable.')
    document = {
        'type': 'engage-backup',
        'slots': slots_in,
        'settings': {k: v for k, v in settings.items() if k in store.BACKUP_SETTING_IDS},
    }
    with _store_lock:
        _check_match(store.load_slots(), if_match)
        count = store.restore_backup_data(document)
        slots = store.load_slots()
    _log('restored {} slots over the API'.format(count))
    return 200, {'restored': count}, {'ETag': etag_of(slots)}


# --- Options ---------------------------------------------------------------

def options(name, query):
    """
    <summary>
    GET /options/{name}: the choices for one picker field.
    </summary>
    <param name="name">'favourites', 'lists' or 'sequence_items'.</param>
    <param name="query">The parsed query: 'q' to search, and for 'sequence_items' the
    'seq_type' the schema's options_params passes.</param>
    <returns>{'items': [...], 'more': bool}.</returns>
    <exception cref="ApiError">404 for any other name.</exception>
    <remarks>
    Each source asks Kodi afresh, so a favourite or list made a moment ago is offered.
    The favourites honour the show_all_favourites setting, as the dialog does.
    </remarks>
    """
    q = (query.get('q') or [''])[0].strip()
    if name == 'favourites':
        items = _favourite_options(q)
    elif name == 'lists':
        items = _list_options(q)
    elif name == 'sequence_items':
        seq_type = (query.get('seq_type') or ['movie'])[0]
        if seq_type not in slot_rules.SEQ_TYPES:
            raise ApiError(400, 'bad_request', 'seq_type must be movie or binge.')
        if seq_type == 'binge' and not q:
            # Every episode in a library is far too many; the dashboard is told to ask
            # for a show name.
            return {'items': [], 'more': True}
        items = _movie_options(q) if seq_type == 'movie' else _episode_options(q)
    else:
        raise ApiError(404, 'not_found', 'There is no option source called {}.'.format(name))
    return {'items': items[:OPTIONS_LIMIT], 'more': len(items) > OPTIONS_LIMIT}


def _favourite_options(q):
    """
    <summary>
    Kodi favourites as choices; the value is the favourite's title, as slots store it.
    </summary>
    <param name="q">Text the title must contain, any case; '' for all.</param>
    <returns>The choices.</returns>
    """
    from resources.lib.favourites_ui import _get_favourites, _maybe_filter_video
    choices = []
    for fav in _maybe_filter_video(_get_favourites()):
        title = fav.get('title') or ''
        if title and q.lower() in title.lower():
            choices.append({'value': title, 'label': title, 'thumb': fav.get('thumbnail') or ''})
    return choices


def _list_options(q):
    """
    <summary>
    The Functional skin's lists as choices; the value is the list name.
    </summary>
    <param name="q">Text the name must contain, any case; '' for all.</param>
    <returns>The choices.</returns>
    """
    from resources.lib import functional_lists
    return [{'value': entry['name'], 'label': entry['name'],
             'detail': '{} items'.format(len(entry['items']))}
            for entry in functional_lists.load_lists()
            if q.lower() in entry['name'].lower()]


def _movie_options(q):
    """
    <summary>
    Library films as choices for a movie sequence; the value is the stored item.
    </summary>
    <param name="q">Text the title must contain; '' for all.</param>
    <returns>The choices, in title order.</returns>
    """
    from resources.lib import slot_manager
    return [{'value': m, 'label': slot_manager._item_line(m)}
            for m in slot_manager._library_movies(q)]


def _episode_options(q):
    """
    <summary>
    Library episodes of the shows whose name contains q, as choices for a binge order.
    </summary>
    <param name="q">Text the show name must contain; never empty here.</param>
    <returns>The choices, by show, season and episode. The value is the stored item,
    the same shape the dialog's episode range picker stores.</returns>
    """
    from resources.lib import slot_manager
    data = json.loads(xbmc.executeJSONRPC(json.dumps({
        'jsonrpc': '2.0', 'method': 'VideoLibrary.GetEpisodes',
        'params': {'properties': ['title', 'season', 'episode', 'firstaired', 'showtitle'],
                   'filter': {'field': 'tvshow', 'operator': 'contains', 'value': q}},
        'id': 1})))
    episodes = data.get('result', {}).get('episodes', []) or []
    items = [{'type': 'episode', 'id': e.get('episodeid'),
              'show': e.get('showtitle', ''), 'season': e.get('season', 0),
              'episode': e.get('episode', 0), 'title': e.get('title', ''),
              'firstaired': e.get('firstaired', '')}
             for e in episodes if e.get('episodeid')]
    items.sort(key=lambda it: (it['show'].lower(), it['season'], it['episode']))
    return [{'value': it, 'label': slot_manager._item_line(it)} for it in items]


# --- HTTP ------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    """
    <summary>
    One request: the Host and token checks, the body, then handle().
    </summary>
    <remarks>
    Responses are HTTP/1.0, one request per connection, so a refused request never
    leaves an unread body in a kept alive connection.
    </remarks>
    """

    server_version = 'EngageApi'
    sys_version = ''

    def log_message(self, fmt, *args):
        """
        <summary>
        Send the standard access line to kodi.log at debug level instead of stderr.
        </summary>
        <param name="fmt">The format string BaseHTTPRequestHandler passes.</param>
        <param name="args">Its arguments. The request line carries no token, which
        travels in a header.</param>
        """
        _log(fmt % args, xbmc.LOGDEBUG)

    def do_GET(self):
        """<summary>Serve a GET.</summary>"""
        self._serve('GET')

    def do_POST(self):
        """<summary>Serve a POST.</summary>"""
        self._serve('POST')

    def do_PUT(self):
        """<summary>Serve a PUT.</summary>"""
        self._serve('PUT')

    def do_DELETE(self):
        """<summary>Serve a DELETE.</summary>"""
        self._serve('DELETE')

    def _serve(self, method):
        """
        <summary>
        Check the request, route it and send the response or the error.
        </summary>
        <param name="method">The request method.</param>
        <remarks>
        The Host check comes first, then the token, both before any body is read. An
        unexpected exception is logged in full to kodi.log and answered with a bare
        500, so no internals reach the dashboard.
        </remarks>
        """
        try:
            expected_host = '127.0.0.1:{}'.format(self.server.server_address[1])
            if (self.headers.get('Host') or '').strip() != expected_host:
                raise ApiError(400, 'bad_request', 'This server only answers to {}.'.format(
                    expected_host))
            sent = (self.headers.get(TOKEN_HEADER) or '').encode('utf-8')
            if not hmac.compare_digest(sent, self.server.api_token.encode('utf-8')):
                raise ApiError(401, 'unauthorised', 'Missing or wrong token.')
            route = urlparse(self.path)
            body = self._read_body() if method in ('POST', 'PUT') else None
            status, payload, headers = handle(method, route.path, parse_qs(route.query),
                                              body, self.headers.get('If-Match'))
            self._send(status, payload, headers)
        except ApiError as e:
            error = {'code': e.code, 'message': e.message}
            if e.field:
                error['field'] = e.field
            self._send(e.status, {'error': error}, e.headers)
        except Exception:
            _log('request failed:\n{}'.format(traceback.format_exc()), xbmc.LOGERROR)
            self._send(500, {'error': {'code': 'internal',
                                       'message': 'Engage hit an error; kodi.log has the detail.'}},
                       {})

    def _read_body(self):
        """
        <summary>
        Read and parse the JSON body.
        </summary>
        <returns>The parsed value.</returns>
        <exception cref="ApiError">413 when Content-Length is over MAX_BODY, 400 when it
        is missing, not a number, or the body is not JSON.</exception>
        """
        try:
            length = int(self.headers.get('Content-Length') or '')
        except ValueError:
            raise ApiError(400, 'bad_request', 'The request needs a Content-Length.')
        if length > MAX_BODY:
            raise ApiError(413, 'too_large', 'The request is larger than {} bytes.'.format(MAX_BODY))
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, 'bad_request', 'The request body is not JSON.')

    def _send(self, status, payload, headers):
        """
        <summary>
        Write the status, headers and JSON body.
        </summary>
        <param name="status">The HTTP status.</param>
        <param name="payload">The value to send as JSON, or None for no body.</param>
        <param name="headers">Extra headers, such as ETag.</param>
        """
        data = b'' if payload is None else json.dumps(payload).encode('utf-8')
        self.send_response(status)
        if payload is not None:
            self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if data:
            self.wfile.write(data)
