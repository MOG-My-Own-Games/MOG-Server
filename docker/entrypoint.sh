#!/bin/sh
# Starts as root (see the Dockerfile: no USER before this runs) specifically
# to fix ownership of bind-mounted volumes before dropping to the
# unprivileged `mog` user. A host directory Docker auto-creates for a bind
# mount (./data/mog, ./data/library in docker-compose.yml) is root-owned by
# default, which the `mog` user (uid 10001) then can't write to - caught
# live as "unable to open database file" out of SQLite, since /mog itself
# was silently unwritable (startup.py's _ensure_dirs only warns on a mkdir
# failure, it doesn't fail the boot over a directory it may not need to
# create itself - see that function's own comment).
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p /mog /opt/proton
    chown -R mog:mog /mog /opt/proton
    # The backup volume, when one is mounted (Docker creates the host directory root-owned): only the folder itself, as it may
    # hold many files. Nothing is created here without a volume: backups would land in the container and be lost with it.
    if grep -q " /backups " /proc/mounts; then
        chown mog:mog /backups
    fi
    # CMD is always a fixed, simple "python3 main.py" (no args needing
    # quoting), so joining with $* to hand su a single -c string is safe.
    exec su -s /bin/sh mog -c "cd /src/backend && exec $*"
fi

exec "$@"
