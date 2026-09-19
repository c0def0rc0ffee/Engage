"""
<summary>
Custom Engage banner prompt, slim top-of-screen WindowXMLDialog with live
countdown and buttons: Start Now, Snooze, Queue Next, Stop & Resume After,
Cancel. Queue Next and Stop & Resume After only appear when something is
already playing.
</summary>
<remarks>
When the countdown reaches 0 with no user input, the banner resolves to the
slot's own timeout action (Queue Next by default, see slots.py). The user can
press any button before then to decide it themselves.
</remarks>
"""

import threading
import time
from datetime import datetime

import xbmc
import xbmcgui

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92
ACTION_STOP = 13

BTN_START = 9001
BTN_SNOOZE = 9002
BTN_QUEUE = 9003
BTN_CANCEL = 9004
BTN_STOP_RESUME = 9005

SNOOZE_OPTIONS_LABELS = ['5 minutes', '10 minutes', '15 minutes', '30 minutes', '1 hour']
SNOOZE_OPTIONS_VALUES = [5, 10, 15, 30, 60]


def _log(msg, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to kodi.log under the 'Engage prompt:' prefix.
    </summary>
    <param name="msg">Text to log.</param>
    <param name="level">Kodi log level, LOGINFO by default.</param>
    """
    xbmc.log('Engage prompt: {}'.format(msg), level)


class EngagePrompt(xbmcgui.WindowXMLDialog):
    """
    <summary>
    Caller sets attributes before doModal(); reads choice/snooze_minutes after.
    </summary>
    """

    def onInit(self):
        """
        <summary>
        Kodi callback once the window XML is loaded: fills in a default for every attribute
        the caller did not set, pushes the window properties the skin binds to, and starts
        the countdown thread.
        </summary>
        <remarks>
        Whether the countdown may fire the timeout action on its own is decided here: only
        when the scheduled time was still ahead as the banner opened. An overdue catch-up
        banner therefore waits for the user. The canresume property drives the visibility
        of the Queue Next and Stop & Resume After buttons in the skin.
        </remarks>
        """
        if not hasattr(self, 'show_name'):
            self.show_name = ''
        if not hasattr(self, 'episode_label'):
            self.episode_label = ''
        if not hasattr(self, 'thumb'):
            self.thumb = ''
        if not hasattr(self, 'scheduled_dt'):
            self.scheduled_dt = datetime.now()
        if not hasattr(self, 'choice'):
            self.choice = 'cancel'
        if not hasattr(self, 'snooze_minutes'):
            self.snooze_minutes = 5
        if not hasattr(self, 'timeout_action'):
            self.timeout_action = 'queue'

        self._stop_timer = False
        self._user_decided = False
        # Only auto-fire at countdown zero if there was still time on the
        # clock when the banner opened. Catch-up prompts (already overdue)
        # must wait for the user, auto-playing hours late would be rude.
        self._allow_autofire = (self.scheduled_dt - datetime.now()).total_seconds() > 0

        self.setProperty('showname', self.show_name)
        self.setProperty('episode', self.episode_label)
        # Poster/thumbnail; fall back to a generic video icon so the layout
        # never has an empty hole.
        self.setProperty('thumb', self.thumb or 'DefaultVideo.png')
        # Drives visibility of the playback-only buttons (Queue Next and
        # Stop & Resume After), they only make sense if something is playing.
        self.setProperty('canresume', '1' if xbmc.Player().isPlaying() else '0')
        self._update_countdown()

        self._timer_thread = threading.Thread(target=self._tick, daemon=True)
        self._timer_thread.start()

    def _tick(self):
        """
        <summary>
        Countdown thread body: refresh the countdown label once a second and, when it reaches
        zero with no user input and auto-fire is allowed, resolve the banner to the slot's
        timeout action and close it.
        </summary>
        <remarks>
        The loop ends when a button or Back sets _stop_timer. Any exception ends the
        thread quietly and the banner then simply waits for the user.
        </remarks>
        """
        while not self._stop_timer:
            try:
                self._update_countdown()
                # Fire the slot's timeout action when the countdown reaches 0,
                # unless the user already clicked something (and only when the
                # countdown was genuinely running when the banner opened).
                remaining = (self.scheduled_dt - datetime.now()).total_seconds()
                if remaining <= 0 and self._allow_autofire and not self._user_decided:
                    _log('Countdown reached 0 with no input, firing timeout '
                         'action: {}'.format(self.timeout_action))
                    self.choice = self.timeout_action
                    self._user_decided = True
                    self._stop_timer = True
                    self.close()
                    break
            except Exception as e:
                _log('countdown tick error: {}'.format(e), xbmc.LOGWARNING)
                break
            time.sleep(1)

    def _update_countdown(self):
        """
        <summary>
        Set the countdown window property to a 'Starts in', 'Starting now' or 'Overdue by'
        string measured from scheduled_dt.
        </summary>
        """
        remaining = self.scheduled_dt - datetime.now()
        total_seconds = int(remaining.total_seconds())
        if total_seconds > 0:
            minutes, seconds = divmod(total_seconds, 60)
            if minutes > 0:
                text = 'Starts in {}m {:02d}s'.format(minutes, seconds)
            else:
                text = 'Starts in {}s'.format(seconds)
        elif total_seconds == 0:
            text = 'Starting now'
        else:
            overdue = abs(total_seconds)
            o_min, o_sec = divmod(overdue, 60)
            if o_min > 0:
                text = 'Overdue by {}m {:02d}s'.format(o_min, o_sec)
            else:
                text = 'Overdue by {}s'.format(o_sec)
        self.setProperty('countdown', text)

    def onClick(self, controlId):
        """
        <summary>
        Kodi callback for a button press: record the choice the button stands for and close
        the banner.
        </summary>
        <param name="controlId">Id of the control clicked, one of the BTN_* constants.</param>
        <remarks>
        Snooze asks for a duration before closing. A click on anything other than the five
        buttons is ignored and leaves the countdown running.
        </remarks>
        """
        self._user_decided = True
        if controlId == BTN_START:
            self.choice = 'open'
        elif controlId == BTN_SNOOZE:
            self.choice = 'snooze'
            self._ask_snooze_duration()
        elif controlId == BTN_QUEUE:
            self.choice = 'queue'
        elif controlId == BTN_STOP_RESUME:
            self.choice = 'stop_requeue'
        elif controlId == BTN_CANCEL:
            self.choice = 'cancel'
        else:
            self._user_decided = False
            return
        self._stop_timer = True
        self.close()

    def _ask_snooze_duration(self):
        """
        <summary>
        Offer the snooze lengths as a select list and store the chosen minutes in snooze_minutes.
        </summary>
        <remarks>
        Backing out keeps the default of 5 minutes; the snooze choice itself still stands.
        </remarks>
        """
        choice = xbmcgui.Dialog().select('Snooze for...', SNOOZE_OPTIONS_LABELS)
        if choice >= 0:
            self.snooze_minutes = SNOOZE_OPTIONS_VALUES[choice]
        # If user cancels the duration sub-dialog, keep default 5 min.

    def onAction(self, action):
        """
        <summary>
        Kodi callback for non-button input: Back, previous menu and Stop all cancel the banner.
        </summary>
        <param name="action">The xbmcgui.Action Kodi passed in; only its id is looked at.</param>
        """
        action_id = action.getId()
        if action_id in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK, ACTION_STOP):
            self._user_decided = True
            self.choice = 'cancel'
            self._stop_timer = True
            self.close()


def show_engage_prompt(addon_path, show_name, episode_label, scheduled_dt,
                       thumb='', timeout_action='queue'):
    """
    <summary>
    Show the banner prompt and return (choice, snooze_minutes).
    </summary>
    <remarks>
    choice is one of: 'open', 'snooze', 'queue', 'stop_requeue', 'cancel'.
    episode_label may be '' for movies / non-TV-show favourites.
    thumb is a poster/thumbnail image path (may be '').
    timeout_action is what an untouched countdown resolves to at zero.
    </remarks>
    """
    try:
        dlg = EngagePrompt('EngagePrompt.xml', addon_path, 'Default', '720p')
        dlg.show_name = show_name
        dlg.episode_label = episode_label
        dlg.scheduled_dt = scheduled_dt
        dlg.thumb = thumb
        dlg.choice = 'cancel'
        dlg.snooze_minutes = 5
        dlg.timeout_action = timeout_action
        dlg.doModal()
        result = (dlg.choice, dlg.snooze_minutes)
        del dlg
        return result
    except Exception as e:
        _log('WindowXMLDialog failed, falling back to plain dialog: {}'.format(e), xbmc.LOGERROR)
        return _fallback_dialog(show_name, episode_label, scheduled_dt)


def _fallback_dialog(show_name, episode_label, scheduled_dt):
    """
    <summary>
    Plain select dialog offering the same choices, used when the skinned banner cannot be
    created.
    </summary>
    <param name="show_name">Slot label, used as the dialog heading.</param>
    <param name="episode_label">Episode line appended to the heading when not empty.</param>
    <param name="scheduled_dt">Accepted for parity with show_engage_prompt; not used here.</param>
    <returns>(choice, 5): the snooze length is always 5 minutes on this path.</returns>
    <remarks>
    There is no countdown, so no timeout action ever fires; backing out means cancel.
    </remarks>
    """
    title = show_name
    if episode_label:
        title = '{}, {}'.format(show_name, episode_label)
    options = ['Start Now', 'Snooze 5 min']
    actions = ['open', 'snooze']
    if xbmc.Player().isPlaying():
        options += ['Queue Next', 'Stop & Resume After']
        actions += ['queue', 'stop_requeue']
    options.append('Cancel')
    actions.append('cancel')
    choice = xbmcgui.Dialog().select('Engage: {}'.format(title), options)
    if choice < 0:
        return ('cancel', 5)
    return (actions[choice], 5)
