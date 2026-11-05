"""roborock-map: serve HA's Roborock map recoloured in the Kodiak palette.

GET /map.png  -> fetch HA's image entity through /api/image_proxy, recolour +
                 mask it (render.py), return a PNG of the same size.
GET /healthz  -> 200 while the process is up (body says when the last render
                 happened and the last error, if any). Not tied to HA being up,
                 so an HA restart does not flap this container.

HA polls it through a template image entity whose URL changes whenever the
source image entity updates (config/packages/roborock.yaml), so renders follow
the vacuum's map updates. A short cache keeps parallel viewers from re-rendering.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

import requests
from flask import Flask, Response, jsonify

from render import load_config, render_bytes

HA_URL = os.environ.get("HA_URL", "http://host.docker.internal:8123").rstrip("/")
SOURCE_ENTITY = os.environ.get("SOURCE_ENTITY", "image.mr_slave_main_floor_2")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
CONFIG_PATH = os.environ.get("MAP_CONFIG", os.path.join(os.path.dirname(__file__), "config.json"))
CACHE_SECONDS = float(os.environ.get("CACHE_SECONDS", "20"))
TIMEOUT = float(os.environ.get("HA_TIMEOUT", "10"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("roborock-map")

app = Flask(__name__)
CFG = load_config(CONFIG_PATH)
_lock = threading.Lock()
_state: dict = {"src_hash": None, "png": None, "fetched": 0.0, "rendered_at": None, "error": None}


def _fetch_source() -> bytes:
    r = requests.get(
        f"{HA_URL}/api/image_proxy/{SOURCE_ENTITY}",
        headers={"Authorization": f"Bearer {HA_TOKEN}"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    if not r.content.startswith(b"\x89PNG"):
        raise ValueError(f"source is not a PNG (content-type {r.headers.get('content-type')})")
    return r.content


def current_png() -> bytes:
    with _lock:
        now = time.monotonic()
        if _state["png"] is not None and now - _state["fetched"] < CACHE_SECONDS:
            return _state["png"]
        try:
            src = _fetch_source()
        except Exception as exc:  # serve the last good render while HA is away
            # Keep the detail in the log only; /healthz and /map.png show a
            # classification (CodeQL: no exception text to the client).
            _state["error"] = f"fetch failed ({type(exc).__name__})"
            log.warning("source fetch failed: %s", exc)
            if _state["png"] is not None:
                return _state["png"]
            raise
        _state["fetched"] = now
        h = hashlib.sha256(src).hexdigest()
        if h != _state["src_hash"]:
            t0 = time.monotonic()
            _state["png"] = render_bytes(src, CFG)
            _state["src_hash"] = h
            _state["rendered_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            log.info("rendered %d bytes in %.2fs", len(_state["png"]), time.monotonic() - t0)
        _state["error"] = None
        return _state["png"]


@app.get("/map.png")
def map_png() -> Response:
    if not HA_TOKEN:
        return Response("HA_TOKEN not set (roborock-map.env)\n", status=503, mimetype="text/plain")
    try:
        png = current_png()
    except Exception as exc:
        log.warning("map render unavailable: %s", exc)
        return Response("source unavailable\n", status=502, mimetype="text/plain")
    return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz() -> Response:
    return jsonify(ok=True, token_set=bool(HA_TOKEN), rendered_at=_state["rendered_at"], last_error=_state["error"])


if __name__ == "__main__":
    from waitress import serve

    if not HA_TOKEN:
        log.warning("HA_TOKEN is empty: /map.png will return 503 until roborock-map.env is filled")
    serve(app, host=os.environ.get("BIND", "0.0.0.0"), port=int(os.environ.get("PORT", "8125")), threads=4)
