"""
Spotify Web API layer for spotineck.

Lets our UI/agent control Spotify directly:
  * start music on the spotineck device (transfer playback) — without touching the phone;
  * read rich now-playing metadata (artwork, track, artist);
  * search the full Spotify catalog;
  * start a specific track/playlist/album by URI.

OAuth Authorization Code. Tokens live in /data/spotify_token.json and refresh themselves.
Credentials come from the environment (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET), see .env.
"""
import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/api/spotify/callback")
DEVICE_NAME = os.environ.get("SPOTINECK_DEVICE_NAME", "spotineck")
TOKEN_PATH = Path(os.environ.get("SPOTIFY_TOKEN_PATH", "/data/spotify_token.json"))

SCOPES = " ".join([
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "user-read-private",
    "playlist-read-private",
    "user-library-read",
])

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

router = APIRouter(prefix="/api/spotify", tags=["spotify"])
_token: dict | None = None
_np_cache: dict = {"at": 0.0, "val": None}   # short now-playing cache (TTL below)
_NP_TTL = 2.0


# ───────────────────────── tokens ─────────────────────────
def _load() -> dict | None:
    global _token
    if _token is None and TOKEN_PATH.exists():
        try:
            _token = json.loads(TOKEN_PATH.read_text())
        except Exception:
            _token = None
    return _token


def _save(tok: dict) -> None:
    global _token
    _token = tok
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(tok))


def _basic_auth() -> str:
    return base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()


async def _refresh(tok: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {_basic_auth()}"},
            data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
        )
        r.raise_for_status()
        new = r.json()
    tok["access_token"] = new["access_token"]
    tok["expires_at"] = time.time() + new.get("expires_in", 3600)
    if new.get("refresh_token"):
        tok["refresh_token"] = new["refresh_token"]
    _save(tok)
    return tok


async def _access_token() -> str:
    tok = _load()
    if not tok:
        raise HTTPException(401, "Spotify not authorized. Open GET /api/spotify/login")
    if tok.get("expires_at", 0) < time.time() + 60:
        tok = await _refresh(tok)
    return tok["access_token"]


async def _sp(method: str, path: str, **kw) -> httpx.Response:
    token = await _access_token()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.request(method, API + path, headers={"Authorization": f"Bearer {token}"}, **kw)
    if r.status_code == 401:  # token expired between checks — refresh and retry
        await _refresh(_load())
        token = await _access_token()
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.request(method, API + path, headers={"Authorization": f"Bearer {token}"}, **kw)
    return r


# ───────────────────────── OAuth ─────────────────────────
@router.get("/status", summary="Spotify integration status")
async def status():
    return {
        "configured": bool(CLIENT_ID and CLIENT_SECRET),
        "authorized": _load() is not None,
        "device_name": DEVICE_NAME,
        "redirect_uri": REDIRECT_URI,
    }


@router.get("/login", summary="Start OAuth (open in a browser)")
async def login():
    if not CLIENT_ID:
        raise HTTPException(500, "SPOTIFY_CLIENT_ID is not set (see ~/spotineck/.env)")
    q = urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    })
    return RedirectResponse(f"{AUTH_URL}?{q}")


@router.get("/callback", summary="OAuth callback (Spotify redirects here)")
async def callback(code: str = Query(...)):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {_basic_auth()}"},
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        )
    if r.status_code != 200:
        raise HTTPException(400, f"Code exchange failed: {r.text}")
    tok = r.json()
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600)
    _save(tok)
    return RedirectResponse("/?spotify=ok")


# ───────────────────────── control ─────────────────────────
async def _find_device() -> dict | None:
    r = await _sp("GET", "/me/player/devices")
    if r.status_code != 200:
        return None
    for d in r.json().get("devices", []):
        if d.get("name") == DEVICE_NAME:
            return d
    return None


