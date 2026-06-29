# spotineck

**A universal, synchronized multi-room zone out of speakers from different ecosystems.**

Spotineck turns a pile of incompatible speakers — **HomePod** (AirPlay), **Google
Home / Nest** and any **Chromecast**, plus the host's local audio — into **one
synchronized zone** you can stream to from **Spotify** (Spotify Connect) and from
**any Apple device** (AirPlay). All of it with a polished Spotify-style web UI and a
clean, agent-friendly API.

> Why: HomePod, Alexa/Echo, Google Home and Chromecast live in three incompatible
> multi-room protocols and **don't combine** into one group out of the box. Spotineck
> is the bridge: one input (Spotify/AirPlay) → a synchronized fan-out to every speaker
> at once.

**Website:** https://andrewkomkov.github.io/spotineck/

```
                            ┌──────────────── nettop (Linux + Docker) ───────────────┐
 Spotify (any device) ─────►│ librespot ─┐                                            │
                            │            ├─► OwnTone ─┬─► AirPlay 2 ─► HomePod         │
 iPhone/iPad/Mac (AirPlay)─►│ shairport ─┘            ├─► Chromecast ─► Google / TV    │
                            │   (pipe sources)        └─► ALSA ───────► host speakers  │
 Browser / Agent ──HTTP/WS─►│ spotineck-api (REST + WebSocket + web UI)               │
                            └────────────────────────────────────────────────────────┘
```

## Features

- 🔊 **Synchronized zone** of AirPlay 2 (HomePod) + Chromecast (Google/TV) + local ALSA.
- 🎚 **Per-speaker volume and delay (offset)** right in the UI — fix cross-protocol drift
  by ear, at runtime, without restarting.
- 🟢 **Spotify Connect** — the zone shows up as a "spotineck" device in the Spotify app.
- ﹫ **AirPlay receiver** — spotineck appears as an AirPlay speaker; any audio from Apple
  (not just Spotify) is fanned out to the whole group, Chromecast included.
- 🎛 **Spotify Web API** (optional) — start music from the UI/agent without a phone, rich
  metadata (track/artwork), catalog search.
- ✏️ **Rename the device** from the UI/API.
- 🔐 **AirPlay PIN authorization** of speakers right in the interface.
- 📱 **Web interface** in Spotify style, mobile-friendly.
- 🤖 **Agent-friendly API**: clean REST + OpenAPI (`/docs`, `/openapi.json`) + WebSocket.

## How it works

Spotineck doesn't reinvent the multi-room engine — it uses **[OwnTone](https://github.com/owntone/owntone-server)**
(a synchronized AirPlay 2 / Chromecast player). Each "input" is a small service that
writes PCM into a named pipe, and OwnTone reads it as a source and synchronously feeds
it to every enabled output:

