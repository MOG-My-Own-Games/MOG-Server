# Notice

MOG Server is licensed under AGPL-3.0-or-later (see `LICENSE`). The install
sandbox (`backend/handler/install/`, `backend/handler/install/auto_mode/`,
`backend/handler/filesystem/installer_detection.py`), its database-session
plumbing (`backend/decorators/database.py`), and `docker/icewm-preferences`
were adapted from [RomM](https://github.com/rommapp/romm), also AGPL-3.0-or-later.
Each adapted file carries its own `# Adapted from RomM ...` header.

MOG is a standalone project: it shares no branding, name, or UI with RomM
and is not affiliated with or endorsed by the RomM project. The credit above
reflects the AGPL's attribution/license-notice requirement for the source
code it's built on, not a product association.

Everything else in this repository (models, endpoints, auth, scanning,
metadata handlers, the single-container architecture, build/deploy tooling)
is original MOG code.
