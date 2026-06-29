#!/bin/sh
set -e

# Свой avahi/dbus НЕ поднимаем — на host-сети должен быть один avahi (хостовый),
# иначе конфликт имени хоста роняет запись _raop._tcp и iPad «не удаётся
# подключиться к spotineck». Публикуем AirPlay через avahi хоста по его system
# D-Bus (/run/dbus примонтирован с хоста). Ждём, пока сокет доступен.
i=0
while [ ! -S /run/dbus/system_bus_socket ]; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
        echo "shairport: system D-Bus (/run/dbus) не появился за 60с — стартуем как есть" >&2
        break
    fi
    echo "shairport: жду system D-Bus хоста (/run/dbus)…"
    sleep 1
done

echo "shairport: D-Bus готов, запускаю shairport-sync (mDNS через avahi хоста)"
exec shairport-sync -c /etc/shairport-sync.conf
