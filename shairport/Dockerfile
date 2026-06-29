# ─────────────────────────────────────────────────────────────────────────────
# spotineck/shairport — Shairport Sync (classic AirPlay 1) from source.
# Multi-stage: the heavy build is thrown away, only the binary + runtime libs make
# it into the final image. Classic AirPlay 1 (no --with-airplay-2): RSA/AES via
# openssl, no nqptp/PTP — reliable in Docker. ALAC is decoded by the built-in decoder
# (we don't pull in ffmpeg).
#
# IMPORTANT about mDNS. The image does NOT start its own avahi-daemon. Under
# network_mode: host that would give a SECOND avahi in one network namespace next to
# owntone's avahi → host-name conflict and a "jumping"/disappearing _raop._tcp record →
# on iPhone/iPad "can't connect to spotineck". Instead, shairport publishes AirPlay
# through the SINGLE avahi from the owntone container over the shared system D-Bus
# socket (shared dbus-socket + avahi-run volumes). Just like librespot gets by with its
# own libmdns — one mDNS responder per host network.
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

# Classic AirPlay 1: avahi (mDNS client to the shared daemon), openssl, metadata,
# pipe output (PCM to /music/airplay for OwnTone) and dummy as a fallback backend.
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
      org.opencontainers.image.description="Shairport Sync ${SHAIRPORT_VERSION} classic (AirPlay 1) → pipe, mDNS via the shared owntone avahi" \
      org.opencontainers.image.source="https://github.com/mikebrady/shairport-sync" \
      org.opencontainers.image.version="${SHAIRPORT_VERSION}" \
      org.opencontainers.image.licenses="GPL-2.0"

# Runtime dependencies only. libavahi-client3 pulls in libdbus-1-3 — needed to
# publish AirPlay through owntone's avahi over the shared system D-Bus.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpopt0 libconfig9 libssl3 \
        libavahi-client3 libavahi-common3 libdbus-1-3 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /staging/usr/local/bin/shairport-sync /usr/local/bin/shairport-sync
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
