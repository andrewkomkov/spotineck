"""
spotineck-api — чистый agent-friendly слой поверх OwnTone.

OwnTone отдаёт богатый, но низкоуровневый REST+WS API. Этот сервис превращает его в
предсказуемый набор эндпоинтов с единым снимком состояния, OpenAPI-схемой (/docs,
/openapi.json) и WebSocket'ом для реального времени. Плюс отдаёт веб-интерфейс.

Фишка spotineck — управление ГРУППОЙ колонок (HomePod / Google Home / любые AirPlay+Cast)
как одной синхронной зоной, поверх единственного Spotify Connect эндпоинта.
"""
import asyncio
import json
import mimetypes
import os
from contextlib import asynccontextmanager
from typing import Any

# веб-манифест PWA должен отдаваться с правильным MIME, иначе Chrome на Android
# не предложит установку. StaticFiles берёт тип из mimetypes — доучиваем его.
mimetypes.add_type("application/manifest+json", ".webmanifest")

import httpx
import websockets
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import spotify

OWNTONE_URL = os.environ.get("OWNTONE_URL", "http://127.0.0.1:3689")
DEVICE_NAME_FILE = os.environ.get("DEVICE_NAME_FILE", "/cfg/device_name")
LIBRESPOT_CONTAINER = os.environ.get("LIBRESPOT_CONTAINER", "spotineck-librespot")
PORT = int(os.environ.get("SPOTINECK_PORT", "8080"))
# websocket_port OwnTone (см. owntone.conf). По умолчанию 3688.
OWNTONE_WS = os.environ.get("OWNTONE_WS", "ws://127.0.0.1:3688")

http: httpx.AsyncClient = None  # инициализируется в lifespan
# offset_ms по колонкам: OwnTone GET /api/outputs не всегда возвращает его обратно,
# поэтому помним последнее заданное значение, чтобы слайдер в UI не сбрасывался.
_offsets: dict[str, int] = {}


