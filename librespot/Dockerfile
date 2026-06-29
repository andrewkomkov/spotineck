# Wrapper around giof71/librespot: the Spotify Connect device name is read from the
# file /config/device_name (written by our api). On rename, the api restarts the container.
FROM giof71/librespot:latest
COPY entrypoint.sh /lse.sh
ENTRYPOINT ["/bin/sh", "/lse.sh"]
