"""
spotineck-api — a clean, agent-friendly layer over OwnTone.

OwnTone exposes a rich but low-level REST+WS API. This service turns it into a
predictable set of endpoints with a single state snapshot, an OpenAPI schema (/docs,
/openapi.json) and a WebSocket for real-time updates. It also serves the web interface.

The point of spotineck is controlling a GROUP of speakers (HomePod / Google Home /
any AirPlay+Cast) as one synchronized zone, on top of a single Spotify Connect endpoint.
"""
import asyncio
import json
import mimetypes
import os
from contextlib import asynccontextmanager
from typing import Any

# the PWA web manifest must be served with the correct MIME type, otherwise Chrome on
# Android won't offer to install. StaticFiles takes the type from mimetypes — teach it.
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
# OwnTone websocket_port (see owntone.conf). Defaults to 3688.
OWNTONE_WS = os.environ.get("OWNTONE_WS", "ws://127.0.0.1:3688")

http: httpx.AsyncClient = None  # initialized in lifespan
# per-speaker offset_ms: OwnTone GET /api/outputs doesn't always return it back, so we
# remember the last value we set, so the slider in the UI doesn't reset.
_offsets: dict[str, int] = {}


# ───────────────────────── response models (for OpenAPI / agents) ─────────────────────────
class Track(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    length_ms: int = 0
    artwork_url: str | None = Field(None, description="Relative URL of the artwork on this API")
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
    selected: bool = Field(False, description="whether it's in the synchronized group right now")
    volume: int = 0
    offset_ms: int = Field(0, description="delay compensation, -2000..2000 (negative = play earlier)")
    has_password: bool = False
    needs_auth: bool = False


class State(BaseModel):
    playback: Playback
    speakers: list[Speaker]
    queue_count: int = 0
    server_ts: float = Field(0, description="server time in ms — for interpolating progress on the client")


# ───────────────────────── talking to OwnTone ─────────────────────────
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
    # Spotify Connect (librespot) writes the pipe without metadata — OwnTone's track is "spotify".
    # Only for that case do we mix in rich metadata from the Spotify Web API.
    # AirPlay (shairport) sends its own metadata — OwnTone already exposes it, leave it alone.
    is_spotify = bool(now and now.get("title") == "spotify")
    sp_np = await spotify.get_now_playing() if is_spotify else None
    if sp_np and sp_np.get("title"):
        track = Track(
            title=sp_np.get("title"),
            artist=sp_np.get("artist"),
            album=sp_np.get("album"),
            length_ms=sp_np.get("length_ms", 0),
            artwork_url=sp_np.get("artwork_url"),   # external Spotify artwork URL
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


# ───────────────────────── WebSocket: real-time relay ─────────────────────────
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
    """Subscribe to OwnTone events → relay state to clients."""
    sub = json.dumps({"notify": ["player", "outputs", "queue", "volume", "options"]})
    while True:
        try:
            async with websockets.connect(OWNTONE_WS, subprotocols=["notify"]) as ws:
                await ws.send(sub)
                await broadcast_state()
                async for _ in ws:
                    await broadcast_state()
        except Exception:
            await asyncio.sleep(2)  # OwnTone not ready / reconnect


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
        "Control a synchronized multi-room zone on top of Spotify Connect.\n\n"
        "A single `spotineck` endpoint in Spotify fans audio out to a group of speakers "
        "(HomePod via AirPlay 2, Google Home via Chromecast and any others). "
        "This API gives clean control over playback, volume and — most importantly — "
        "the membership of the synchronized speaker group.\n\n"
        "For agents: see `GET /api/capabilities`, the full schema is in `/openapi.json`."
    ),
    lifespan=lifespan,
)

# Spotify Web API layer (OAuth + transfer + catalog search)
app.include_router(spotify.router)


# ───────────────────────── state ─────────────────────────
@app.get("/api/state", response_model=State, tags=["state"], summary="Full state snapshot")
async def get_state():
    return await build_state()


@app.get("/api/capabilities", tags=["state"], summary="Short capability description for agents")
async def capabilities():
    return {
        "name": "spotineck",
        "summary": "A synchronized multi-room zone on top of a single Spotify Connect endpoint.",
        "concepts": {
            "speaker_group": "Speakers with selected=true play one zone in sync. "
            "Change membership via PUT /api/speakers/group or POST /api/speakers/{id}.",
            "playback": "The source is Spotify Connect (pick 'spotineck' in the Spotify app) "
            "or OwnTone's local queue. Transport is controlled via /api/playback/*.",
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
            "device_name": "GET/POST /api/device-name {name} — name in Spotify Connect",
            "realtime": "WS /ws — state push on every change",
        },
        "openapi": "/openapi.json",
    }


# ───────────────────────── playback ─────────────────────────
class VolumeBody(BaseModel):
    volume: int = Field(..., ge=0, le=100)


class SeekBody(BaseModel):
    position_ms: int = Field(..., ge=0)


class ToggleBody(BaseModel):
    enabled: bool


class RepeatBody(BaseModel):
    mode: str = Field("off", pattern="^(off|all|single)$")


@app.post("/api/playback/play", tags=["playback"], summary="Start/resume")
async def play():
    await ot_put("/api/player/play")
    return {"ok": True}


@app.post("/api/playback/pause", tags=["playback"], summary="Pause")
async def pause():
    await ot_put("/api/player/pause")
    return {"ok": True}


@app.post("/api/playback/toggle", tags=["playback"], summary="Play/Pause toggle")
async def toggle():
    player = await ot_get("/api/player")
    if player.get("state") == "play":
        await ot_put("/api/player/pause")
    else:
        await ot_put("/api/player/play")
    return {"ok": True}


