import json
import re
import time
from datetime import datetime, timedelta

import xbmc
import xbmcaddon
import xbmcgui

ADDON_ID = 'service.engage'
DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
SNOOZE_MINUTES = 5
STARTUP_REST_SECONDS = 20  # let Kodi fully settle at login before the first prompt


class Slot:
    """Wraps one slot dict from the JSON store (resources/lib/slots.py)."""

    def __init__(self, data):
        self.id = data.get('id', 0)
        # Kept for log readability, "slot 3" rather than internal ids.
        self.index = self.id - 1
        self.enabled = bool(data.get('enabled', False))
        self.label = data.get('label') or ''
        # Multiple days supported via 'days'; fall back to legacy single 'day'.
        self.days = data.get('days') or ([data['day']] if data.get('day') else ['Monday'])
        self.specific_date = (data.get('date') or '').strip()
        self.hour = int(data.get('hour', 19))
        self.minute = int(data.get('minute', 0))
        self.warning = int(data.get('warning', 5))
        self.favourite = data.get('favourite') or ''
        self.autoopen = bool(data.get('autoopen', False))
        self.snooze_enabled = bool(data.get('snooze', True))
        # What an untouched banner resolves to when the countdown hits zero.
        from resources.lib import slots as slot_store
        self.timeout_action = slot_store.timeout_action_of(data)
        # 'favourite' (default) plays a single Kodi favourite (TV next-unwatched
        # or movie/folder). 'sequence' plays the next unwatched item from an
        # ordered library list: movies (e.g. the Marvel films) or episodes
        # across shows (e.g. an Arrowverse air-date binge).
        self.kind = data.get('kind', 'favourite')
        self.seq_type = data.get('seq_type', 'movie')
        # Unified item list; convert legacy 'movies' if present.
        self.items = data.get('items')
        if self.items is None:
            self.items = [{'type': 'movie', 'id': m.get('id'), 'title': m.get('title'),
                           'year': m.get('year')}
                          for m in (data.get('movies') or [])]

    def day_indexes(self):
        """Weekday numbers (Mon=0) this slot runs on."""
        out = []
        for d in self.days:
            try:
                out.append(DAY_NAMES.index(d))
            except ValueError:
                pass
        return out

    def parsed_date(self):
        """Return a date object if specific_date is a valid YYYY-MM-DD, else None.

        Parses manually rather than with datetime.strptime, Kodi's embedded
        Python has a long-standing bug where strptime raises TypeError when
        first called from a non-main thread.
        """
        if not self.specific_date:
            return None
        try:
            parts = self.specific_date.split('-')
            if len(parts) != 3:
                return None
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            return datetime(year, month, day).date()
        except (ValueError, TypeError):
            return None


