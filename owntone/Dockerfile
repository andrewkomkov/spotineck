# ─────────────────────────────────────────────────────────────────────────────
# spotineck/owntone — OwnTone built from the latest release sources.
# Multi-stage: the heavy build stage is thrown away, only the binary +
# runtime libraries make it into the final image. Support for AirPlay 2
# (libplist/libsodium/libgcrypt), Chromecast (--enable-chromecast), websockets,
# ALSA, pipe sources.
# ─────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS builder

ARG OWNTONE_VERSION=29.2

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates \
        autoconf automake libtool gettext gawk gperf bison flex pkg-config \
        libconfuse-dev libunistring-dev libsqlite3-dev \
        libavcodec-dev libavformat-dev libavfilter-dev libswscale-dev libavutil-dev \
        libasound2-dev libxml2-dev libgcrypt20-dev libavahi-client-dev zlib1g-dev \
        libevent-dev libplist-dev libsodium-dev libjson-c-dev libwebsockets-dev \
        libcurl4-openssl-dev libprotobuf-c-dev libgnutls28-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${OWNTONE_VERSION}" \
        https://github.com/owntone/owntone-server.git .

RUN autoreconf -fi \
    && ./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var \
        --enable-chromecast \
    && make -j"$(nproc)" \
    && make install DESTDIR=/staging

# ─────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

ARG OWNTONE_VERSION=29.2
LABEL org.opencontainers.image.title="spotineck-owntone" \
      org.opencontainers.image.description="OwnTone ${OWNTONE_VERSION} — AirPlay 2 + Chromecast + pipe, minimal multi-stage build" \
      org.opencontainers.image.source="https://github.com/owntone/owntone-server" \
      org.opencontainers.image.version="${OWNTONE_VERSION}" \
      org.opencontainers.image.licenses="GPL-3.0"

RUN apt-get update && apt-get install -y --no-install-recommends \
        libconfuse2 libunistring2 libsqlite3-0 \
        libavcodec59 libavformat59 libavfilter8 libswscale6 libavutil57 libswresample4 \
        libasound2 libxml2 libgcrypt20 libavahi-client3 libavahi-common3 zlib1g \
        libevent-2.1-7 libevent-pthreads-2.1-7 libevent-openssl-2.1-7 \
        libplist3 libsodium23 libjson-c5 libwebsockets17 \
        libcurl4 libprotobuf-c1 libgnutls30 libgomp1 \
        avahi-daemon dbus libnss-mdns alsa-utils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 998 -d /var/cache/owntone owntone \
    && usermod -aG audio owntone

# Only /usr (binary + libs + web assets). /var and /etc from staging aren't needed
# (/var/run in the base image is a symlink, we drop our own config below).
COPY --from=builder /staging/usr/ /usr/
COPY owntone.conf /etc/owntone.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /music /var/cache/owntone

EXPOSE 3689 3688
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://127.0.0.1:3689/api/config >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
