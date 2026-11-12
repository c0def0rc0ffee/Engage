# Engage features

Everything Engage does, in full. The [README](../README.md) has the short version.

## Slots

- **Unlimited watch slots.** Schedule any favourite (TV show, movie, folder,
  playlist) at a time that repeats weekly on chosen days, every 2, 3 or 4
  weeks on chosen days (every other Tuesday, say, starting this week or next),
  monthly on a day of the month, yearly on a date, or just once on a one off
  date. A monthly slot on the 29th to 31st runs on the last day of a shorter
  month, and a yearly 29th February runs on the 28th outside leap years.
- **Several days and times per slot.** Pick any combination of weekdays
  (useful for a show that runs every weekday, say The Simpsons every weekday at
  6), and add more days with their own time to the same slot (Voyager on
  Monday and Wednesday at 19:00 and on Saturday at 15:00). Every showing plays
  the next one in line, whichever day and time it falls on.
- **Knows the next episode.** For TV shows it resolves the exact next
  *unwatched* episode, and skips season 0 specials until the main run is done.
  It plays it as a proper library or plugin item, so metadata and watched
  tracking just work.
- **Movie sequences.** Build an ordered list of films (all the Marvel movies,
  say); each scheduled showing plays the next one you haven't watched.
- **Binge and crossover orders.** Weave episodes from several shows into one
  hand built order (Arrow S3E1 to 3, then Flash S1E1 to 4, then back to Arrow)
  for interlinked series like the Arrowverse. It plays the next unwatched in
  your order.

## Functional skin lists

Schedule one of the lists you build in the Functional skin (a "Saturday Night
Movie" list, say) and choose how each showing picks from it:

- the next unwatched item in the list's order;
- every item in turn, watched or not, wrapping back to the top after the last;
- a random unwatched item;
- any item at random.

A list of TV shows can also play the next unwatched episode of every show in
it, back to back as one playlist, so Voyager, Babylon 5 and Doctor Who each get
an episode in one evening.

- The list stays in the skin: add, reorder or remove items there and the next
  showing follows.
- Once everything in a random unwatched list is watched, it picks from the
  whole list again.
- A plain file or plugin item in a list has no watched count Engage can read,
  so the unwatched modes always treat it as unwatched. Turn on the list's
  Remove Watched switch in the skin to have such items drop off once seen.
- A random slot can optionally keep sequels in order: films in the same Kodi
  movie set, and episodes of the same show, then only come up in turn, so
  Prisoner of Azkaban never plays before Chamber of Secrets. The order follows
  either where they sit in the list (reorder the list in the skin to fix an
  exception) or release date. A film Kodi has not put in a set counts as
  standalone.
- In the Functional skin's Coming up list, a list slot can name either the
  list or the film or episode each showing will play. Random slots draw their
  pick as soon as the showing appears there, so the name holds right through to
  the banner.
- **Ends At in the Functional skin.** Each slot has a switch for whether the
  Functional skin's Coming up list may show an "Ends At" line after a run of
  back to back slots that ends with it. It changes only what the skin shows.
- **Coming up countdown.** One switch in the settings, off by default, counts
  down to the slot that plays next: in the last 30 minutes (5 to 120, your
  choice) a skin's Coming up list shows the minutes left and a bar that empties
  as it nears. The Functional skin draws it on that slot's line.

## The countdown banner

A slim banner slides down from the top with the show's **poster**, the show or
episode, and a live countdown. It stays out of your way. Five layouts:

| Layout | What it looks like |
|---|---|
| **Standard** | The full height banner with text buttons. |
| **Thin** | About half the height, with icon buttons and a one line hint for the focused icon. |
| **Uber thin** | A single line of text with small icon buttons and no poster. |
| **Uber thin, no buttons** | The same line with nothing to press. It just counts down to the slot's *When the countdown ends* action. Back still cancels it, and a missed slot's catch up banner gets the buttons back. |
| **Full Monty** | For movie night: the fanart fills the screen and a centred panel shows the big poster, the year, running time, genres and rating, the tagline and the synopsis, with the buttons underneath. |

Pick a default in the settings and override it per slot. One more switch, off
by default, keeps the Full Monty for when nothing is playing: with it on, a
Full Monty banner that would open over something already playing, such as a
slot that follows straight on from another, opens as Uber thin instead.

## One tap actions

- **Start Now**: play immediately.
- **Snooze**: 5, 10, 15, 30 or 60 minutes, then it asks again.
- **Queue Next**: finish what you're watching, then play the slot.
- **Stop & Resume After**: stop now, play the slot, then **resume what you were
  watching** from where you left off.
- **Cancel**: skip this one.

**You decide what happens if you ignore it.** Each slot picks what the
countdown does when it reaches zero: **queue it** for when your current show
finishes (the default), **start it now**, **stop and resume after**, or **do
nothing** and let it pass.

## Missed and early

- **Catch up window.** Had Kodi switched off when a show was due? Turn it on
  within a configurable window (up to 12 hours) and Engage still prompts you.
  Leave it off for a week and it stays quiet, with no pile up of stale prompts.
- **Watched it early?** If you've played the show within the last few hours
  (4 by default, adjustable, 0 turns it off) the slot stays quiet that day
  instead of nagging you about what you just watched.

## Managing slots

- **Slot manager.** A full screen list of every slot with what it plays, when
  it runs and when it's next due, sortable by next up, A to Z, time of day or
  type. Add, edit, duplicate and delete slots through simple on screen menus
  that work well with a remote. No fiddly keypads.
- **Backup and restore.** Save all your slots and settings to a JSON file and
  restore them on any box. Handy for moving to a new media centre.

No streaming service, no accounts, no cloud: Engage uses your Kodi Favourites
and library.

See also [settings](settings.md) and [skin properties](skinners.md).