class EngageScheduler:

    def __init__(self):
        self.monitor = xbmc.Monitor()
        self.triggered = {}
        self.warned = {}
        self.cancelled = {}
        self.snoozed = {}
        self._warned_misconfig = set()
        # When the user picks "Queue Next" we hold the (slot, key) here and
        # the player monitor fires it when current playback ends.
        self.queued_next = None
        # When the user picks "Stop & Resume After" we stash the interrupted
        # item here; _resume_armed gates resuming until the slot itself ends.
        self.resume_after = None
        self._resume_armed = False
        self._player = _EngagePlayerMonitor(self)

    def _log(self, msg, level=xbmc.LOGINFO):
        xbmc.log('Engage: {}'.format(msg), level)

    def _debug(self, msg):
        addon = xbmcaddon.Addon(ADDON_ID)
        if addon.getSettingBool('debug'):
            self._log('[DEBUG] {}'.format(msg), xbmc.LOGDEBUG)

    def _reload_slots(self):
        from resources.lib import slots as slot_store
        slots = []
        for data in slot_store.load_slots():
            slot = Slot(data)
            if not slot.enabled:
                continue
            if slot.kind == 'sequence':
                if not slot.items:
                    if slot.id not in self._warned_misconfig:
                        self._log('Slot {} is an empty sequence, skipping'.format(slot.id), xbmc.LOGWARNING)
                        self._warned_misconfig.add(slot.id)
                    continue
                if not slot.label:
                    slot.label = 'Sequence'
            else:
                if not slot.favourite:
                    if slot.id not in self._warned_misconfig:
                        self._log('Slot {} is enabled but has no favourite set, skipping'.format(slot.id), xbmc.LOGWARNING)
                        self._warned_misconfig.add(slot.id)
                    continue
                # Fall back to the favourite name when the user didn't enter a label.
                if not slot.label:
                    slot.label = slot.favourite
            slots.append(slot)
        return slots

    def _today_key(self, slot):
        """Dedup key for today's occurrence of a slot. Includes the scheduled
        time so editing a slot's time re-arms it even if it already fired
        today, important for testing and for genuine same-day reschedules."""
        return '{}-{}-{:02d}{:02d}'.format(
            datetime.now().strftime('%Y-%m-%d'), slot.id, slot.hour, slot.minute)

    def _get_scheduled_dt(self, slot):
        """Return today's datetime for this slot if today matches, else None.

        Specific date overrides day-of-week, if a YYYY-MM-DD is set, the slot
        only fires on that exact date. Invalid date strings fall back to the
        weekly day-of-week behaviour.
        """
        now = datetime.now()
        target_date = slot.parsed_date()
        if target_date is not None:
            if now.date() != target_date:
                return None
        else:
            if now.weekday() not in slot.day_indexes():
                return None
        return now.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)

    def _activate_favourite(self, favourite_name):
        """Activate a Kodi favourite by name. Try JSON-RPC first, fall back to window command."""
        self._log('Activating favourite: {}'.format(favourite_name))

        # Get the list of favourites and find a match
        request = json.dumps({
            'jsonrpc': '2.0',
            'method': 'Favourites.GetFavourites',
            'params': {'properties': ['window', 'path', 'windowparameter']},
            'id': 1
        })
        response = xbmc.executeJSONRPC(request)
        self._debug('Favourites.GetFavourites response: {}'.format(response))

        data = json.loads(response)
        favourites = data.get('result', {}).get('favourites', []) or []

        target = None
        for fav in favourites:
            if fav.get('title', '').lower() == favourite_name.lower():
                target = fav
                break

        if not target:
            self._log('Favourite not found: {}'.format(favourite_name), xbmc.LOGWARNING)
            xbmcgui.Dialog().notification(
                'Engage',
                'Favourite not found: {}'.format(favourite_name),
                xbmcgui.NOTIFICATION_WARNING,
                5000
            )
            self._open_favourites_fallback()
            return False

        fav_type = target.get('type', '')
        path = target.get('path', '')
        window = target.get('window', '')
        window_param = target.get('windowparameter', '')

        if fav_type == 'media' and path:
            xbmc.Player().play(path)
            self._log('Playing media favourite: {}'.format(path))
            return True
        elif fav_type == 'window' and window:
            cmd = 'ActivateWindow({},{})'.format(window, window_param) if window_param else 'ActivateWindow({})'.format(window)
            xbmc.executebuiltin(cmd)
            self._log('Opened window favourite: {}'.format(cmd))
            return True
        elif fav_type == 'script' and path:
            xbmc.executebuiltin('RunScript({})'.format(path))
            self._log('Ran script favourite: {}'.format(path))
            return True
        elif path:
            xbmc.Player().play(path)
            self._log('Playing path from favourite: {}'.format(path))
            return True
        else:
            self._log('Unknown favourite type: {}'.format(fav_type), xbmc.LOGWARNING)
            self._open_favourites_fallback()
            return False

    def _open_favourites_fallback(self):
        """Open the Kodi Favourites window as a fallback."""
        self._log('Opening Favourites window as fallback')
        xbmc.executebuiltin('ActivateWindow(Favourites)')

    def _resolve_episode_info(self, slot):
        """For TV show favourites, look up the next unwatched episode.
        Returns the episode dict from JSON-RPC or None for movies / non-TV / failed lookups."""
        favs = self._get_favourites_raw()
        fav = next((f for f in favs if f.get('title', '').lower() == slot.favourite.lower()), None)
        if not fav:
            return None
        tvshowid = self._extract_tvshowid(fav)
        if tvshowid is None:
            return None
        return self._get_next_unwatched(tvshowid)

    def _get_favourites_raw(self):
        request = json.dumps({
            'jsonrpc': '2.0',
            'method': 'Favourites.GetFavourites',
            'params': {'properties': ['window', 'path', 'windowparameter']},
            'id': 1
        })
        response = xbmc.executeJSONRPC(request)
        data = json.loads(response)
        return data.get('result', {}).get('favourites', []) or []

    def _extract_tvshowid(self, fav):
        """Pull the tvshowid out of a videodb://tvshows/titles/<id>/ favourite."""
        wp = fav.get('windowparameter') or ''
        path = fav.get('path') or ''
        for source in (wp, path):
            match = re.search(r'videodb://tvshows/titles/(\d+)', source, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def _get_next_unwatched(self, tvshowid):
        """Return the lowest season/episode unwatched episode for a TV show."""
        request = json.dumps({
            'jsonrpc': '2.0',
            'method': 'VideoLibrary.GetEpisodes',
            'params': {
                'tvshowid': tvshowid,
                'properties': ['title', 'season', 'episode', 'playcount', 'file',
                               'showtitle', 'art', 'thumbnail'],
                'filter': {'field': 'playcount', 'operator': 'is', 'value': '0'}
            },
            'id': 1
        })
        response = xbmc.executeJSONRPC(request)
        self._debug('GetEpisodes response (truncated): {}'.format(response[:500]))
        data = json.loads(response)
        episodes = data.get('result', {}).get('episodes', []) or []
        if not episodes:
            return None
        # Sort by (season, episode) for the chronologically earliest unwatched,
        # but push season 0 (specials) to the end, only offer a special when
        # every regular episode has been watched.
        episodes.sort(key=lambda e: (e.get('season', 0) == 0, e.get('season', 0), e.get('episode', 0)))
        return episodes[0]

    def _episode_label(self, episode):
        """Format an episode dict as 'S03E14, Lower Decks'."""
        if not episode:
            return ''
        return 'S{:02d}E{:02d}, {}'.format(
            episode.get('season', 0),
            episode.get('episode', 0),
            episode.get('title', '')
        )

    def _play_episode(self, episode):
        """Play a specific episode by episodeid via Player.Open."""
        request = json.dumps({
            'jsonrpc': '2.0',
            'method': 'Player.Open',
            'params': {'item': {'episodeid': episode['episodeid']}},
            'id': 1
        })
        response = xbmc.executeJSONRPC(request)
        self._log('Player.Open episode response: {}'.format(response))

    def _resolve_next_item(self, slot):
        """For a sequence slot, return the first unwatched item in the list as
        {'type', 'id', 'label'}, or None if empty / all watched. Items missing
        from the library are skipped. Works for both movie and episode items."""
        for entry in slot.items:
            item_id = entry.get('id')
            item_type = entry.get('type', 'movie')
            if not item_id:
                continue
            if item_type == 'episode':
                data = json.loads(xbmc.executeJSONRPC(json.dumps({
                    'jsonrpc': '2.0', 'method': 'VideoLibrary.GetEpisodeDetails',
                    'params': {'episodeid': item_id,
                               'properties': ['title', 'playcount', 'season', 'episode',
                                              'showtitle', 'art', 'thumbnail']},
                    'id': 1
                })))
                details = data.get('result', {}).get('episodedetails')
                if not details:
                    continue
                if int(details.get('playcount', 0)) == 0:
                    label = '{} · S{:02d}E{:02d}, {}'.format(
                        details.get('showtitle', entry.get('show', '')),
                        details.get('season', 0), details.get('episode', 0),
                        details.get('title', ''))
                    return {'type': 'episode', 'id': item_id, 'label': label,
                            'art': self._pick_poster(details, is_episode=True)}
            else:
                data = json.loads(xbmc.executeJSONRPC(json.dumps({
                    'jsonrpc': '2.0', 'method': 'VideoLibrary.GetMovieDetails',
                    'params': {'movieid': item_id,
                               'properties': ['title', 'playcount', 'art', 'thumbnail']},
                    'id': 1
                })))
                details = data.get('result', {}).get('moviedetails')
                if not details:
                    continue
                if int(details.get('playcount', 0)) == 0:
                    return {'type': 'movie', 'id': item_id,
                            'label': details.get('title', entry.get('title', '')),
                            'art': self._pick_poster(details, is_episode=False)}
        return None

    def _pick_poster(self, details, is_episode):
        """Choose a portrait poster image path from a library item's art dict.
        Prefers the show/movie poster; falls back to episode thumb."""
        art = details.get('art', {}) or {}
        if is_episode:
            for k in ('tvshow.poster', 'season.poster', 'poster'):
                if art.get(k):
                    return art[k]
            return art.get('thumb') or details.get('thumbnail') or ''
        for k in ('poster', 'thumb'):
            if art.get(k):
                return art[k]
        return details.get('thumbnail') or ''

    def _episode_art(self, episode):
        """Poster for a next-unwatched episode dict (from _get_next_unwatched)."""
        return self._pick_poster(episode, is_episode=True)

    def _favourite_thumb(self, name):
        """Thumbnail for a plain favourite, matched by title."""
        request = json.dumps({
            'jsonrpc': '2.0', 'method': 'Favourites.GetFavourites',
            'params': {'properties': ['thumbnail']}, 'id': 1
        })
        data = json.loads(xbmc.executeJSONRPC(request))
        for fav in (data.get('result', {}).get('favourites', []) or []):
            if fav.get('title', '').lower() == (name or '').lower():
                return fav.get('thumbnail') or ''
        return ''

    def _play_item(self, item):
        """Play a sequence item (movie or episode) by library id via Player.Open."""
        key = 'episodeid' if item['type'] == 'episode' else 'movieid'
        response = xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'method': 'Player.Open',
            'params': {'item': {key: item['id']}}, 'id': 1
        }))
        self._log('Player.Open {} response: {}'.format(item['type'], response))

    def _play_slot(self, slot):
        """Play whatever this slot points at, for a sequence, the next unwatched
        item in the list; for a favourite, the next unwatched episode (TV) or
        the favourite itself (movie / folder / playlist / etc)."""
        if slot.kind == 'sequence':
            item = self._resolve_next_item(slot)
            if item:
                self._log('Slot {}: playing next unwatched "{}"'.format(
                    slot.index + 1, item['label']))
                self._play_item(item)
            else:
                self._log('Slot {}: sequence all watched'.format(slot.index + 1))
                xbmcgui.Dialog().notification(
                    'Engage',
                    '{}, all watched!'.format(slot.label),
                    xbmcgui.NOTIFICATION_INFO, 6000
                )
            return

        episode = self._resolve_episode_info(slot)
        if episode and episode.get('episodeid'):
            self._log('Slot {}: playing next unwatched {}'.format(
                slot.index + 1, self._episode_label(episode)))
            self._play_episode(episode)
        else:
            self._log('Slot {}: no resolvable episode, activating favourite "{}"'.format(
                slot.index + 1, slot.favourite))
            self._activate_favourite(slot.favourite)

    def _apply_prompt_action(self, slot, key, action, snooze_minutes):
        """Translate a prompt result into scheduler state changes."""
        if action == 'skip':
            return  # GUI wasn't ready; caller leaves the slot un-warned to retry
        now = datetime.now()
        if action == 'open':
            self._play_slot(slot)
            self.triggered[key] = True
            self._log('Slot {} opened by user'.format(slot.index + 1))
        elif action == 'snooze':
            self.snoozed[key] = now + timedelta(minutes=snooze_minutes)
            self._log('Slot {} snoozed for {} minutes'.format(slot.index + 1, snooze_minutes))
        elif action == 'queue':
            if xbmc.Player().isPlaying():
                self.queued_next = (slot, key)
                xbmcgui.Dialog().notification(
                    'Engage',
                    '{} queued, starts when current playback ends.'.format(slot.label),
                    xbmcgui.NOTIFICATION_INFO, 5000
                )
                self._log('Slot {} queued for after current playback'.format(slot.index + 1))
            else:
                self._log('Queue Next chosen but nothing is playing, opening now')
                self._play_slot(slot)
                self.triggered[key] = True
        elif action == 'stop_requeue':
            self._stop_and_requeue(slot, key)
        elif action == 'cancel':
            self.cancelled[key] = True
            self._log('Slot {} cancelled by user'.format(slot.index + 1))

    def _stop_and_requeue(self, slot, key):
        """Stop what's playing now, remember it (and its position), play the
        slot, then resume the interrupted item when the slot finishes."""
        player = xbmc.Player()
        if player.isPlaying():
            info = self._capture_now_playing()
            if info:
                self.resume_after = info
                self._resume_armed = False
                self._log('Will resume {} at {:.0f}s after slot {}'.format(
                    info.get('describe', '?'), info.get('time', 0), slot.index + 1))
                xbmcgui.Dialog().notification(
                    'Engage',
                    'Will resume your show after {}.'.format(slot.label),
                    xbmcgui.NOTIFICATION_INFO, 5000
                )
            else:
                self._log('Could not capture resume info', xbmc.LOGWARNING)
                self.resume_after = None
            player.stop()
            xbmc.sleep(800)  # let the stop settle before opening the slot
        self._play_slot(slot)
        self.triggered[key] = True

    def _capture_now_playing(self):
        """Capture the currently-playing item by library identity (so resume
        keeps full metadata + watched tracking) rather than its resolved stream
        URL. Returns a dict for _resume_playback, or None."""
        try:
            players = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'method': 'Player.GetActivePlayers', 'id': 1
            }))).get('result', []) or []
            if not players:
                return None
            pid = players[0].get('playerid', 1)

            item = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'method': 'Player.GetItem',
                'params': {'playerid': pid, 'properties': ['file', 'title', 'showtitle']},
                'id': 1
            }))).get('result', {}).get('item', {}) or {}

            props = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'method': 'Player.GetProperties',
                'params': {'playerid': pid, 'properties': ['time']},
                'id': 1
            }))).get('result', {}) or {}

            t = props.get('time', {}) or {}
            seconds = (t.get('hours', 0) * 3600 + t.get('minutes', 0) * 60
                       + t.get('seconds', 0))
            resume_time = max(0.0, seconds - 5)  # back up 5s for context

            info = {'time': resume_time}
            item_type = item.get('type', '')
            item_id = item.get('id', -1)
            # Prefer library identity, plays through the library with full
            # metadata and updates watched state on completion.
            if item_id and item_id > 0 and item_type in ('episode', 'movie', 'musicvideo'):
                info['library'] = {'{}id'.format(item_type): item_id}
                info['describe'] = '{} #{}'.format(item_type, item_id)
            else:
                # Plugin/other (e.g. Jellyfin), replay the item's own path so it
                # goes back through the plugin (which reports watched state),
                # NOT the resolved stream URL from getPlayingFile().
                path = item.get('file') or ''
                if not path:
                    return None
                info['file'] = path
                info['describe'] = item.get('label') or path
            return info
        except Exception as e:
            self._log('capture_now_playing failed: {}'.format(e), xbmc.LOGWARNING)
            return None

    def _on_playback_started(self):
        """Called when playback starts. Arms the resume so that the NEXT stop/end
        (i.e. when the slot itself finishes) triggers the resume, not the stop
        we issued on the original item."""
        if self.resume_after and not self._resume_armed:
            self._resume_armed = True
            self._log('Resume armed, will restore after this playback ends')

    def _on_playback_ended(self):
        """Called by the player monitor when current playback stops/ends."""
        # Resume-after takes priority over a queued slot.
        if self.resume_after and self._resume_armed:
            info = self.resume_after
            self.resume_after = None
            self._resume_armed = False
            import threading
            threading.Thread(target=self._resume_playback, args=(info,), daemon=True).start()
            return

        if self.queued_next:
            slot, key = self.queued_next
            self.queued_next = None
            self._log('Playback ended, firing queued slot {}'.format(slot.index + 1))
            self._play_slot(slot)
            self.triggered[key] = True

    def _resume_playback(self, info):
        """Resume a previously-interrupted item, seeking back to its position.
        Runs on its own thread so the player callback isn't blocked.

        Opens via library id (episode/movie) where possible so the item plays
        with full metadata and watched tracking; otherwise replays its plugin
        path. Falls back to a raw file play only as a last resort."""
        self._log('Resuming interrupted playback: {}'.format(info.get('describe', '?')))

        params = {'item': None}
        if 'library' in info:
            params['item'] = info['library']
            # Kodi saved a bookmark when we stopped the item, so ask for a
            # native resume too. Belt and braces: if the seek below were ever
            # missed, playback still starts near the right spot, not at 0:00.
            params['options'] = {'resume': True}
        elif info.get('file'):
            params['item'] = {'file': info['file']}
        else:
            return

        response = xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'method': 'Player.Open',
            'params': params, 'id': 1
        }))
        self._log('Resume Player.Open response: {}'.format(response))

        # Wait for the player to be genuinely up, not just queued.
        # isPlaying() goes true before the stream is seekable and a seek
        # issued in that window is silently dropped, which played the item
        # from scratch. A real duration means the demuxer is ready. Plugin
        # streams (e.g. Jellyfin) can take a while to resolve, hence 20s.
        player = xbmc.Player()
        for _ in range(200):
            if player.isPlaying():
                try:
                    if player.getTotalTime() > 0:
                        break
                except RuntimeError:
                    pass
            xbmc.sleep(100)
        else:
            self._log('Resume: player never became ready, no seek',
                      xbmc.LOGWARNING)
            return

        # Seek and confirm it landed; retry while the player warms up.
        target = info.get('time', 0)
        if target <= 0:
            return
        for _ in range(10):
            try:
                player.seekTime(target)
                xbmc.sleep(500)
                if not player.isPlaying():
                    return
                if abs(player.getTime() - target) < 4:
                    self._log('Resumed and sought to {:.0f}s'.format(target))
                    return
            except Exception as e:
                self._log('Resume seek attempt failed: {}'.format(e),
                          xbmc.LOGWARNING)
                xbmc.sleep(500)
        self._log('Resume seek did not stick after retries', xbmc.LOGWARNING)

    def _handle_warning(self, slot, scheduled_dt):
        """Show the Engage banner prompt. Returns (action, snooze_minutes).

        action is one of: 'open', 'snooze', 'queue', 'cancel'. (The startup
        GUI-readiness wait is handled once in _wait_for_gui; we deliberately do
        NOT gate on the home window here, that would wrongly defer prompts
        whenever the user is browsing any menu.)
        """
        from resources.lib.prompt import show_engage_prompt
        addon_path = xbmcaddon.Addon(ADDON_ID).getAddonInfo('path')

        # Work out the sub-line + poster for the banner: next unwatched episode
        # for a TV favourite, next unwatched item for a sequence, or the
        # favourite's own thumbnail for a plain favourite.
        thumb = ''
        if slot.kind == 'sequence':
            nxt = self._resolve_next_item(slot)
            episode_label = nxt['label'] if nxt else 'All watched'
            thumb = nxt.get('art', '') if nxt else ''
        else:
            episode = self._resolve_episode_info(slot)
            episode_label = self._episode_label(episode)
            thumb = self._episode_art(episode) if episode else self._favourite_thumb(slot.favourite)

        # Brief notification so the user knows something just popped.
        name = slot.label if not episode_label else '{} ({})'.format(slot.label, episode_label)
        seconds_left = (scheduled_dt - datetime.now()).total_seconds()
        if seconds_left >= 0:
            minutes_left = max(1, int(round(seconds_left / 60)))
            plural = '' if minutes_left == 1 else 's'
            notif_msg = '{} starts in {} minute{}.'.format(name, minutes_left, plural)
        else:
            notif_msg = '{} should have started at {}.'.format(
                name, scheduled_dt.strftime('%H:%M'))
        xbmcgui.Dialog().notification(
            'Engage', notif_msg, xbmcgui.NOTIFICATION_INFO, 4000
        )

        action, snooze_minutes = show_engage_prompt(
            addon_path, slot.label, episode_label, scheduled_dt, thumb,
            slot.timeout_action
        )
        self._log('Prompt for slot {} returned action={}, snooze={}m'.format(
            slot.index + 1, action, snooze_minutes))
        return action, snooze_minutes

    def _check_test_mode(self):
        addon = xbmcaddon.Addon(ADDON_ID)
        if not addon.getSettingBool('test_mode'):
            return

        self._log('Test mode activated, triggering all enabled slots')
        addon.setSettingBool('test_mode', False)

        slots = self._reload_slots()
        for slot in slots:
            xbmcgui.Dialog().notification(
                'Engage',
                '[TEST] {}, activating favourite: {}'.format(slot.label, slot.favourite),
                xbmcgui.NOTIFICATION_INFO,
                3000
            )
            self._play_slot(slot)
            if self.monitor.waitForAbort(2):
                return

    def _wait_for_gui(self):
        """Give Kodi a good rest before we ever show a prompt.

        At start='login' the service fires within milliseconds of login, while
        the skin/home window is still loading. A WindowXMLDialog shown that
        early renders but never gets an input grab or render loop, it freezes.
        So we wait for the home window to appear AND ensure at least a 20s
        settle before the first check. (As Father Ted would say, the GUI's
        just resting.)
        """
        # Wait for the home window to become visible (up to ~60s as a backstop).
        for _ in range(60):
            if self.monitor.abortRequested():
                return
            if xbmc.getCondVisibility('Window.IsVisible(home)'):
                break
            if self.monitor.waitForAbort(1):
                return
        # A solid rest so the skin, library and dialogs are fully ready.
        self._log('GUI is up, resting {}s before first check'.format(STARTUP_REST_SECONDS))
        self.monitor.waitForAbort(STARTUP_REST_SECONDS)

    def run(self):
        self._log('Scheduler loop starting')
        self._wait_for_gui()

        while not self.monitor.abortRequested():
            # Any error in a tick must never kill the service, log it and
            # keep polling. A media centre can run for days between restarts.
            poll_interval = 30
            try:
                addon = xbmcaddon.Addon(ADDON_ID)
                poll_interval = addon.getSettingInt('poll_interval')
                if poll_interval < 5:
                    poll_interval = 30
                self._tick()
            except Exception:
                import traceback
                self._log('Scheduler tick failed:\n{}'.format(traceback.format_exc()), xbmc.LOGERROR)

            if self.monitor.waitForAbort(poll_interval):
                break

        self._log('Scheduler loop ended')

    def _tick(self):
        """One pass over all slots. Called every poll_interval seconds."""
        self._check_test_mode()

        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')

        # Purge stale tracking entries from previous days
        for tracker in (self.triggered, self.warned, self.cancelled, self.snoozed):
            stale = [k for k in tracker if not k.startswith(today_str)]
            for k in stale:
                del tracker[k]

        slots = self._reload_slots()
        self._debug('Checking {} enabled slots at {}'.format(len(slots), now.strftime('%H:%M:%S')))

        for slot in slots:
            try:
                self._process_slot(slot, now)
            except Exception:
                import traceback
                self._log('Slot {} processing failed:\n{}'.format(
                    slot.index + 1, traceback.format_exc()), xbmc.LOGERROR)

    def _process_slot(self, slot, now):
        """Evaluate a single slot against the current time."""
        key = self._today_key(slot)

        if key in self.triggered or key in self.cancelled:
            return

        scheduled_dt = self._get_scheduled_dt(slot)
        if scheduled_dt is None:
            return

        warning_dt = scheduled_dt - timedelta(minutes=slot.warning)

        # Handle snooze first, an expired snooze must re-prompt even if
        # we're now past scheduled_dt + 2min (snooze can outlast that window).
        snooze_until = self.snoozed.get(key)
        if snooze_until is not None:
            if now < snooze_until:
                self._debug('Slot {} snoozed until {}'.format(slot.index + 1, snooze_until.strftime('%H:%M')))
                return
            # Bring the banner back with a fresh short countdown rather than
            # playing immediately, the user can start, snooze again, queue,
            # or cancel; ignoring it auto-plays when the countdown hits zero.
            del self.snoozed[key]
            self._log('Snooze expired for slot {}, re-showing prompt'.format(slot.index + 1))
            action, snooze_min = self._handle_warning(slot, now + timedelta(seconds=60))
            self._apply_prompt_action(slot, key, action, snooze_min)
            return

        # Slot time already passed (Kodi was likely off at the time). Within
        # the configurable catch-up window we still prompt, the banner shows
        # how overdue it is and waits for the user (no auto-play). Beyond the
        # window we skip silently so a week of downtime doesn't spam prompts.
        if now > scheduled_dt + timedelta(minutes=2):
            if key in self.warned:
                return
            try:
                catchup_hours = xbmcaddon.Addon(ADDON_ID).getSettingInt('catchup_hours')
            except Exception:
                catchup_hours = 6
            if catchup_hours > 0 and now <= scheduled_dt + timedelta(hours=catchup_hours):
                # Cap how many times a missed-slot banner appears (persists
                # across restarts) so it stops nagging.
                from resources.lib import slots as slot_store
                try:
                    max_prompts = xbmcaddon.Addon(ADDON_ID).getSettingInt('catchup_max_prompts')
                except Exception:
                    max_prompts = 3
                if max_prompts < 1:
                    max_prompts = 3
                shown = slot_store.get_catchup_count(key)
                if shown >= max_prompts:
                    self._debug('Slot {} catch-up cap ({}) reached, skipping'.format(
                        slot.index + 1, max_prompts))
                    self.warned[key] = True
                    return
                self._log('Catch-up: slot {} was due at {}, prompting ({}/{})'.format(
                    slot.index + 1, scheduled_dt.strftime('%H:%M'), shown + 1, max_prompts))
                action, snooze_min = self._handle_warning(slot, scheduled_dt)
                if action != 'skip':
                    slot_store.bump_catchup_count(key, now.strftime('%Y-%m-%d'))
                    self._apply_prompt_action(slot, key, action, snooze_min)
                    self.warned[key] = True
            else:
                self._debug('Slot {} outside catch-up window, skipping'.format(slot.index + 1))
                self.warned[key] = True
            return

        # In the warning window
        if warning_dt <= now < scheduled_dt:
            if key not in self.warned:
                self._debug('Warning window for slot {}'.format(slot.index + 1))
                action, snooze_min = self._handle_warning(slot, scheduled_dt)
                self._apply_prompt_action(slot, key, action, snooze_min)
                if action != 'skip':
                    self.warned[key] = True

        # Scheduled time reached
        elif now >= scheduled_dt:
            if slot.autoopen:
                xbmcgui.Dialog().notification(
                    'Engage',
                    '{} is ready.'.format(slot.label),
                    xbmcgui.NOTIFICATION_INFO,
                    5000
                )
                self._play_slot(slot)
                self.triggered[key] = True
                self._log('Slot {} auto-opened at scheduled time'.format(slot.index + 1))
            elif key not in self.warned:
                # Kodi started right at/after the time, no warning was shown, no auto-open
                action, snooze_min = self._handle_warning(slot, scheduled_dt)
                self._apply_prompt_action(slot, key, action, snooze_min)
                if action != 'skip':
                    self.warned[key] = True


class _EngagePlayerMonitor(xbmc.Player):
    """Subclass of xbmc.Player so we get OnPlayBackStopped / OnPlayBackEnded
    callbacks. Used to fire 'Queue Next' slots when current playback ends."""

    def __init__(self, scheduler):
        super().__init__()
        self._scheduler = scheduler

    def onAVStarted(self):
        self._scheduler._on_playback_started()

    def onPlayBackEnded(self):
        self._scheduler._on_playback_ended()

    def onPlayBackStopped(self):
        self._scheduler._on_playback_ended()