@router.get("/devices", summary="Visible Spotify Connect devices")
async def devices():
    r = await _sp("GET", "/me/player/devices")
    return r.json() if r.status_code == 200 else {"devices": []}


@router.post("/play-here", summary="Transfer playback to spotineck (play on the group)")
async def play_here():
    dev = await _find_device()
    if not dev:
        raise HTTPException(
            404,
            f"Device '{DEVICE_NAME}' is not visible in Spotify. Make sure the owntone container "
            f"is running and pick 'spotineck' in the Spotify app at least once so it shows up.",
        )
    r = await _sp("PUT", "/me/player", json={"device_ids": [dev["id"]], "play": True})
    if r.status_code not in (200, 202, 204):
        raise HTTPException(r.status_code, r.text)
    return {"ok": True, "device": dev["name"]}


class PlayBody(BaseModel):
    uri: str | None = None        # spotify:track:... / spotify:album:... / spotify:playlist:...


@router.post("/play", summary="Start a track/album/playlist by URI on spotineck")
async def play(body: PlayBody):
    dev = await _find_device()
    if not dev:
        raise HTTPException(404, f"Device '{DEVICE_NAME}' is not visible in Spotify.")
    payload: dict = {}
    if body.uri:
        if body.uri.startswith("spotify:track:"):
            payload["uris"] = [body.uri]
        else:
            payload["context_uri"] = body.uri
    r = await _sp("PUT", f"/me/player/play?device_id={dev['id']}", json=payload or None)
    if r.status_code not in (200, 202, 204):
        raise HTTPException(r.status_code, r.text)
    return {"ok": True}


class QueueBody(BaseModel):
    uri: str  # spotify:track:... — what to add to the playback queue


@router.post("/queue", summary="Add a track to the Spotify queue (plays on spotineck)")
async def queue(body: QueueBody):
    dev = await _find_device()
    params = {"uri": body.uri}
    if dev:
        params["device_id"] = dev["id"]
    r = await _sp("POST", "/me/player/queue", params=params)
    if r.status_code not in (200, 202, 204):
        raise HTTPException(r.status_code, r.text)
    return {"ok": True}


async def get_now_playing() -> dict | None:
    """Normalized metadata of the current Spotify track, or None if not playing/not authorized.
    Cached for _NP_TTL s to avoid hammering the Spotify API on every WS event."""
    if _load() is None:
        return None
    now = time.monotonic()
    if now - _np_cache["at"] < _NP_TTL:
        return _np_cache["val"]
    try:
        r = await _sp("GET", "/me/player")
    except Exception:
        return _np_cache["val"]
    def _cache(v):
        _np_cache["at"] = time.monotonic()
        _np_cache["val"] = v
        return v

    if r.status_code == 204 or not r.content:
        return _cache(None)
    d = r.json()
    item = d.get("item") or {}
    if not item:
        return _cache(None)
    images = (item.get("album") or {}).get("images") or []
    return _cache({
        "playing": d.get("is_playing", False),
        "device": (d.get("device") or {}).get("name"),
        "title": item.get("name"),
        "artist": ", ".join(a["name"] for a in item.get("artists", [])),
        "album": (item.get("album") or {}).get("name"),
        "artwork_url": images[0]["url"] if images else None,
        "progress_ms": d.get("progress_ms", 0),
        "length_ms": item.get("duration_ms", 0),
        "uri": item.get("uri"),
    })


@router.get("/now-playing", summary="Rich metadata of the current Spotify track")
async def now_playing():
    np = await get_now_playing()
    return np or {"playing": False}


@router.get("/search", summary="Search the full Spotify catalog")
async def search(q: str, type: str = "track,artist,album,playlist", limit: int = 10):
    # a dev-mode Spotify app caps the search limit at 10 (≥12 → "Invalid limit")
    limit = max(1, min(limit, 10))
    r = await _sp("GET", "/search", params={"q": q, "type": type, "limit": limit})
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text)
    return r.json()
