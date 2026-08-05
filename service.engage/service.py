import sys
import traceback

import xbmc
import xbmcaddon

# Make sure the addon root is on sys.path regardless of how Kodi launched us.
_ADDON = xbmcaddon.Addon()
_ADDON_PATH = _ADDON.getAddonInfo('path')
if _ADDON_PATH and _ADDON_PATH not in sys.path:
    sys.path.insert(0, _ADDON_PATH)

if __name__ == '__main__':
    xbmc.log('Engage: service starting', xbmc.LOGINFO)
    try:
        from resources.lib.scheduler import EngageScheduler
        scheduler = EngageScheduler()
        scheduler.run()
    except Exception:
        # An import error or constructor crash would otherwise die silently, so
        # make sure it lands in kodi.log where it can be diagnosed.
        xbmc.log('Engage: service crashed:\n{}'.format(traceback.format_exc()), xbmc.LOGERROR)
    xbmc.log('Engage: service stopped', xbmc.LOGINFO)