# ───────────────────────── модели ответов (для OpenAPI / агентов) ─────────────────────────
class Track(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    length_ms: int = 0
    artwork_url: str | None = Field(None, description="Относительный URL обложки на этом API")
    uri: str | None = None


class Playback(BaseModel):
    state: str = Field("stop", description="play | pause | stop")
    volume: int = 0
    progress_ms: int = 0
    length_ms: int = 0
    shuffle: bool = False
    repeat: str = Field("off", description="off | all | single")
    track: Track = Track()


class Speaker(BaseModel):
    id: str
    name: str
    type: str = Field("", description="AirPlay 1/2, Chromecast, ALSA …")
    selected: bool = Field(False, description="входит ли в синхронную группу прямо сейчас")
    volume: int = 0
    offset_ms: int = Field(0, description="компенсация задержки, -2000..2000 (минус = играть раньше)")
    has_password: bool = False
    needs_auth: bool = False


class State(BaseModel):
    playback: Playback
    speakers: list[Speaker]
    queue_count: int = 0
    server_ts: float = Field(0, description="время сервера в мс — для интерполяции прогресса на клиенте")


# ───────────────────────── общение с OwnTone ─────────────────────────
async def ot_get(path: str, **params) -> dict:
    r = await http.get(path, params=params or None)
    r.raise_for_status()
    if r.content:
        return r.json()
    return {}


async def ot_put(path: str, json_body: dict | None = None, **params) -> None:
    r = await http.put(path, params=params or None, json=json_body)
    r.raise_for_status()


async def build_state() -> State:
    player, queue, outputs = await asyncio.gather(
        ot_get("/api/player"),
        ot_get("/api/queue"),
        ot_get("/api/outputs"),
    )
    items = queue.get("items", [])
    now = next((it for it in items if it.get("id") == player.get("item_id")), None)
    track = Track()
    # Spotify Connect (librespot) пишет pipe без метаданных — у OwnTone трек "spotify".
    # Только для него подмешиваем богатые метаданные из Spotify Web API.
    # AirPlay (shairport) шлёт свои метаданные — их OwnTone уже отдаёт, не трогаем.
    is_spotify = bool(now and now.get("title") == "spotify")
    sp_np = await spotify.get_now_playing() if is_spotify else None
    if sp_np and sp_np.get("title"):
        track = Track(
            title=sp_np.get("title"),
            artist=sp_np.get("artist"),
            album=sp_np.get("album"),
            length_ms=sp_np.get("length_ms", 0),
            artwork_url=sp_np.get("artwork_url"),   # внешний URL обложки Spotify
            uri=sp_np.get("uri"),
        )
    elif now:
        art = now.get("artwork_url")
        track = Track(
            title=now.get("title"),
            artist=now.get("artist"),
            album=now.get("album"),
            length_ms=now.get("length_ms", 0),
            artwork_url="/api/artwork" if art else None,
            uri=now.get("uri"),
        )
    speakers = [
        Speaker(
            id=str(o["id"]),
            name=o.get("name", "?"),
            type=o.get("type", ""),
            selected=o.get("selected", False),
            volume=o.get("volume", 0),
            offset_ms=o.get("offset_ms", _offsets.get(str(o["id"]), 0)),
            has_password=o.get("has_password", False),
            needs_auth=o.get("needs_auth_key", False),
        )
        for o in outputs.get("outputs", [])
    ]
    pb = Playback(
        state=player.get("state", "stop"),
        volume=player.get("volume", 0),
        progress_ms=sp_np["progress_ms"] if sp_np else player.get("item_progress_ms", 0),
        length_ms=sp_np["length_ms"] if sp_np else player.get("item_length_ms", 0),
        shuffle=player.get("shuffle", False),
        repeat=player.get("repeat", "off"),
        track=track,
    )
    return State(
        playback=pb,
        speakers=speakers,
        queue_count=queue.get("count", len(items)),
        server_ts=asyncio.get_event_loop().time() * 1000,
    )


# ───────────────────────── WebSocket: relay реального времени ─────────────────────────
clients: set[WebSocket] = set()


async def broadcast_state() -> None:
    if not clients:
        return
    try:
        state = (await build_state()).model_dump()
    except Exception:
        return
    payload = json.dumps({"type": "state", "data": state})
    dead = []
    for ws in clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


async def owntone_listener() -> None:
    """Подписка на события OwnTone → ретрансляция состояния клиентам."""
    sub = json.dumps({"notify": ["player", "outputs", "queue", "volume", "options"]})
    while True:
        try:
            async with websockets.connect(OWNTONE_WS, subprotocols=["notify"]) as ws:
                await ws.send(sub)
                await broadcast_state()
                async for _ in ws:
                    await broadcast_state()
        except Exception:
            await asyncio.sleep(2)  # OwnTone не готов / реконнект


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http
    http = httpx.AsyncClient(base_url=OWNTONE_URL, timeout=10)
    task = asyncio.create_task(owntone_listener())
    yield
    task.cancel()
    await http.aclose()


app = FastAPI(
    title="spotineck API",
    version="1.0.0",
    description=(
        "Управление синхронной мультирум-зоной поверх Spotify Connect.\n\n"
        "Один эндпоинт `spotineck` в Spotify раздаёт звук на группу колонок "
        "(HomePod через AirPlay 2, Google Home через Chromecast и любые другие). "
        "Этот API даёт чистое управление воспроизведением, громкостью и — главное — "
        "составом синхронной группы колонок.\n\n"
        "Для агентов: см. `GET /api/capabilities`, полная схема в `/openapi.json`."
    ),
    lifespan=lifespan,
)

# Spotify Web API слой (OAuth + transfer + поиск по каталогу)
app.include_router(spotify.router)


# ───────────────────────── состояние ─────────────────────────
@app.get("/api/state", response_model=State, tags=["state"], summary="Полный снимок состояния")
async def get_state():
    return await build_state()


@app.get("/api/capabilities", tags=["state"], summary="Краткое описание возможностей для агентов")
async def capabilities():
    return {
        "name": "spotineck",
        "summary": "Синхронная мультирум-зона поверх одного Spotify Connect эндпоинта.",
        "concepts": {
            "speaker_group": "Колонки с selected=true играют синхронно одну зону. "
            "Меняй состав через PUT /api/speakers/group или POST /api/speakers/{id}.",
            "playback": "Источник — Spotify Connect (выбери 'spotineck' в приложении Spotify) "
            "или локальная очередь OwnTone. Транспорт управляется через /api/playback/*.",
        },
        "key_endpoints": {
            "snapshot": "GET /api/state",
            "play_pause": "POST /api/playback/toggle",
            "next_prev": "POST /api/playback/next | /api/playback/previous",
            "master_volume": "POST /api/playback/volume {volume:0-100}",
            "list_speakers": "GET /api/speakers",
            "toggle_speaker": "POST /api/speakers/{id} {selected:bool, volume?:0-100}",
            "set_group": "PUT /api/speakers/group {ids:[...]}",
            "search": "GET /api/search?q=...",
            "device_name": "GET/POST /api/device-name {name} — имя в Spotify Connect",
            "realtime": "WS /ws — пуш состояния при любом изменении",
        },
        "openapi": "/openapi.json",
    }


# ───────────────────────── воспроизведение ─────────────────────────
class VolumeBody(BaseModel):
    volume: int = Field(..., ge=0, le=100)


class SeekBody(BaseModel):
    position_ms: int = Field(..., ge=0)


class ToggleBody(BaseModel):
    enabled: bool


class RepeatBody(BaseModel):
    mode: str = Field("off", pattern="^(off|all|single)$")


@app.post("/api/playback/play", tags=["playback"], summary="Старт/возобновление")
async def play():
    await ot_put("/api/player/play")
    return {"ok": True}


@app.post("/api/playback/pause", tags=["playback"], summary="Пауза")
async def pause():
    await ot_put("/api/player/pause")
    return {"ok": True}


@app.post("/api/playback/toggle", tags=["playback"], summary="Play/Pause переключатель")
async def toggle():
    player = await ot_get("/api/player")
    if player.get("state") == "play":
        await ot_put("/api/player/pause")
    else:
        await ot_put("/api/player/play")
    return {"ok": True}


@app.post("/api/playback/next", tags=["playback"], summary="Следующий трек")
async def next_track():
    await ot_put("/api/player/next")
    return {"ok": True}


@app.post("/api/playback/previous", tags=["playback"], summary="Предыдущий трек")
async def prev_track():
    await ot_put("/api/player/previous")
    return {"ok": True}


@app.post("/api/playback/seek", tags=["playback"], summary="Перемотка к позиции")
async def seek(body: SeekBody):
    await ot_put("/api/player/seek", position_ms=body.position_ms)
    return {"ok": True}


@app.post("/api/playback/volume", tags=["playback"], summary="Общая громкость зоны (0-100)")
async def volume(body: VolumeBody):
    await ot_put("/api/player/volume", volume=body.volume)
    return {"ok": True}


@app.post("/api/playback/shuffle", tags=["playback"], summary="Перемешивание вкл/выкл")
async def shuffle(body: ToggleBody):
    await ot_put("/api/player/shuffle", state=str(body.enabled).lower())
    return {"ok": True}


@app.post("/api/playback/repeat", tags=["playback"], summary="Повтор: off|all|single")
async def repeat(body: RepeatBody):
    await ot_put("/api/player/repeat", state=body.mode)
    return {"ok": True}


# ───────────────────────── колонки / группа ─────────────────────────
class SpeakerBody(BaseModel):
    selected: bool | None = Field(None, description="включить/выключить из синхронной группы")
    volume: int | None = Field(None, ge=0, le=100)
    offset_ms: int | None = Field(None, ge=-2000, le=2000,
                                  description="компенсация задержки (минус = играть раньше, для ТВ)")


class GroupBody(BaseModel):
    ids: list[str] = Field(..., description="итоговый состав синхронной группы")


@app.get("/api/speakers", response_model=list[Speaker], tags=["speakers"], summary="Все колонки")
async def speakers():
    return (await build_state()).speakers


@app.post("/api/speakers/{speaker_id}", tags=["speakers"], summary="Тумблер/громкость колонки")
async def set_speaker(speaker_id: str, body: SpeakerBody):
    payload: dict[str, Any] = {}
    if body.selected is not None:
        payload["selected"] = body.selected
    if body.volume is not None:
        payload["volume"] = body.volume
    if body.offset_ms is not None:
        payload["offset_ms"] = body.offset_ms
        _offsets[speaker_id] = body.offset_ms
    await ot_put(f"/api/outputs/{speaker_id}", json_body=payload)

    # Chromecast/AirPlay применяют offset только при (пере)старте сессии вывода.
    # Если крутим задержку у уже играющего выхода — быстро пере-инициализируем его,
    # иначе на слух ничего не меняется.
    if body.offset_ms is not None and body.selected is None:
        out = await ot_get(f"/api/outputs/{speaker_id}")
        if out.get("selected"):
            await ot_put(f"/api/outputs/{speaker_id}", json_body={"selected": False})
            await asyncio.sleep(0.25)
            await ot_put(f"/api/outputs/{speaker_id}",
                         json_body={"selected": True, "offset_ms": body.offset_ms})
    return {"ok": True}


@app.put("/api/speakers/group", tags=["speakers"], summary="Задать весь состав группы разом")
async def set_group(body: GroupBody):
    await ot_put("/api/outputs/set", json_body={"outputs": body.ids})
    return {"ok": True}


class PinBody(BaseModel):
    pin: str = Field(..., description="4-значный PIN, который колонка показывает при подключении")


@app.post("/api/speakers/{speaker_id}/verify", tags=["speakers"],
          summary="Подтвердить AirPlay-колонку PIN-кодом (для needs_auth=true)")
async def verify_speaker(speaker_id: str, body: PinBody):
    r = await http.post(f"/api/outputs/{speaker_id}/verification", json={"pin": body.pin})
    if r.status_code >= 400:
        return JSONResponse({"ok": False, "error": r.text}, status_code=r.status_code)
    return {"ok": True}


# ───────────────────────── имя устройства ─────────────────────────
class DeviceNameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=32,
                      description="имя устройства в Spotify Connect")


