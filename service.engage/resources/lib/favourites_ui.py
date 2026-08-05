import json
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'service.engage'
ADDON_TITLE = 'Engage'
FAVOURITES_PATH = 'special://profile/favourites.xml'

# Heuristics for "video-only" filtering (movies + TV shows + video plugins/playlists).
VIDEO_EXTS = (
    '.mkv', '.mp4', '.avi', '.ts', '.mov', '.m4v', '.webm', '.iso',
    '.mpg', '.mpeg', '.flv', '.wmv', '.divx', '.strm', '.m2ts'
)
VIDEO_WINDOWS = (
    'videos', 'video', 'tvshows', 'movies', 'movieinformation',
    'videolibrary', 'videoplaylist', 'tvchannels', 'tvrecordings'
)
VIDEO_PATH_HINTS = (
    'videodb://', 'library://video/', 'plugin://plugin.video.',
    'special://videoplaylists/', 'pvr://channels/tv/', 'pvr://recordings/tv/'
)


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('Engage: {}'.format(msg), level)


def _notify(message, icon=xbmcgui.NOTIFICATION_INFO, duration=4000):
    xbmcgui.Dialog().notification(ADDON_TITLE, message, icon, duration)


def _get_favourites():
    """Return the list of favourites via JSON-RPC."""
    request = json.dumps({
        'jsonrpc': '2.0',
        'method': 'Favourites.GetFavourites',
        'params': {'properties': ['window', 'path', 'windowparameter', 'thumbnail']},
        'id': 1
    })
    response = xbmc.executeJSONRPC(request)
    data = json.loads(response)
    return data.get('result', {}).get('favourites', []) or []


def _is_video_favourite(fav):
    """Heuristic: True if a favourite points at a movie, TV show, or video content."""
    fav_type = (fav.get('type') or '').lower()
    path = (fav.get('path') or '').lower()
    window = (fav.get('window') or '').lower()
    window_param = (fav.get('windowparameter') or '').lower()

    if fav_type == 'window':
        if window in VIDEO_WINDOWS:
            return True
        if any(h in window_param for h in VIDEO_PATH_HINTS):
            return True
        if window_param.endswith(VIDEO_EXTS):
            return True

    if fav_type == 'media':
        if any(h in path for h in VIDEO_PATH_HINTS):
            return True
        if path.endswith(VIDEO_EXTS):
            return True
        # Smart playlists, assume video unless we know otherwise
        if path.endswith('.xsp'):
            return True

    return False


def _maybe_filter_video(favs):
    """Apply the video-only filter unless the user disabled it. Falls back to the
    full list if filtering would produce an empty result (otherwise the dialog
    would be confusingly empty)."""
    addon = xbmcaddon.Addon(ADDON_ID)
    if addon.getSettingBool('show_all_favourites'):
        return favs
    filtered = [f for f in favs if _is_video_favourite(f)]
    if not filtered:
        _log('Video-only filter returned 0 results, showing full list as fallback', xbmc.LOGWARNING)
        return favs
    return filtered


def open_favourites_screen():
    """Jump straight to the Kodi Favourites window."""
    _log('Opening Favourites window')
    xbmc.executebuiltin('ActivateWindow(Favourites)')


def add_favourite():
    """Prompt the user for a title + path, then add it via JSON-RPC."""
    dlg = xbmcgui.Dialog()

    title = dlg.input('Engage, Favourite name', type=xbmcgui.INPUT_ALPHANUM)
    if not title:
        return

    # Choose a type
    type_labels = ['Media (file/path/plugin URL)', 'Window (e.g. Videos, Music)', 'Script (RunScript path)']
    type_keys = ['media', 'window', 'script']
    choice = dlg.select('Engage, Favourite type', type_labels)
    if choice < 0:
        return

    fav_type = type_keys[choice]
    params = {'title': title, 'type': fav_type}

    if fav_type == 'media':
        path = dlg.input('Engage, Path or URL', type=xbmcgui.INPUT_ALPHANUM)
        if not path:
            return
        params['path'] = path

    elif fav_type == 'window':
        window = dlg.input('Engage, Window name (e.g. videos, music)', type=xbmcgui.INPUT_ALPHANUM)
        if not window:
            return
        params['window'] = window
        window_param = dlg.input('Engage, Window parameter (optional, leave empty for none)',
                                 type=xbmcgui.INPUT_ALPHANUM)
        if window_param:
            params['windowparameter'] = window_param

    elif fav_type == 'script':
        path = dlg.input('Engage, Script path', type=xbmcgui.INPUT_ALPHANUM)
        if not path:
            return
        params['path'] = path

    request = json.dumps({
        'jsonrpc': '2.0',
        'method': 'Favourites.AddFavourite',
        'params': params,
        'id': 1
    })
    response = xbmc.executeJSONRPC(request)
    _log('AddFavourite response: {}'.format(response))

    data = json.loads(response)
    if data.get('result') == 'OK':
        _notify('Added favourite: {}'.format(title))
    else:
        err = data.get('error', {}).get('message', 'unknown error')
        _notify('Failed to add favourite: {}'.format(err), xbmcgui.NOTIFICATION_ERROR, 6000)


