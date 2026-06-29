# spotineck

**Универсальная синхронная мультирум-зона из колонок разных экосистем.**

Spotineck превращает кучку несовместимых колонок — **HomePod** (AirPlay), **Google
Home / Nest** и любые **Chromecast**, плюс локальный звук хоста — в **одну синхронную
зону**, в которую можно лить звук из **Spotify** (Spotify Connect) и с **любого
Apple-устройства** (AirPlay). Всё это с красивым веб-интерфейсом в стиле Spotify и
чистым agent-friendly API.

> Зачем: HomePod, Alexa/Echo, Google Home и Chromecast живут в трёх несовместимых
> протоколах мультирума и **не объединяются** в одну группу штатно. Spotineck — мост:
> один вход (Spotify/AirPlay) → синхронный фанат на все колонки сразу.

```
                            ┌──────────────── nettop (Linux + Docker) ───────────────┐
 Spotify (любое устр-во) ──►│ librespot ─┐                                            │
                            │            ├─► OwnTone ─┬─► AirPlay 2 ─► HomePod         │
 iPhone/iPad/Mac (AirPlay)─►│ shairport ─┘            ├─► Chromecast ─► Google / TV    │
                            │   (pipe-источники)      └─► ALSA ───────► динамики хоста │
 Браузер / Агент ──HTTP/WS─►│ spotineck-api (REST + WebSocket + web UI)               │
                            └────────────────────────────────────────────────────────┘
```

## Возможности

- 🔊 **Синхронная зона** из AirPlay 2 (HomePod) + Chromecast (Google/TV) + локального ALSA.
- 🎚 **Per-speaker громкость и задержка (offset)** прямо в UI — добить рассинхрон между
  протоколами на слух, в рантайме, без перезапуска.
- 🟢 **Spotify Connect** — зона видна как устройство «spotineck» в приложении Spotify.
- ﹫ **AirPlay-приёмник** — spotineck виден как AirPlay-колонка; любой звук с Apple
  (не только Spotify) летит на всю группу, включая Chromecast.
- 🎛 **Spotify Web API** (опц.) — запуск музыки из UI/агента без телефона, богатые
  метаданные (трек/обложка), поиск по каталогу.
- ✏️ **Смена имени устройства** из UI/API.
- 🔐 **AirPlay PIN-авторизация** колонок прямо в интерфейсе.
- 📱 **Веб-интерфейс** в стиле Spotify, mobile-friendly.
- 🤖 **Agent-friendly API**: чистый REST + OpenAPI (`/docs`, `/openapi.json`) + WebSocket.

## Как это устроено

