# people-search-updates

Update channel for the PeopleSearch desktop program.

This repository contains **program code only**: no personal data, no passwords, no keys.
The program checks `manifest.json`, downloads the listed files, verifies their SHA-256 hashes,
and installs them only after the user approves.
The program never uploads any data to this repository.

To publish a new version: bump `VERSION` in `app.py`, run `python make_release.py`, and push `manifest.json` and `code/`.
