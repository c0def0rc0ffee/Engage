"""
<summary>
Service entry point, started by Kodi at login and left running.
</summary>
<remarks>
Everything the add-on actually does lives in the scheduler; this file exists
to put the add-on root on sys.path, publish the version into the read-only
settings field, and hand control over. A failure publishing the version is
logged and swallowed, because it must never stop the service starting.
</remarks>
"""
import sys
import traceback

import xbmc
import xbmcaddon

# Make sure the addon root is on sys.path regardless of how Kodi launched us.
_ADDON = xbmcaddon.Addon()
_ADDON_PATH = _ADDON.getAddonInfo('path')
if _ADDON_PATH and _ADDON_PATH not in sys.path:
    sys.path.insert(0, _ADDON_PATH)

def _publish_version():
    """
    <summary>
    Copy the add-on version into the read-only 'version_info' setting so it
    shows on the settings screen. Best effort: a failure here must not stop the
    service starting.
    </summary>
    """
    try:
        version = _ADDON.getAddonInfo('version')
        if version and _ADDON.getSetting('version_info') != version:
            _ADDON.setSetting('version_info', version)
    except Exception:
        xbmc.log('Engage: could not publish version to settings:\n{}'.format(
            traceback.format_exc()), xbmc.LOGWARNING)


if __name__ == '__main__':
    xbmc.log('Engage: service starting', xbmc.LOGINFO)
    _publish_version()
    try:
        from resources.lib.scheduler import EngageScheduler
        scheduler = EngageScheduler()
        scheduler.run()
    except Exception:
        # An import error or constructor crash would otherwise die silently, so
        # make sure it lands in kodi.log where it can be diagnosed.
        xbmc.log('Engage: service crashed:\n{}'.format(traceback.format_exc()), xbmc.LOGERROR)
    xbmc.log('Engage: service stopped', xbmc.LOGINFO)