| Service | What it does |
|---|---|
| **owntone** | The core: reads the pipe sources, synchronously fans out to AirPlay 2 / Chromecast / ALSA. Exposes a REST+WS API. Custom image built from OwnTone 29.2 sources (`andrewkomkov/owntone`). |
| **librespot** | Spotify Connect endpoint → writes to `/music/spotify`. The device name comes from a file (changed from the UI). |
| **shairport** | AirPlay 1 receiver ([shairport-sync](https://github.com/mikebrady/shairport-sync) 5.0.4 classic, custom image `andrewkomkov/spotineck-shairport`) → writes to `/music/airplay`. Publishes mDNS through the single shared avahi from owntone (see below). |
| **api** | FastAPI: smooths the OwnTone API into clean REST/WS, adds the Spotify Web API, device renaming, and serves the web UI. |

## Requirements

- **Linux host** (macOS/Windows won't work: `network_mode: host` is required for mDNS
  and Spotify Connect zeroconf).
- **Docker + docker compose**.
- Speakers and host on the **same network/VLAN** (mDNS doesn't cross subnets).
- For Spotify Connect — **Spotify Premium**.

## Quick start

```bash
git clone https://github.com/andrewkomkov/spotineck.git
cd spotineck
make setup     # creates FIFOs, config, .env from the example
make up        # builds and brings up the stack
```

1. Open `http://<host>:8080` — the web interface.
2. In the **Spotify** app pick the **spotineck** device and hit play.
3. In the UI's **Speakers** tab, enable the speakers you want in the group — playback
   starts in sync.
4. Audio from Apple: on iPhone/iPad/Mac pick **spotineck** in the **AirPlay** menu.

### Spotify Web API (optional)

Enables starting playback from the UI/agent, metadata and search. Create an app on
[developer.spotify.com](https://developer.spotify.com/dashboard) (Web API, redirect URI
`http://127.0.0.1:8080/api/spotify/callback`), put `SPOTIFY_CLIENT_ID/SECRET` into `.env`,
restart the api and run the OAuth flow: `http://<host>:8080/api/spotify/login` (over an
SSH tunnel `ssh -L 8080:127.0.0.1:8080 <host>`, since Spotify requires a loopback redirect).

## API (for agents and integrations)

Clean REST with an OpenAPI schema at `/docs` and `/openapi.json`. Quick summary — `GET /api/capabilities`.

| Method | Purpose |
|---|---|
| `GET /api/state` | full snapshot: playback + speakers + queue |
| `POST /api/playback/{play,pause,toggle,next,previous}` | transport |
| `POST /api/playback/volume` `{volume}` | zone volume |
| `GET /api/speakers` · `POST /api/speakers/{id}` `{selected,volume,offset_ms}` | speakers: into the group / volume / delay |
| `POST /api/speakers/{id}/verify` `{pin}` | AirPlay PIN |
| `PUT /api/speakers/group` `{ids}` | set the whole group membership at once |
| `GET/POST /api/device-name` `{name}` | name in Spotify Connect |
| `GET /api/search?q=` | search |
| `POST /api/spotify/play-here` · `/api/spotify/play` `{uri}` | start on spotineck via the Spotify Web API |
| `WS /ws` | real-time state push |

## Published images

- `andrewkomkov/owntone:29.2` — OwnTone 29.2 (AirPlay 2 + Chromecast + runtime offset), multi-stage from source.
- `andrewkomkov/spotineck-librespot:latest` — librespot with the device name read from a file.
- `andrewkomkov/spotineck-shairport:5.0.4` — Shairport Sync 5.0.4 classic (AirPlay 1) → pipe, multi-stage from source.

### mDNS: one avahi per host network

`network_mode: host` means a single shared network namespace. It must have
**exactly one** `avahi-daemon`. A Linux host usually already runs one, so both
containers (`owntone` and `shairport`) use the **host's avahi** through its system
D-Bus (`bind /run/dbus`) and **don't start** their own. If you don't do this, the
host network ends up with several avahi instances (the host's plus one in each
container), they fight over the host name (`nettop` → `nettop-2` → … → `nettop-298`),
the `_raop._tcp`/`_daap._tcp` records "jump around", and on iPhone/iPad this looks
exactly like "**can't connect to spotineck**". Likewise librespot gets by with its
own `libmdns`.

> If the host has no avahi — the `owntone` entrypoint will start its own dbus+avahi
> as a fallback (then you don't need to disable `shairport`, but double-check there
> really is no host avahi, otherwise the conflict comes back).

## Limitations (honestly)

- **A Chromecast receiver is impossible**: the Google Cast protocol is closed/certified
  on the receiver side, and there's no open implementation to "pretend to be a Chromecast"
  (unlike AirPlay). The Cast button in Android apps won't see spotineck. For Android:
  Spotify Connect, AirPlay (from a Mac), or DLNA/UPnP (planned).
- **TV-Chromecast with a large buffer** (often >2 s) is hard to align perfectly: OwnTone
  caps the offset at ±2000 ms. Small, stable discrepancies (AirPlay↔Cast, ~100–300 ms)
  the delay slider removes nicely.
- **Echo/Alexa** can't be added to the synchronized group (no AirPlay/Cast/stream receiver).

## Layout

```
owntone/      custom OwnTone 29.2 image (Dockerfile + config + entrypoint)
librespot/    librespot wrapper (device name from a file)
shairport/    custom Shairport Sync 5.0.4 classic image (Dockerfile + entrypoint)
config/       owntone.conf, shairport-sync.conf (deploy configs)
api/          FastAPI (REST+WS over OwnTone, Spotify Web API) + web UI (static)
docs/         GitHub Pages landing page (deployed via .github/workflows/pages.yml)
docker-compose.yml
```

## Acknowledgements

Built on [OwnTone](https://github.com/owntone/owntone-server),
[librespot](https://github.com/librespot-org/librespot) and
[shairport-sync](https://github.com/mikebrady/shairport-sync).

## License

MIT (spotineck code). Components are under their own licenses (OwnTone — GPL-3.0).