Spotineck не изобретает мультирум-движок — он использует **[OwnTone](https://github.com/owntone/owntone-server)**
(синхронный AirPlay 2 / Chromecast плеер). Каждый «вход» — это маленький сервис,
который пишет PCM в именованный pipe, а OwnTone читает его как источник и синхронно
раздаёт на все включённые выходы:

| Сервис | Что делает |
|---|---|
| **owntone** | Ядро: читает pipe-источники, синхронно фанатит на AirPlay 2 / Chromecast / ALSA. Отдаёт REST+WS API. Свой образ из исходников OwnTone 29.2 (`andrewkomkov/owntone`). |
| **librespot** | Spotify Connect эндпоинт → пишет в `/music/spotify`. Имя устройства берётся из файла (меняется из UI). |
| **shairport** | AirPlay приёмник ([shairport-sync](https://github.com/mikebrady/shairport-sync)) → пишет в `/music/airplay`. |
| **api** | FastAPI: причёсывает OwnTone API в чистый REST/WS, добавляет Spotify Web API, смену имени, отдаёт web UI. |

## Требования

- **Linux-хост** (для macOS/Windows не подойдёт: нужен `network_mode: host` для mDNS
  и Spotify Connect zeroconf).
- **Docker + docker compose**.
- Колонки и хост — в **одной сети/VLAN** (mDNS не ходит между подсетями).
- Для Spotify Connect — **Spotify Premium**.

## Быстрый старт

```bash
git clone https://github.com/andrewkomkov/spotineck.git
cd spotineck
make setup     # создаёт FIFO, config, .env из примера
make up        # собирает и поднимает стек
```

1. Открой `http://<host>:8080` — веб-интерфейс.
2. В приложении **Spotify** выбери устройство **spotineck** и нажми play.
3. В UI на вкладке **Колонки** включи нужные колонки в группу — заиграет синхронно.
4. Звук с Apple: на iPhone/iPad/Mac выбери **spotineck** в меню **AirPlay**.

### Spotify Web API (опционально)

Даёт запуск из UI/агента, метаданные и поиск. Создай app на
[developer.spotify.com](https://developer.spotify.com/dashboard) (Web API, redirect URI
`http://127.0.0.1:8080/api/spotify/callback`), впиши `SPOTIFY_CLIENT_ID/SECRET` в `.env`,
перезапусти api и пройди OAuth: `http://<host>:8080/api/spotify/login` (через
SSH-туннель `ssh -L 8080:127.0.0.1:8080 <host>`, т.к. Spotify требует loopback redirect).

## API (для агентов и интеграций)

Чистый REST со схемой OpenAPI на `/docs` и `/openapi.json`. Краткая сводка — `GET /api/capabilities`.

| Метод | Назначение |
|---|---|
| `GET /api/state` | полный снимок: воспроизведение + колонки + очередь |
| `POST /api/playback/{play,pause,toggle,next,previous}` | транспорт |
| `POST /api/playback/volume` `{volume}` | громкость зоны |
| `GET /api/speakers` · `POST /api/speakers/{id}` `{selected,volume,offset_ms}` | колонки: в группу / громкость / задержка |
| `POST /api/speakers/{id}/verify` `{pin}` | AirPlay PIN |
| `PUT /api/speakers/group` `{ids}` | задать состав группы разом |
| `GET/POST /api/device-name` `{name}` | имя в Spotify Connect |
| `GET /api/search?q=` | поиск |
| `POST /api/spotify/play-here` · `/api/spotify/play` `{uri}` | запуск на spotineck через Spotify Web API |
| `WS /ws` | пуш состояния в реальном времени |

## Опубликованные образы

- `andrewkomkov/owntone:29.2` — OwnTone 29.2 (AirPlay 2 + Chromecast + рантайм-offset), multi-stage из исходников.
- `andrewkomkov/spotineck-librespot:latest` — librespot с именем устройства из файла.

## Ограничения (честно)

- **Chromecast-приёмник невозможен**: протокол Google Cast на стороне приёмника
  закрытый/сертифицируемый, открытой реализации «притвориться Chromecast» нет (в отличие
  от AirPlay). Кнопка Cast в Android-приложениях spotineck не увидит. Для Android: Spotify
  Connect, AirPlay (с Mac), либо DLNA/UPnP (в планах).
- **ТВ-Chromecast с большим буфером** (часто >2 сек) тяжело свести идеально: OwnTone
  ограничивает offset ±2000 мс. Небольшие стабильные расхождения (AirPlay↔Cast, ~100–300 мс)
  слайдер задержки убирает отлично.
- **Echo/Alexa** в синхронную группу не заводится (нет AirPlay/Cast/приёма потока).

## Структура

```
owntone/      свой образ OwnTone 29.2 (Dockerfile + конфиг + entrypoint)
librespot/    обёртка librespot (имя устройства из файла)
config/       owntone.conf, shairport-sync.conf (деплой-конфиги)
api/          FastAPI (REST+WS поверх OwnTone, Spotify Web API) + web UI (static)
docker-compose.yml
```

## Благодарности

Построено на [OwnTone](https://github.com/owntone/owntone-server),
[librespot](https://github.com/librespot-org/librespot) и
[shairport-sync](https://github.com/mikebrady/shairport-sync).

## Лицензия

MIT (код spotineck). Компоненты — под своими лицензиями (OwnTone — GPL-3.0).
