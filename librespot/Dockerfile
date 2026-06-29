# Обёртка над giof71/librespot: имя устройства Spotify Connect берётся из файла
# /config/device_name (его пишет наш api). При смене имени api рестартует контейнер.
FROM giof71/librespot:latest
COPY entrypoint.sh /lse.sh
ENTRYPOINT ["/bin/sh", "/lse.sh"]
