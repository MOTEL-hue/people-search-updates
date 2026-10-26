# people-search-updates

Update channel and installer builds for the PeopleSearch desktop program.

This repository contains **program code only**: no personal data, no passwords, no keys.
The program checks `manifest.json`, downloads the listed files, verifies their SHA-256 hashes,
and installs them only after the user approves. It never uploads any data here.

* Installer: see the latest release (`PeopleSearch-Setup.exe`).
* Publish a new version: bump `VERSION` in `code/app.py`, run `python make_release.py`, push `code/` and `manifest.json`.
