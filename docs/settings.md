# Engage settings

Open them from **Add-ons > Program add-ons > Engage**, or **Services > Engage >
Configure**.

## General

| Setting | What it does |
|---|---|
| **Poll interval** | How often the scheduler checks, in seconds. |
| **Catch-up window** | Hours after a missed slot it will still prompt you (0 is off, up to 12). |
| **Max missed-slot banners** | How many times an overdue banner reappears before giving up. |
| **Skip a slot watched in the last** | Hours after you played a slot's show (or a sequence item) that it stays quiet, 0 is off. |
| **Default banner style** | Standard (the full height banner with text buttons), Thin (a slim banner with icon buttons), Uber thin (one line with icon buttons), Uber thin, no buttons (one line that counts down to the slot's countdown action) or Full Monty (centre screen with the picture and synopsis). Every slot whose own **Banner style** is left on Default follows this, from the next banner on. |
| **Show all favourites in pickers** | Off lists only video and TV favourites. |
| **Debug logging** | Verbose logging to `kodi.log` for troubleshooting. |
| **Test mode** | Fire all enabled slots immediately (handy for testing). |
| **Allow JellyStat to manage slots** | On by default. Lets the JellyStat add-on's web dashboard on the same Kodi list, add, edit and delete slots and take or restore a backup. Engage listens on this Kodi only, never on the network; JellyStat's own password guards the dashboard. |

## Upgrading

Installing a newer zip replaces the old one. Your slots and settings are kept
in `userdata` and carry over untouched. Add-ons installed from the
DeliciousCoffee repository update on their own.

Back to the [README](../README.md) or the full [feature list](features.md).
