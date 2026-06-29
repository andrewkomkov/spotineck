#!/bin/sh
set -e

# Directories for the db/cache and the library
mkdir -p /var/cache/owntone /music
chown -R owntone:owntone /var/cache/owntone 2>/dev/null || true

# mDNS. The host network must have EXACTLY ONE avahi-daemon, otherwise several
# daemons fight over the host name (nettop → nettop-2 → … → nettop-298) and the
# _raop/_daap records "jump around" → speakers/AirPlay won't connect. A Linux host
# already has its own avahi — so if its system D-Bus is mounted (/run/dbus), use ITS
# avahi and do NOT start our own. The container dbus+avahi is kept as a fallback
# (host without avahi).
if [ -S /run/dbus/system_bus_socket ]; then
    echo "owntone: using the host's avahi/D-Bus (/run/dbus) — not starting our own"
else
    echo "owntone: host D-Bus not found — starting our own dbus+avahi"
    mkdir -p /run/dbus /run/avahi-daemon
    rm -f /run/dbus/pid /run/dbus/system_bus_socket /run/avahi-daemon/pid /run/avahi-daemon/socket
    dbus-daemon --system --fork
    avahi-daemon --daemonize --no-chroot
fi

exec owntone -f -c /etc/owntone.conf
