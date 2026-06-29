#!/bin/sh
# Read the Spotify Connect device name from a file (written by our api).
# On rename, the api restarts this container → we re-read the file.
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
