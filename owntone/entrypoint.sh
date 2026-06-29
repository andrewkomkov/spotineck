#!/bin/sh
set -e

# Каталоги под бд/кэш и библиотеку
mkdir -p /var/cache/owntone /music
chown -R owntone:owntone /var/cache/owntone 2>/dev/null || true

# mDNS. На host-сети должен быть РОВНО ОДИН avahi-daemon, иначе несколько демонов
# дерутся за имя хоста (nettop → nettop-2 → … → nettop-298) и записи _raop/_daap
# «прыгают» → колонки/AirPlay не подключаются. У Linux-хоста уже есть свой avahi —
# поэтому если его system D-Bus примонтирован (/run/dbus), используем ЕГО avahi и
# свой НЕ поднимаем. Контейнерный dbus+avahi оставлен фолбэком (хост без avahi).
if [ -S /run/dbus/system_bus_socket ]; then
    echo "owntone: использую avahi/D-Bus хоста (/run/dbus) — свой не поднимаю"
else
    echo "owntone: host D-Bus не найден — поднимаю собственный dbus+avahi"
    mkdir -p /run/dbus /run/avahi-daemon
    rm -f /run/dbus/pid /run/dbus/system_bus_socket /run/avahi-daemon/pid /run/avahi-daemon/socket
    dbus-daemon --system --fork
    avahi-daemon --daemonize --no-chroot
fi

exec owntone -f -c /etc/owntone.conf
