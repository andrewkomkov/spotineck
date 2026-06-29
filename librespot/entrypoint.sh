#!/bin/sh
# Имя Spotify Connect устройства берём из файла (его пишет наш api).
# При смене имени api рестартует этот контейнер → перечитываем файл.
NAME=$(cat /config/device_name 2>/dev/null)
[ -z "$NAME" ] && NAME="spotineck"
echo "librespot: device name = $NAME"

exec /usr/bin/librespot \
    --name "$NAME" \
    --backend pipe \
    --device /music/spotify \
    --bitrate 320 \
    --initial-volume 100 \
    --cache /cache \
    --zeroconf-backend libmdns
