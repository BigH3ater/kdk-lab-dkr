"""Kitchen device pairing for kitchen.kmkdp.com (no Authelia on that host).

A paired device holds one cookie, `kitchen_device`, with a signed token:

    k1.<key id>.<device id>.<issued>.<expires>.<HMAC-SHA256, base64url>

The HMAC key is KITCHEN_DEVICE_KEY (1Password, op-injected). The key id is a
hash of the key, so rotating the key changes it: every old token stops
verifying, and the registry marks every device revoked on the first start with
the new key. A good signature is not enough on its own: the device must be in
the registry (a JSON file on the /state bind mount), not revoked, and the
token's issued time must match the record.

Pairing codes are one-time: 128 random bits (base32, 26 characters), valid 10
minutes, stored only as a SHA-256 hash, removed when used. Nothing here logs a
code or a token.

Pure Python over plain data, so the tests drive it without Flask.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from collections import deque

from markupsafe import escape

log = logging.getLogger("mealplan")

COOKIE = "kitchen_device"
TOKEN_TTL = 365 * 86400          # one year
CODE_TTL = 600                   # pairing code: 10 minutes
MAX_CODES = 5                    # open codes kept; older ones are dropped
SEEN_WRITE_S = 300               # last_seen reaches the file at most every 5 min
MIN_KEY_BYTES = 32               # a shorter KITCHEN_DEVICE_KEY counts as missing
NAME_MAX = 40
TOKEN_MAX = 512
CODE_LEN = 26                    # base32 of 16 bytes, no padding
_CODE_RE = re.compile(r"[^A-Z2-7]")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def key_id(key: bytes) -> str:
    return hashlib.sha256(b"kitchen-device-kid:" + key).hexdigest()[:8]


def key_ok(key: str | None) -> bool:
    return bool(key) and len(key.encode()) >= MIN_KEY_BYTES


class Signer:
    def __init__(self, key: str):
        self._key = key.encode()
        self.kid = key_id(self._key)

    def _sig(self, payload: str) -> str:
        return _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())

    def issue(self, device_id: str, issued: int, expires: int) -> str:
        payload = f"k1.{self.kid}.{device_id}.{int(issued)}.{int(expires)}"
        return f"{payload}.{self._sig(payload)}"

    def verify(self, token: str | None, now: float) -> tuple[dict | None, str]:
        """(claims, "") when the token is genuine and in date, else (None, reason)."""
        if not token:
            return None, "no cookie"
        if len(token) > TOKEN_MAX:
            return None, "malformed"
        parts = token.split(".")
        if len(parts) != 6 or parts[0] != "k1":
            return None, "malformed"
        payload, sig = ".".join(parts[:5]), parts[5]
        if not hmac.compare_digest(self._sig(payload).encode(), sig.encode()):
            return None, "bad signature"
        _v, kid, did, iat, exp = parts[:5]
        if kid != self.kid:                    # cannot happen with a good signature; belt and braces
            return None, "old key"
        try:
            iat_i, exp_i = int(iat), int(exp)
        except ValueError:
            return None, "malformed"
        if exp_i <= now:
            return None, "expired"
        if iat_i > now + 300:
            return None, "issued in the future"
        return {"id": did, "issued": iat_i, "expires": exp_i}, ""


def normalize_code(raw: str | None) -> str:
    """Upper-case base32 letters only (spaces and dashes from the grouped
    display are dropped); "" unless it is exactly CODE_LEN long."""
    c = _CODE_RE.sub("", (raw or "").upper())[:64]
    return c if len(c) == CODE_LEN else ""


def group_code(code: str) -> str:
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def clean_name(raw: str | None) -> str:
    name = " ".join((raw or "").split())[:NAME_MAX].strip()
    return name or "Kitchen iPad"


class Registry:
    """Devices and open pairing codes, one JSON file, written atomically
    (temp file in the same directory, fsync, rename). One process owns it, so
    the in-memory copy is the truth and every change is written through."""

    def __init__(self, path: str, kid: str):
        self.path = path
        self._lock = threading.Lock()
        self._seen_written: dict[str, int] = {}
        self._data = self._load()
        if self._data.get("kid") != kid:
            n = 0
            now = int(time.time())
            for d in self._data["devices"]:
                if not d.get("revoked"):
                    d["revoked"], d["revoked_by"] = now, "key rotation"
                    n += 1
            self._data["codes"] = []
            if self._data.get("kid") and n:
                log.warning("kitchen: device key changed, %d device(s) revoked", n)
            self._data["kid"] = kid
            self._save()

    # ---- file ----
    def _load(self) -> dict:
        try:
            with open(self.path) as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            log.exception("kitchen: device registry unreadable, starting empty")
            data = {}
        data.setdefault("kid", "")
        data["devices"] = [d for d in data.get("devices") or [] if isinstance(d, dict) and d.get("id")]
        data["codes"] = [c for c in data.get("codes") or [] if isinstance(c, dict) and c.get("hash")]
        return data

    def _save(self) -> None:
        d = os.path.dirname(self.path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".devices-", dir=d)
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._data, fh, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- pairing codes ----
    def _prune_codes(self, now: float) -> None:
        self._data["codes"] = [c for c in self._data["codes"] if c.get("expires", 0) > now]

    def new_code(self, created_by: str, now: float) -> str:
        code = base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=")
        with self._lock:
            self._prune_codes(now)
            self._data["codes"].append({"hash": _hash_code(code), "expires": int(now) + CODE_TTL,
                                        "created_by": created_by})
            self._data["codes"] = self._data["codes"][-MAX_CODES:]
            self._save()
        return code

    def consume_code(self, code: str, now: float) -> bool:
        """True once for a valid code; it is gone afterwards."""
        if not code:
            return False
        h = _hash_code(code)
        with self._lock:
            self._prune_codes(now)
            hit = [c for c in self._data["codes"] if hmac.compare_digest(c["hash"], h)]
            if not hit:
                return False
            self._data["codes"] = [c for c in self._data["codes"] if c not in hit]
            self._save()
            return True

    # ---- devices ----
    def add_device(self, name: str, now: float) -> dict:
        dev = {"id": secrets.token_urlsafe(12), "name": clean_name(name), "issued": int(now),
               "expires": int(now) + TOKEN_TTL, "last_seen": int(now), "revoked": None}
        with self._lock:
            self._data["devices"].append(dev)
            self._save()
        return dict(dev)

    def active(self, claims: dict) -> dict | None:
        with self._lock:
            for d in self._data["devices"]:
                if d["id"] == claims["id"]:
                    if d.get("revoked") or d.get("issued") != claims["issued"]:
                        return None
                    return dict(d)
        return None

    def touch(self, did: str, now: float) -> None:
        with self._lock:
            for d in self._data["devices"]:
                if d["id"] == did:
                    d["last_seen"] = int(now)
                    if now - self._seen_written.get(did, 0) >= SEEN_WRITE_S:
                        self._seen_written[did] = int(now)
                        self._save()
                    return

    def revoke(self, did: str, by: str, now: float) -> dict | None:
        with self._lock:
            for d in self._data["devices"]:
                if d["id"] == did and not d.get("revoked"):
                    d["revoked"], d["revoked_by"] = int(now), by
                    self._save()
                    return dict(d)
        return None

    def revoke_all(self, by: str, now: float) -> int:
        n = 0
        with self._lock:
            for d in self._data["devices"]:
                if not d.get("revoked"):
                    d["revoked"], d["revoked_by"] = int(now), by
                    n += 1
            self._data["codes"] = []
            self._save()
        return n

    def devices(self) -> list[dict]:
        with self._lock:
            return [dict(d) for d in self._data["devices"]]


def device_id_ok(did: str | None) -> bool:
    return bool(did) and bool(_ID_RE.match(did))


class RateLimiter:
    """At most `limit` hits per `window` seconds per key (the client IP)."""

    def __init__(self, limit: int = 10, window: int = 600, max_keys: int = 4096):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float) -> bool:
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                if len(self._hits) >= self.max_keys:          # drop idle keys first
                    for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                        del self._hits[k]
                q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def origin_ok(origin: str | None, referer: str | None, expected: str) -> bool:
    """The POST came from a page on `expected` (scheme://host): Origin when the
    browser sends one (every current browser does on POST), else Referer."""
    if origin:
        return origin == expected
    if referer:
        return referer == expected or referer.startswith(expected + "/")
    return False


def qr_svg(url: str) -> str:
    """Inline SVG QR code for the pairing link, or "" without segno."""
    try:
        import segno
    except ImportError:
        return ""
    try:
        return segno.make(url, error="m").svg_inline(scale=6, border=2, dark="#100D0B", light="#F5EEE6")
    except Exception:
        log.warning("kitchen: QR render failed")
        return ""


# ---- pages ------------------------------------------------------------------------
_CSS = ("<style>.pair{max-width:640px;margin:32px auto;padding:0 16px}"
        ".pair .card{padding:24px;margin-bottom:16px}.pair h1{margin:0 0 12px;font-size:28px}"
        ".pair input[type=text]{width:100%;box-sizing:border-box;font-size:20px;padding:12px;"
        "border-radius:10px;border:1px solid #4a3d35;background:#100D0B;color:#F5EEE6;margin:8px 0 16px}"
        ".pair .code{font:600 22px ui-monospace,monospace;letter-spacing:1px;word-break:break-all}"
        ".pair .qr svg{width:240px;height:240px;display:block;margin:12px 0}"
        ".pair table{width:100%;border-collapse:collapse}.pair td,.pair th{text-align:left;padding:8px 4px;"
        "border-bottom:1px solid #2d2420;vertical-align:middle}.pair .revoked{opacity:.55}"
        ".pair form.inline{display:inline}.pair .err{color:#E2A86B}.pair .row{display:flex;gap:12px;flex-wrap:wrap}"
        "</style>")


def _shell(head: str, body: str) -> str:
    return f"{head.replace('</head>', _CSS + '</head>', 1)}<body class='sub'><main class='pair'>{body}</main></body></html>"


# The pairing link carries the code in the URL fragment (/pair#code=...), which
# a browser never sends to a server, so Traefik's access log and Loki never see
# it. This script copies it into the form, clears the fragment and focuses the
# name; the code reaches the server only in the POST body. Without JS the code
# box is typed by hand.
_PAIR_JS = ("<script>(function(){var m=/(?:^#|&)code=([^&]*)/.exec(location.hash||'');"
            "if(!m){return;}var c=document.getElementById('code');"
            "try{c.value=decodeURIComponent(m[1]);}catch(e){c.value=m[1];}"
            "try{history.replaceState(null,'',location.pathname);}catch(e){}"
            "var n=document.getElementById('name');if(n){n.focus();n.select();}})();</script>")


def pair_form(error: str = "") -> str:
    err = f"<p class='err'>{escape(error)}</p>" if error else ""
    return ("<section class='card'><h1>Pair this screen</h1>"
            f"{err}<p>Open the pairing link from the devices page, or type the code it shows.</p>"
            "<form method='post' action='/pair'><label for='code'>Pairing code</label>"
            "<input id='code' name='code' type='text' autocomplete='off' autocapitalize='characters' "
            "spellcheck='false' required maxlength='40'>"
            "<label for='name'>Device name</label>"
            f"<input id='name' name='name' type='text' value='Kitchen iPad' maxlength='{NAME_MAX}' required>"
            "<button class='btn primary' type='submit'>Pair</button></form></section>" + _PAIR_JS)


def unpaired_page(head: str) -> str:
    return _shell(head, "<section class='card'><h1>This screen isn't paired</h1>"
                  "<p>On a laptop or phone signed in as Jacob, open "
                  "<b>home.kmkdp.com/meal/kitchen/devices</b> and press <b>Pair a device</b>. "
                  "Then open the link on this screen, or type the code below.</p></section>"
                  + pair_form())


def pair_page(head: str, error: str = "") -> str:
    return _shell(head, pair_form(error))


def message_page(head: str, title: str, text: str) -> str:
    return _shell(head, f"<section class='card'><h1>{escape(title)}</h1><p>{escape(text)}</p></section>")


def _when(ts, tz) -> str:
    if not ts:
        return "never"
    return datetime.datetime.fromtimestamp(int(ts), tz).strftime("%b %-d, %Y %-I:%M %p")


def devices_page(head: str, devs: list[dict], tz, configured: bool, base: str,
                 code: str = "", link: str = "", qr: str = "") -> str:
    parts = ["<section class='card'><h1>Kitchen devices</h1>"
             "<p>Paired screens open <b>kitchen.kmkdp.com</b> without a sign-in for one year. "
             "Revoke one here if it is lost or replaced.</p>"]
    if not configured:
        parts.append("<p class='err'>Pairing is not configured: KITCHEN_DEVICE_KEY is missing "
                     "from the dashboard .env.</p>")
    else:
        parts.append(f"<form method='post' action='{base}/kitchen/devices/pair'>"
                     "<button class='btn primary' type='submit'>Pair a device</button></form>")
    parts.append("</section>")
    if code:
        parts.append("<section class='card'><h1>Pairing link</h1>"
                     "<p>Open this on the kitchen screen within 10 minutes. It works once.</p>"
                     f"<p><a href='{escape(link)}'>{escape(link)}</a></p>"
                     + (f"<div class='qr'>{qr}</div>" if qr else "")
                     + f"<p>Or type the code: <span class='code'>{escape(group_code(code))}</span></p></section>")
    rows = []
    for d in sorted(devs, key=lambda d: (bool(d.get("revoked")), -int(d.get("issued") or 0))):
        if d.get("revoked"):
            state = f"revoked {_when(d['revoked'], tz)}"
            act = ""
        else:
            state = "active"
            act = (f"<form class='inline' method='post' action='{base}/kitchen/devices/revoke'>"
                   f"<input type='hidden' name='id' value='{escape(d['id'])}'>"
                   "<button class='btn ghost' type='submit'>Revoke</button></form>")
        rows.append(f"<tr class='{'revoked' if d.get('revoked') else ''}'><td>{escape(d.get('name') or '')}</td>"
                    f"<td>{escape(_when(d.get('issued'), tz))}</td><td>{escape(_when(d.get('last_seen'), tz))}</td>"
                    f"<td>{escape(state)}</td><td>{act}</td></tr>")
    table = ("<table><tr><th>Name</th><th>Paired</th><th>Last seen</th><th>State</th><th></th></tr>"
             + "".join(rows) + "</table>") if rows else "<p class='muted'>No devices paired yet.</p>"
    active = any(not d.get("revoked") for d in devs)
    revoke_all = (f"<form method='post' action='{base}/kitchen/devices/revoke-all'>"
                  "<button class='btn ghost' type='submit'>Revoke all</button></form>") if active else ""
    parts.append(f"<section class='card'><h1>Paired</h1>{table}<div class='row'>{revoke_all}</div></section>")
    return _shell(head, "".join(parts))
