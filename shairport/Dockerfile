# ─────────────────────────────────────────────────────────────────────────────
# spotineck/shairport — Shairport Sync (классический AirPlay 1) из исходников.
# Multi-stage: тяжёлый билд выкидывается, в финал едут только бинарь + рантайм-либы.
# Классический AirPlay 1 (без --with-airplay-2): RSA/AES через openssl, без nqptp/PTP
# — надёжно в Docker. ALAC декодируется встроенным декодером (ffmpeg не тянем).
#
# ВАЖНО про mDNS. Образ НЕ запускает свой avahi-daemon. В network_mode: host это
# дало бы ВТОРОЙ avahi в одном сетевом неймспейсе рядом с avahi из owntone →
# конфликт имени хоста и «прыгающая»/исчезающая запись _raop._tcp → на iPhone/iPad
# «не удаётся подключиться к spotineck». Вместо этого shairport публикует AirPlay
# через ЕДИНЫЙ avahi из контейнера owntone по общему system D-Bus сокету
# (шаренные volume dbus-socket + avahi-run). Так же, как librespot обходится своим
# libmdns — один ответчик mDNS на host-сети.
# ─────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS builder

ARG SHAIRPORT_VERSION=5.0.4

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates \
        autoconf automake libtool pkg-config xxd \
        libpopt-dev libconfig-dev libssl-dev libavahi-client-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${SHAIRPORT_VERSION}" \
        https://github.com/mikebrady/shairport-sync.git .

# Классический AirPlay 1: avahi (mDNS-клиент к общему демону), openssl, метаданные,
# pipe-выход (PCM в /music/airplay для OwnTone) и dummy как запасной бэкенд.
RUN autoreconf -fi \
    && ./configure --sysconfdir=/etc \
        --with-avahi \
        --with-ssl=openssl \
        --with-metadata \
        --with-pipe \
        --with-dummy \
    && make -j"$(nproc)" \
    && make install DESTDIR=/staging

# ─────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

ARG SHAIRPORT_VERSION=5.0.4
LABEL org.opencontainers.image.title="spotineck-shairport" \
      org.opencontainers.image.description="Shairport Sync ${SHAIRPORT_VERSION} classic (AirPlay 1) → pipe, mDNS через общий avahi owntone" \
      org.opencontainers.image.source="https://github.com/mikebrady/shairport-sync" \
      org.opencontainers.image.version="${SHAIRPORT_VERSION}" \
      org.opencontainers.image.licenses="GPL-2.0"

# Только рантайм-зависимости. libavahi-client3 тянет libdbus-1-3 — он нужен, чтобы
# публиковать AirPlay через avahi из owntone по общему system-шине D-Bus.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpopt0 libconfig9 libssl3 \
        libavahi-client3 libavahi-common3 libdbus-1-3 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /staging/usr/local/bin/shairport-sync /usr/local/bin/shairport-sync
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
