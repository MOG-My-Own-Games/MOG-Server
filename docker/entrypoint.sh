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
    # -xdev: a volume mounted inside /mog (the cache, often huge) keeps its own owner, only its folder is touched, and a
    # volume that refuses chown (mergerfs, NFS, rootless Docker) must not stop the boot with set -e.
    find /mog /opt/proton -xdev \( ! -user mog -o ! -group mog \) -exec chown mog:mog {} + ||
        echo "entrypoint: could not change the owner of everything in /mog and /opt/proton, going on" >&2
    # The install caches made while the server still ran as root (the cache is a volume, which -xdev above leaves alone): the
    # server could not delete them, so expired ones stayed and a deleted one stayed on disk. Only a session's own folder that is
    # not the server's is walked, so the next starts pay nothing and a huge cache is never scanned.
    if [ -d /mog/cache/installs ]; then
        for dir in /mog/cache/installs/*/; do
            [ -d "$dir" ] || continue
            [ "$(stat -c %U "$dir")" = "mog" ] ||
                chown -R mog:mog "$dir" ||
                echo "entrypoint: could not change the owner of $dir, going on" >&2
        done
    fi
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