def _read_device_name() -> str:
    try:
        return open(DEVICE_NAME_FILE).read().strip() or "spotineck"
    except Exception:
        return "spotineck"


@app.get("/api/device-name", tags=["settings"], summary="Текущее имя устройства в Spotify")
async def get_device_name():
    return {"name": _read_device_name()}


@app.post("/api/device-name", tags=["settings"],
          summary="Сменить имя устройства в Spotify (перезапускает librespot)")
async def set_device_name(body: DeviceNameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "пустое имя")
    try:
        with open(DEVICE_NAME_FILE, "w") as f:
            f.write(name)
    except Exception as e:
        raise HTTPException(500, f"не удалось сохранить имя: {e}")
    # перезапуск librespot, чтобы новое имя применилось (~3 сек недоступности Connect)
    try:
        import docker
        docker.from_env().containers.get(LIBRESPOT_CONTAINER).restart()
    except Exception as e:
        return JSONResponse(
            {"ok": False, "name": name,
             "error": f"имя сохранено, но рестарт librespot не удался: {e}"},
            status_code=500,
        )
    return {"ok": True, "name": name}


# ───────────────────────── очередь / поиск / обложка ─────────────────────────
@app.get("/api/queue", tags=["queue"], summary="Текущая очередь")
async def queue():
    return await ot_get("/api/queue")


