import os
import sys
import traceback

import xbmc
import xbmcaddon
import xbmcgui

# Make sure the addon root is on sys.path so 'resources.lib...' imports work
# regardless of how Kodi launched this script (RunScript by path, by addon id, etc).
_ADDON = xbmcaddon.Addon('service.engage')
_ADDON_PATH = _ADDON.getAddonInfo('path')
if _ADDON_PATH and _ADDON_PATH not in sys.path:
    sys.path.insert(0, _ADDON_PATH)

xbmc.log('Engage script.py: invoked with argv={}'.format(sys.argv), xbmc.LOGINFO)

from resources.lib.favourites_ui import handle_action

MENU_LABELS = [
    'Manage slots...',
    'Open Engage settings',
    'Open Favourites screen',
    'Add a favourite...',
    'Remove a favourite...',
]
MENU_ACTIONS = ['manage_slots', 'open_settings', 'open_favs', 'add_fav', 'remove_fav']


def show_menu():
    choice = xbmcgui.Dialog().select('Engage', MENU_LABELS)
    if choice < 0:
        return
    handle_action(MENU_ACTIONS[choice])


if __name__ == '__main__':
    try:
        if len(sys.argv) > 1 and sys.argv[1]:
            xbmc.log('Engage script.py: dispatching action="{}" args={}'.format(
                sys.argv[1], sys.argv[2:]), xbmc.LOGINFO)
            handle_action(sys.argv[1], *sys.argv[2:])
        else:
            xbmc.log('Engage script.py: no args, showing launcher menu', xbmc.LOGINFO)
            show_menu()
    except Exception as e:
        xbmc.log('Engage script.py: EXCEPTION {}'.format(e), xbmc.LOGERROR)
        xbmc.log(traceback.format_exc(), xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            'Engage', 'Script error, see kodi.log', xbmcgui.NOTIFICATION_ERROR, 6000
        )
