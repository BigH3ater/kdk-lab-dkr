"""The add box's "Manual list instead" registry: shopping-list entry ids that go
on the Manual list even though their Tandoor food has a NewPi/Target category.

Tandoor is never edited for this (no food or category change): the dashboard
keeps the ids in its own small JSON file on the writable /state mount
(manual-forced.json, next to devices.json), written atomically (temp file in
the same directory, fsync, rename). Every read prunes ids whose entry is
checked, or is gone from a list read made well after the id was recorded, so
the file only ever holds the few open forced items.

One process owns the file (the sidecar); a lock serialises its read-modify-
write cycles across waitress threads."""
import json
import os
import tempfile
import threading
import time

# An id missing from a list read is only pruned once it is older than this:
# a page can render from a snapshot read before the add (the snapshot is served
# up to 15 min stale), and that must not drop the brand-new id.
GRACE_S = 1800


class Registry:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def writable(self) -> bool:
        """True if the add box can record a forced-manual id here."""
        d = os.path.dirname(self.path) or "."
        if not (os.path.isdir(d) and os.access(d, os.W_OK | os.X_OK)):
            return False
        return not os.path.exists(self.path) or os.access(self.path, os.W_OK)

    def _load(self) -> dict[int, float]:
        try:
            with open(self.path) as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return {}
        ids = raw.get("ids") if isinstance(raw, dict) else None
        out: dict[int, float] = {}
        for k, v in (ids or {}).items() if isinstance(ids, dict) else ():
            try:
                out[int(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out

    def _save(self, ids: dict[int, float]) -> bool:
        d = os.path.dirname(self.path) or "."
        try:
            fd, tmp = tempfile.mkstemp(prefix=".manual-forced-", dir=d)
        except OSError:
            return False
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump({"ids": {str(k): v for k, v in sorted(ids.items())}}, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            return True
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False

    def add(self, eid: int, now: float | None = None) -> bool:
        """Record one entry id. False if the file could not be written."""
        if not self.writable():
            return False
        with self._lock:
            ids = self._load()
            ids[int(eid)] = now if now is not None else time.time()
            return self._save(ids)

    def ids(self, entries=None, now: float | None = None) -> set[int]:
        """The forced ids. Given `entries` (a full shopping-list read), first
        prune ids whose entry is checked, or is absent and older than GRACE_S;
        the file is rewritten only when something was pruned (and only if it
        can be: an unwritable /state still routes from what it can read)."""
        now = now if now is not None else time.time()
        with self._lock:
            ids = self._load()
            if entries is None or not ids:
                return set(ids)
            state = {}
            for e in entries:
                try:
                    state[int(e.get("id"))] = bool(e.get("checked"))
                except (TypeError, ValueError):
                    continue
            keep = {i: ts for i, ts in ids.items()
                    if (i in state and not state[i]) or (i not in state and now - ts < GRACE_S)}
            if len(keep) != len(ids):
                self._save(keep)
            return set(keep)
