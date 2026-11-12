<p align="center"><img src="docs/screenshots/banner.png" width="100%" alt="Engage, your weekly watch schedule, inside Kodi. Three tiles sketch a slim countdown banner, a list of watch slots and the Full Monty movie night panel."></p>

<p align="center"><a href="#quick-start">Quick start</a> · <a href="#what-it-does">Features</a> · <a href="#documentation">Docs</a> · <a href="docs/settings.md">Settings</a> · <a href="docs/skinners.md">Skin properties</a> · <a href="../../releases">Releases</a></p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.11.10-blue" alt="Version 1.11.10">
  <img src="https://img.shields.io/badge/licence-GPL--2.0--or--later-green" alt="GPL 2.0 or later licence">
  <img src="https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white" alt="Python 3.8 or newer">
  <img src="https://img.shields.io/badge/Kodi-19%20Matrix%2B-17B2E7?logo=kodi&logoColor=white" alt="Kodi 19 Matrix or newer">
  <img src="https://img.shields.io/badge/add--on-service-17B2E7" alt="Kodi service add-on">
  <img src="https://img.shields.io/badge/repository-DeliciousCoffee-6F4E37" alt="Available from the DeliciousCoffee repository">
</p>

Prompts you with a countdown banner when a weekly watch slot arrives, with the next unwatched episode already chosen, instead of you remembering what to put on.

<p align="center"><img src="docs/screenshots/hero.png" width="640" alt="The standard Engage banner across the top of the Kodi home screen: the poster for a sample film, the slot name Film night, the film it will play, a countdown reading Starts in 1m 54s, and Start Now, Snooze and Cancel buttons."></p>

## Quick start

1. Install the [DeliciousCoffee repository](https://github.com/c0def0rc0ffee/DeliciousCoffee) once, then install Engage from it. Kodi keeps it updated.
2. Or download `service.engage-x.y.z.zip` from [Releases](../../releases), turn on **Settings > System > Add-ons > Unknown sources**, and use **Install from zip file**.
3. Add the show or film to **Kodi Favourites**. For a TV show, favourite the show itself.
4. Open Engage, choose **Manage slots...**, then **Add slot** and **Single show or film**, and pick the favourite, a day and a time.

## What it does

| Feature | In short |
|---|---|
| **Slots** | Weekly, every 2 to 4 weeks, monthly, yearly or once, on several days and times. |
| **Next episode** | Plays the next unwatched episode, specials last. |
| **Movie sequences** | An ordered run of films, next unwatched each time. |
| **Binge orders** | Episodes from several shows woven into one crossover order. |
| **Functional skin lists** | Play a skin list in order, at random or one episode per show. |
| **Banner layouts** | Standard, Thin, Uber thin, Uber thin with no buttons, or Full Monty. |
| **One tap actions** | Start Now, Snooze, Queue Next, Stop & Resume After, Cancel. |
| **Catch up** | Prompts for a missed slot when Kodi comes back, and stays quiet if you watched early. |
| **Backup and restore** | All slots and settings to one JSON file. |

<p align="center">
  <img src="docs/screenshots/slots.png" width="400" alt="The Manage slots window listing five sample slots: a weekly film sequence, a Functional skin list, an every two weeks favourite, a monthly favourite and a one off date, each with when it runs and when it is next due.">
  <img src="docs/screenshots/full-monty.png" width="400" alt="The Full Monty banner for a sample film: a large poster, the year, running time, genre and rating, a tagline and a short synopsis over the film's fanart, with Start Now, Snooze and Cancel buttons.">
</p>

## Documentation

- [Features in full](docs/features.md)
- [Settings](docs/settings.md)
- [Skin properties](docs/skinners.md) for skin authors
- [Building and source tree](docs/developers.md)

## Licence

[GPL-2.0-or-later](LICENSE)
