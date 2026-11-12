# Engage for developers

## Building

`build-zip.sh` (bash, Linux) reads the version from `VERSION`, which is kept in
sync with `service.engage/addon.xml`. It runs every `tests/test_*.py` and a
house rules check first and packages nothing if either fails. Then it produces:

- an installable zip in `Engage Dist/`, with forward slash paths and a
  directory entry for the add-on folder, so Kodi on every platform accepts it;
- a source zip in `Engage Git/`;
- a runnable copy of the add-on in `Engage App/`.

`build-zip.ps1` is the superseded Windows script, kept for reference and no
longer run.

Installing a build is the same as installing a release: see
[Quick start](../README.md#quick-start).

## Images

`tools/make_banner.py` draws `docs/screenshots/banner.png` and
`docs/screenshots/social.png` from plain shapes. It needs Pillow and the Noto
Sans fonts and is not part of the add-on.

## Source tree

```
service.engage/
  addon.xml               add-on manifest (service and program script)
  service.py              service entry point
  script.py               launcher menu and action dispatcher
  resources/
    settings.xml          General settings
    lib/
      scheduler.py        polling loop, episode resolution, playback, resume
      prompt.py           the countdown banner (WindowXMLDialog)
      slots.py            JSON slot store, backup and restore
      slot_manager.py     dialog driven slot management
      slots_window.py     the full screen Manage slots window
      slot_rules.py       slot validation and the API shape of a slot
      functional_lists.py Functional skin list slots
      api_server.py       loopback API the JellyStat dashboard manages slots through
      favourites_ui.py    favourites picker, add and remove
    skins/Default/        banner skins (standard, thin, uber thin, Full Monty), textures and icons
tests/                    standalone test scripts, Kodi modules stubbed
tools/                    build and image helpers
```

Back to the [README](../README.md).