@app.post("/api/queue/clear", tags=["queue"], summary="Очистить очередь")
async def queue_clear():
    await ot_put("/api/queue/clear")
    return {"ok": True}


@app.get("/api/search", tags=["library"], summary="Поиск по библиотеке (треки/альбомы/артисты/плейлисты)")
async def search(q: str, types: str = "tracks,artists,albums,playlists"):
    # OwnTone ищет по локальной библиотеке и Spotify (если выполнен вход в OwnTone)
    return await ot_get("/api/search", type=types, query=q, media_kind="music")


@app.get("/api/artwork", tags=["state"], summary="Обложка текущего трека (картинка)")
async def artwork():
    try:
        player = await ot_get("/api/player")
        queue = await ot_get("/api/queue")
        now = next((it for it in queue.get("items", []) if it.get("id") == player.get("item_id")), None)
        url = now.get("artwork_url") if now else None
        if not url:
            return Response(status_code=404)
        r = await http.get(url)
        return Response(content=r.content, media_type=r.headers.get("content-type", "image/png"))
    except Exception:
        return Response(status_code=404)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "state", "data": (await build_state()).model_dump()}))
        while True:
            await ws.receive_text()  # клиент может слать ping; нам важно держать соединение
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    try:
        await ot_get("/api/config")
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


# ───────────────────────── web (SPA) ─────────────────────────
# монтируется последним, чтобы не перехватывать /api/*
app.mount("/", StaticFiles(directory="app/static", html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