def remove_favourite():
    """List existing favourites and remove the selected one by editing favourites.xml."""
    favs = _maybe_filter_video(_get_favourites())
    if not favs:
        _notify('No favourites found.', xbmcgui.NOTIFICATION_WARNING)
        return

    titles = [f.get('title', '(untitled)') for f in favs]
    choice = xbmcgui.Dialog().select('Engage, Remove favourite', titles)
    if choice < 0:
        return

    target_title = titles[choice]
    confirm = xbmcgui.Dialog().yesno(
        'Engage',
        'Remove "{}" from your favourites?'.format(target_title)
    )
    if not confirm:
        return

    if _remove_from_favourites_xml(target_title):
        _notify('Removed favourite: {}'.format(target_title))
    else:
        _notify('Could not remove favourite.', xbmcgui.NOTIFICATION_ERROR)


def _remove_from_favourites_xml(title):
    """Remove the first <favourite name="title"> element from favourites.xml."""
    real_path = xbmcvfs.translatePath(FAVOURITES_PATH)

    if not xbmcvfs.exists(FAVOURITES_PATH):
        _log('favourites.xml does not exist at {}'.format(real_path), xbmc.LOGWARNING)
        return False

    try:
        tree = ET.parse(real_path)
        root = tree.getroot()
    except ET.ParseError as e:
        _log('Failed to parse favourites.xml: {}'.format(e), xbmc.LOGERROR)
        return False

    removed = False
    for fav in list(root.findall('favourite')):
        if fav.get('name', '') == title:
            root.remove(fav)
            removed = True
            break

    if not removed:
        _log('No matching favourite element found for: {}'.format(title), xbmc.LOGWARNING)
        return False

    try:
        tree.write(real_path, encoding='utf-8', xml_declaration=True)
        _log('Removed favourite "{}" from {}'.format(title, real_path))
        # Refresh the favourites window if it happens to be open
        xbmc.executebuiltin('Container.Refresh')
        return True
    except (OSError, IOError) as e:
        _log('Failed to write favourites.xml: {}'.format(e), xbmc.LOGERROR)
        return False


def open_settings():
    """Open the Engage add-on settings dialog."""
    _log('Opening Engage settings')
    xbmcaddon.Addon(ADDON_ID).openSettings()


SETTINGS_WINDOW_NAMES = ('addonsettings', 'dialogaddonsettings')


def _settings_dialog_visible():
    return any(xbmc.getCondVisibility('Window.IsActive({})'.format(w)) for w in SETTINGS_WINDOW_NAMES)


def _close_addon_settings_if_open():
    """Close the addon settings dialog if open. Returns True if it was open."""
    if not _settings_dialog_visible():
        _log('settings dialog not open, no close needed')
        return False
    _log('settings dialog open, attempting close')
    for w in SETTINGS_WINDOW_NAMES:
        xbmc.executebuiltin('Dialog.Close({},true)'.format(w))
    # Wait up to ~3s for it to actually close
    for _ in range(60):
        if not _settings_dialog_visible():
            _log('settings dialog closed successfully')
            return True
        xbmc.sleep(50)
    _log('timed out waiting for settings dialog to close', xbmc.LOGWARNING)
    return True


def pick_favourite_for_slot(slot_index_str):
    """Show a select dialog of current favourites and write the choice into slotN_favourite.

    Important: the addon settings dialog must NOT be open while we call
    setSettingString, otherwise the dialog's stale in-memory copy will
    overwrite our change when the user clicks OK. So we close it first and
    reopen it afterwards.
    """
    try:
        slot_index = int(slot_index_str)
    except (TypeError, ValueError):
        _log('pick_fav called with invalid slot index: {}'.format(slot_index_str), xbmc.LOGWARNING)
        return

    if slot_index < 1 or slot_index > 20:
        _log('pick_fav slot index out of range: {}'.format(slot_index), xbmc.LOGWARNING)
        return

    # The settings dialog is usually already closed via option="close" on the
    # action button, but call this to be safe, its stale in-memory copy would
    # otherwise overwrite our setSettingString call.
    _close_addon_settings_if_open()

    favs = _maybe_filter_video(_get_favourites())
    if not favs:
        _notify('No favourites found.', xbmcgui.NOTIFICATION_WARNING, duration=6000)
        xbmcaddon.Addon(ADDON_ID).openSettings()
        return

    titles = [f.get('title', '(untitled)') for f in favs]
    choice = xbmcgui.Dialog().select(
        'Engage, Pick favourite for Slot {}'.format(slot_index),
        titles
    )

    if choice >= 0:
        chosen = titles[choice]
        addon = xbmcaddon.Addon(ADDON_ID)
        addon.setSettingString('slot{}_favourite'.format(slot_index), chosen)
        _log('Slot {} favourite set to "{}"'.format(slot_index, chosen))
        _notify('Slot {} set to: {}'.format(slot_index, chosen), duration=8000)

    # The pick action only fires from inside the settings UI (action="close"
    # closed it on the way in), so always reopen, the user sees the favourite
    # name field now filled in.
    xbmcaddon.Addon(ADDON_ID).openSettings()


def handle_action(action, *args):
    if action == 'manage_slots':
        # Imported lazily to avoid a circular import (slot_manager uses our
        # favourites helpers).
        from resources.lib.slot_manager import manage_slots
        manage_slots()
    elif action == 'open_settings':
        open_settings()
    elif action == 'open_favs':
        open_favourites_screen()
    elif action == 'add_fav':
        add_favourite()
    elif action == 'remove_fav':
        remove_favourite()
    elif action == 'pick_fav':
        if args:
            pick_favourite_for_slot(args[0])
        else:
            _notify('pick_fav requires a slot number', xbmcgui.NOTIFICATION_WARNING)
    else:
        _log('Unknown action: {}'.format(action), xbmc.LOGWARNING)
        _notify('Unknown action: {}'.format(action), xbmcgui.NOTIFICATION_WARNING)
