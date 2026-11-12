# Engage skin properties

While the service runs it publishes the next slot due as properties on Kodi's
Home window. They are refreshed after every check, after a slot is saved, after
a library change and a few seconds after any playback ends, and cleared when
the service stops or no slot is enabled. Each property is written only when its
value changes.

The Functional skin uses them for its "Coming up" block (0.17.0 and later) and
the numbered rows below for its "Coming up" list (0.18.0 and later).

## The next slot

| `Window(home).Property(...)` | What it holds | Example |
|---|---|---|
| `Engage.Next.Label` | The slot's label. Empty when nothing is coming up, so gate the block on this. | `Voyager` |
| `Engage.Next.Episode` | The next episode or film line, as on the banner. Empty for a plain favourite. | `S03E04, Gravity` |
| `Engage.Next.When` | When it is due, in words. | `Tonight 19:00`, `Tomorrow 19:00`, `Thursday 19:00`, `Thursday 22/10/2026 19:00` |
| `Engage.Next.Day` | The weekday. | `Thursday` |
| `Engage.Next.Date` | The date, `dd/mm/yyyy`. | `15/10/2026` |
| `Engage.Next.Time` | The time, `HH:MM`. | `19:00` |
| `Engage.Next.Thumb` | Poster or thumbnail path, may be empty. | |
| `Engage.Next.SlotId` | The slot's id. | `3` |
| `Engage.Next.State` | `upcoming`, `warning`, `snoozed` or `overdue`. | `upcoming` |
| `Engage.Next.Count` | How many slots are enabled. | `4` |

## The numbered rows

Beside that single set, the next 50 occurrences across every enabled slot, in
due order over the coming seven days, are published as numbered rows.

- Each row is the slot's title and when it is due, deliberately without an
  episode line. Number 1 is the same occurrence as the single set.
- A Functional list slot is named differently: a random one by its list, an in
  order one by the item due to play, and a second showing of it by the item
  after that.
- Once an in order unwatched or one episode from each show list is all watched,
  its slot has no rows (it still comes due and says it is all watched), so the
  next slot moves up.
- A weekly slot on two days this week appears twice.
- A number with no occurrence has both properties cleared, so gate the list on
  `Engage.Next.1.Label` and each row on its own label.
- Rows are written from 50 down to 1 with each row's label last, and cleared
  from 1 upwards with each row's label first, so a half updated list is never
  shown.

| `Window(home).Property(...)` | What it holds | Example |
|---|---|---|
| `Engage.Next.1.Label` to `Engage.Next.50.Label` | The slot's label; for a Functional list slot, the list's name when it picks at random, or the item it will play when it plays in order. | `Voyager` |
| `Engage.Next.1.When` to `Engage.Next.50.When` | When it is due, the same words as `Engage.Next.When`. | `Tonight 19:00` |
| `Engage.Next.1.Due` to `Engage.Next.50.Due` | When it is due, as `YYYY-MM-DDTHH:MM` for a skin to calculate with. | `2026-10-22T19:00` |
| `Engage.Next.1.Runtime` to `Engage.Next.50.Runtime` | How long what it will play runs, in whole seconds; empty when that cannot be known, such as a random list pick not yet drawn, a plain file or a plugin. | `2640` |
| `Engage.Next.1.EndsAt` to `Engage.Next.50.EndsAt` | `1` when the slot's "Ends At line after it" switch is on (the default), empty when off; a setting for the Functional skin's Coming up list. | `1` |

Back to the [README](../README.md).
