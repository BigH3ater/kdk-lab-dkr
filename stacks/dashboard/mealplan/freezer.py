"""Freezer inventory for the groceries page (Pantry tab "Freezer" section, Thaw
tab "Reserved" list). Dependency-light (no Flask) so it unit-tests on its own.

The file: /pantry/freezer.json, a bind mount of /opt/kdk-lab/dashboard/pantry
on kdk-dkr-01, shared with the grocery-planner job (which reads it to plan and
decrements what a planned week reserves). Schema and units:
stacks/grocery-planner/grocery/freezer.py. Every Pantry write: the current file
is copied to freezer.json.bak, the new one lands via a unique tmp + rename
(atomic), all under an flock on .freezer.lock that the planner takes too.
"undo" swaps the two: it undoes the owner's LAST TAP only. The planner never
writes .bak, and undo is refused while the newest version is the weekly
build's reservation (undoing that would put reserved meat back). "Order
arrived" is recorded (order, time) and a repeat within 12 h is refused.
Counts only; no prices, no secrets.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
import re
import tempfile
import threading

SCHEMA_VERSION = 1
FILE = os.environ.get("FREEZER_FILE", "/pantry/freezer.json")
UNITS = ("portion", "lb", "package")
LOCK_NAME = ".freezer.lock"         # shared with the grocery planner's reservation write
PLANNER = "grocery-planner"         # updated_by of the planner's writes
ARRIVAL_WINDOW = datetime.timedelta(hours=12)
_lock = threading.Lock()


class FreezerError(Exception):
    """A refused change (unknown cut, bad number, no file)."""


def load(path: str | None = None) -> dict | None:
    """The inventory, or None when the file is missing/unreadable/another
    schema version (the page then says so and offers no buttons)."""
    try:
        with open(path or FILE) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cuts"), list):
        return None
    if data.get("version") != SCHEMA_VERSION:
        return None
    return data


def _write(p: str, raw: bytes) -> None:
    """A unique mkstemp name in the same dir + rename (both containers run as
    PID 2 under init, so a pid-based name could collide)."""
    d = os.path.dirname(p) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{os.path.basename(p)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.chmod(tmp, 0o644)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def file_lock(p: str):
    """Exclusive flock on <dir>/.freezer.lock (the planner's apply_reservations
    takes the same one). Read-only open when the other uid created it."""
    lp = os.path.join(os.path.dirname(p) or ".", LOCK_NAME)
    fd = None
    for flags in (os.O_RDWR | os.O_CREAT, os.O_RDONLY):
        try:
            fd = os.open(lp, flags, 0o666)
            break
        except OSError:
            continue
    try:
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def save(data: dict, who: str, path: str | None = None) -> None:
    p = path or FILE
    data = dict(data, version=SCHEMA_VERSION, updated_by=who or "dashboard",
                updated=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    data.pop("restored_by", None)
    if os.path.exists(p):
        with open(p, "rb") as fh:
            _write(p + ".bak", fh.read())
    _write(p, (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode())


def _num(raw, lo=0.0, hi=1000.0) -> float:
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        raise FreezerError("not a number")
    if not (lo <= v <= hi) or v != v:
        raise FreezerError("out of range")
    return round(v, 2)


def _clean(v: float):
    return int(v) if float(v).is_integer() else v


def _cut(data: dict, cut_id: str) -> dict:
    for c in data["cuts"]:
        if c.get("id") == cut_id:
            return c
    raise FreezerError(f"unknown cut {cut_id!r}")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]


def change(action: str, form: dict, who: str, orders: dict | None = None,
           path: str | None = None, now: datetime.datetime | None = None) -> str:
    """Apply one Pantry-tab action under the lock; returns a short log text.
      adjust   cut, delta (+1/-1)        set       cut, count
      portions cut, n (portions/package) add       name, unit, count, package,
      arrived  order (standing_orders key)          package_lb (portion/package)
      undo     swap freezer.json <-> .bak: the owner's last tap only
    Raises FreezerError on a refused change."""
    p = path or FILE
    with _lock, file_lock(p):
        return _change(action, form, who, orders, p, now)


def _change(action, form, who, orders, p, now) -> str:
    if action == "undo":
        if not os.path.exists(p + ".bak"):
            raise FreezerError("nothing to undo")
        cur_data = load(p)
        if (cur_data is not None and cur_data.get("updated_by") == PLANNER
                and not cur_data.get("restored_by")):
            raise FreezerError("the last change was the weekly build's reservation; "
                               "undo only reverts your own last tap")
        with open(p, "rb") as cur, open(p + ".bak", "rb") as bak:
            cur_raw, prev = cur.read(), bak.read()
        try:                                 # mark it restored, so a redo is never refused
            prev = (json.dumps(dict(json.loads(prev), restored_by=who or "dashboard"),
                               indent=1, ensure_ascii=False) + "\n").encode()
        except (ValueError, TypeError):
            pass
        _write(p + ".bak", cur_raw)          # .bak first: a crash between keeps both copies
        _write(p, prev)
        return "undo: restored the version before your last tap"
    data = load(p)
    if data is None:
        raise FreezerError("no freezer inventory file")
    if action in ("adjust", "set"):
        c = _cut(data, form.get("cut", ""))
        old = float(c.get("count") or 0)
        new = old + _num(form.get("delta"), -100, 100) if action == "adjust" else _num(form.get("count"))
        c["count"] = _clean(round(max(0.0, new), 2))
        c.pop("fill_in", None)                   # a count the owner touched is known
        text = f"{c['id']} {_clean(old)} -> {c['count']}"
    elif action == "portions":
        c = _cut(data, form.get("cut", ""))
        c["portions_per_package"] = _clean(_num(form.get("n"), 1, 20))
        text = f"{c['id']} portions_per_package={c['portions_per_package']}"
    elif action == "add":
        name = (form.get("name") or "").strip()[:60]
        unit = form.get("unit") or "portion"
        cid = slug(name)
        if not cid or unit not in UNITS:
            raise FreezerError("name and unit needed")
        if any(c.get("id") == cid for c in data["cuts"]):
            raise FreezerError(f"{cid} already listed")
        row = {"id": cid, "name": name, "unit": unit,
               "count": _clean(_num(form.get("count") or 0)),
               "package": (form.get("package") or "").strip()[:40],
               "source": "manual"}
        # Recipes measure meat by weight: a portion/package cut needs its weight
        # or the planner can't convert "1.5 lb" (freezer_unconvertible).
        raw_lb = str(form.get("package_lb") or "").strip()
        if unit != "lb" and not raw_lb:
            raise FreezerError("lb per portion/package needed (recipes measure by weight)")
        if raw_lb:
            row["package_lb"] = _clean(_num(raw_lb, 0.01, 50))
        data["cuts"].append(row)
        text = f"added {cid}"
    elif action == "arrived":
        key = form.get("order", "")
        order = (orders or {}).get(key)
        if not order:
            raise FreezerError("unknown standing order")
        t = (now or datetime.datetime.now().astimezone())
        for r in data.get("arrivals") or []:
            try:
                at = datetime.datetime.fromisoformat(str(r.get("at")))
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.astimezone()
            if r.get("order") == key and abs(t - at) < ARRIVAL_WINDOW:
                raise FreezerError(f"order {key} already recorded at {str(r.get('at'))[:16]} "
                                   "(within 12 h); use +/− or undo")
        data["arrivals"] = ((data.get("arrivals") or [])
                            + [{"order": key, "at": t.isoformat(timespec="seconds")}])[-20:]
        added, skipped = [], []
        for cid, amt in (order.get("items") or {}).items():
            try:
                c = _cut(data, cid)
            except FreezerError:
                skipped.append(cid)
                continue
            c["count"] = _clean(round(float(c.get("count") or 0) + float(amt), 2))
            c.pop("fill_in", None)
            added.append(f"{cid}+{_clean(float(amt))}")
        text = (f"order arrived {form.get('order')}: {', '.join(added)}"
                + (f" (not listed: {', '.join(skipped)})" if skipped else ""))
    else:
        raise FreezerError(f"unknown action {action!r}")
    save(data, who, p)
    return text


def fmt_count(c: dict) -> str:
    n = _clean(float(c.get("count") or 0))
    unit = c.get("unit") or "portion"
    if unit == "lb":
        return f"{n} lb"
    word = {"portion": "portion", "package": "pkg"}[unit] if unit in ("portion", "package") else unit
    return f"{n} {word}{'' if n == 1 or word == 'pkg' else 's'}"


def reserved(data: dict | None, weeks: set[str]) -> list[dict]:
    """The planner's reservations for the given ISO week keys, by date."""
    if not data:
        return []
    rows = [r for r in data.get("reservations") or [] if r.get("week") in weeks]
    return sorted(rows, key=lambda r: (r.get("date") or "", r.get("cut") or ""))