@app.post("/api/playback/next", tags=["playback"], summary="Next track")
async def next_track():
    await ot_put("/api/player/next")
    return {"ok": True}


@app.post("/api/playback/previous", tags=["playback"], summary="Previous track")
async def prev_track():
    await ot_put("/api/player/previous")
    return {"ok": True}


@app.post("/api/playback/seek", tags=["playback"], summary="Seek to position")
async def seek(body: SeekBody):
    await ot_put("/api/player/seek", position_ms=body.position_ms)
    return {"ok": True}


@app.post("/api/playback/volume", tags=["playback"], summary="Zone master volume (0-100)")
async def volume(body: VolumeBody):
    await ot_put("/api/player/volume", volume=body.volume)
    return {"ok": True}


@app.post("/api/playback/shuffle", tags=["playback"], summary="Shuffle on/off")
async def shuffle(body: ToggleBody):
    await ot_put("/api/player/shuffle", state=str(body.enabled).lower())
    return {"ok": True}


@app.post("/api/playback/repeat", tags=["playback"], summary="Repeat: off|all|single")
async def repeat(body: RepeatBody):
    await ot_put("/api/player/repeat", state=body.mode)
    return {"ok": True}


# ───────────────────────── speakers / group ─────────────────────────
class SpeakerBody(BaseModel):
    selected: bool | None = Field(None, description="add/remove from the synchronized group")
    volume: int | None = Field(None, ge=0, le=100)
    offset_ms: int | None = Field(None, ge=-2000, le=2000,
                                  description="delay compensation (negative = play earlier, for TVs)")


class GroupBody(BaseModel):
    ids: list[str] = Field(..., description="the resulting synchronized group membership")


@app.get("/api/speakers", response_model=list[Speaker], tags=["speakers"], summary="All speakers")
async def speakers():
    return (await build_state()).speakers


@app.post("/api/speakers/{speaker_id}", tags=["speakers"], summary="Speaker toggle/volume")
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

    # Chromecast/AirPlay apply the offset only on (re)start of the output session.
    # If we change the delay on an already-playing output — quickly re-initialize it,
    # otherwise nothing changes audibly.
    if body.offset_ms is not None and body.selected is None:
        out = await ot_get(f"/api/outputs/{speaker_id}")
        if out.get("selected"):
            await ot_put(f"/api/outputs/{speaker_id}", json_body={"selected": False})
            await asyncio.sleep(0.25)
            await ot_put(f"/api/outputs/{speaker_id}",
                         json_body={"selected": True, "offset_ms": body.offset_ms})
    return {"ok": True}


@app.put("/api/speakers/group", tags=["speakers"], summary="Set the whole group membership at once")
async def set_group(body: GroupBody):
    await ot_put("/api/outputs/set", json_body={"outputs": body.ids})
    return {"ok": True}


class PinBody(BaseModel):
    pin: str = Field(..., description="the 4-digit PIN the speaker shows when connecting")


@app.post("/api/speakers/{speaker_id}/verify", tags=["speakers"],
          summary="Verify an AirPlay speaker with a PIN (for needs_auth=true)")
async def verify_speaker(speaker_id: str, body: PinBody):
    r = await http.post(f"/api/outputs/{speaker_id}/verification", json={"pin": body.pin})
    if r.status_code >= 400:
        return JSONResponse({"ok": False, "error": r.text}, status_code=r.status_code)
    return {"ok": True}


# ───────────────────────── device name ─────────────────────────
class DeviceNameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=32,
                      description="device name in Spotify Connect")


def _read_device_name() -> str:
    try:
        return open(DEVICE_NAME_FILE).read().strip() or "spotineck"
    except Exception:
        return "spotineck"


@app.get("/api/device-name", tags=["settings"], summary="Current device name in Spotify")
async def get_device_name():
    return {"name": _read_device_name()}


@app.post("/api/device-name", tags=["settings"],
          summary="Change the device name in Spotify (restarts librespot)")
async def set_device_name(body: DeviceNameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "empty name")
    try:
        with open(DEVICE_NAME_FILE, "w") as f:
            f.write(name)
    except Exception as e:
        raise HTTPException(500, f"failed to save the name: {e}")
    # restart librespot so the new name takes effect (~3 s of Connect downtime)
    try:
        import docker
        docker.from_env().containers.get(LIBRESPOT_CONTAINER).restart()
    except Exception as e:
        return JSONResponse(
            {"ok": False, "name": name,
             "error": f"name saved, but restarting librespot failed: {e}"},
            status_code=500,
        )
    return {"ok": True, "name": name}


# ───────────────────────── queue / search / artwork ─────────────────────────
@app.get("/api/queue", tags=["queue"], summary="Current queue")
async def queue():
    return await ot_get("/api/queue")


@app.post("/api/queue/clear", tags=["queue"], summary="Clear the queue")
async def queue_clear():
    await ot_put("/api/queue/clear")
    return {"ok": True}


@app.get("/api/search", tags=["library"], summary="Search the library (tracks/albums/artists/playlists)")
async def search(q: str, types: str = "tracks,artists,albums,playlists"):
    # OwnTone searches the local library and Spotify (if logged in within OwnTone)
    return await ot_get("/api/search", type=types, query=q, media_kind="music")


@app.get("/api/artwork", tags=["state"], summary="Current track artwork (image)")
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
            await ws.receive_text()  # the client may send ping; we just keep the connection alive
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
# mounted last so it doesn't intercept /api/*
app.mount("/", StaticFiles(directory="app/static", html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
