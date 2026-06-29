#!/bin/sh
set -e

# Do NOT start our own avahi/dbus — the host network must have one avahi (the host's),
# otherwise a host-name conflict drops the _raop._tcp record and the iPad "can't
# connect to spotineck". Publish AirPlay through the host's avahi over its system
# D-Bus (/run/dbus is mounted from the host). Wait until the socket is available.
i=0
while [ ! -S /run/dbus/system_bus_socket ]; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
        echo "shairport: host system D-Bus (/run/dbus) did not appear in 60s — starting anyway" >&2
        break
    fi
    echo "shairport: waiting for the host system D-Bus (/run/dbus)…"
    sleep 1
done

echo "shairport: D-Bus ready, starting shairport-sync (mDNS via the host avahi)"
exec shairport-sync -c /etc/shairport-sync.conf
