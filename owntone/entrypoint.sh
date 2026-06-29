#!/bin/sh
set -e

# Каталоги под бд/кэш и библиотеку
mkdir -p /var/cache/owntone /music /run/dbus
chown -R owntone:owntone /var/cache/owntone 2>/dev/null || true

# mDNS внутри контейнера: dbus + avahi (нужно OwnTone для анонса себя и
# обнаружения AirPlay/Chromecast). В host-network уживается с avahi хоста.
rm -f /run/dbus/pid
dbus-daemon --system --fork
avahi-daemon --daemonize --no-chroot

exec owntone -f -c /etc/owntone.conf
