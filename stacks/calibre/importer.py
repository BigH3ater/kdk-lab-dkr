#!/usr/bin/env python3
"""calibre-import: add LazyLibrarian's ebooks to the Calibre library, once each.

Runs as the `import` service of stacks/calibre (docs/services/calibre.md,
"Import and dedupe"). Every INTERVAL seconds it scans INCOMING and adds through
the running Content Server (SERVER, localhost in the shared netns).

Why not `calibredb add -r INCOMING --automerge ignore` (the old loop): calibre
dedupes by title+authors only, so a file whose embedded title is junk, or whose
LazyLibrarian sidecar .opf carries a different title/author string, looked new
on every pass and was re-added every 15 minutes; and a sidecar .opf whose
basename differs from the ebook's became a book of its own with no formats
(ids 30-33 and 43-46, 2026-11-08).

Dedupe here is by PATH and CONTENT, with calibre's title/author check kept as
the last guard:
  1. manifest (STATE_DIR/manifest.json): every source file handled, keyed by its
     path under INCOMING, with size, mtime_ns and sha256. A file whose size and
     mtime are unchanged is skipped without being read; a touched file with the
     same sha256 is just re-stamped; a file with new content is added again.
  2. library content index (LIBRARY, the Calibre library mounted read-only):
     a file whose sha256 equals a format file already in the library is recorded
     as `in-library` and never sent to calibredb. This seeds the manifest on the
     first run (calibre stores added files byte-for-byte) and makes a lost
     manifest harmless.
  3. calibredb add --automerge ignore, one logical book per call.

Books are the ebook files of one directory that share a basename (calibre's own
grouping, so name.epub + name.mobi stay one record). .opf files are never added
as books: the directory's sidecar (same basename, or the only .opf in a
directory holding a single book) is staged next to the ebook under the ebook's
basename so calibre reads it as the book's metadata, and its cover image comes
along. calibredb has no --opf option; staging symlinks in /tmp is how a sidecar
with a different basename reaches it.

Usage: importer.py            loop forever (the service)
       importer.py --once     one pass, then exit
       importer.py --forget REL_PATH [...]   drop manifest entries so the next
                              pass re-imports those files (paths under INCOMING)
No secrets: the server needs no credentials from localhost.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Ebook formats imported (a subset of calibre's BOOK_EXTENSIONS: no txt/html/md/
# zip/doc noise). Lower-case, no dot.
EBOOK_EXTS = frozenset({
    "epub", "kepub", "pdf", "mobi", "azw", "azw1", "azw3", "azw4", "kfx", "kpf",
    "prc", "pdb", "fb2", "fbz", "djvu", "djv", "cbz", "cbr", "cb7", "cbc", "lit",
    "lrf", "rtf", "docx", "odt", "htmlz", "txtz", "chm",
})
IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "gif", "webp"})
MANIFEST_VERSION = 1


def log(msg: str) -> None:
    print(f"[calibre-import] {time.strftime('%F %T')} {msg}", flush=True)


@dataclass
class Config:
    server: str
    incoming: Path
    state_dir: Path
    library: Path | None
    stage_dir: Path
    heartbeat: Path
    interval: int
    calibredb: str = "calibredb"

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        lib = env.get("LIBRARY", "/library")
        return cls(
            server=env.get("SERVER", "http://localhost:8081/"),
            incoming=Path(env.get("INCOMING", "/incoming")),
            state_dir=Path(env.get("STATE_DIR", "/state")),
            library=Path(lib) if lib else None,
            stage_dir=Path(env.get("STAGE_DIR", "/tmp/calibre-import-stage")),
            heartbeat=Path(env.get("HEARTBEAT", "/tmp/heartbeat")),
            interval=int(env.get("INTERVAL", "900")),
            calibredb=env.get("CALIBREDB", "calibredb"),
        )


def ext_of(path: Path) -> str:
    return path.suffix[1:].lower()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------- scan

@dataclass
class Group:
    """One logical book: same-basename ebook files in one directory."""
    directory: Path
    stem: str
    files: list[Path]
    sidecar: Path | None = None
    images: list[Path] = field(default_factory=list)


def scan(incoming: Path) -> list[Group]:
    groups: list[Group] = []
    for dirpath, dirnames, filenames in os.walk(incoming):
        dirnames.sort()
        d = Path(dirpath)
        books: dict[str, Group] = {}
        opfs: list[Path] = []
        images: list[Path] = []
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = d / name
            e = ext_of(p)
            if e in EBOOK_EXTS:
                key = p.stem.lower()
                g = books.setdefault(key, Group(d, p.stem, []))
                if all(ext_of(f) != e for f in g.files):  # one file per format
                    g.files.append(p)
            elif e == "opf":
                opfs.append(p)
            elif e in IMAGE_EXTS:
                images.append(p)
        for g in books.values():
            same = [o for o in opfs if o.stem.lower() == g.stem.lower()]
            if same:
                g.sidecar = same[0]
            elif len(opfs) == 1 and len(books) == 1:
                # LazyLibrarian sometimes spells the sidecar differently
                # (O'Reilly vs OReilly, a dropped comma): only trust it when the
                # directory cannot mean any other book.
                g.sidecar = opfs[0]
            if g.sidecar is not None:
                g.images = images
            groups.append(g)
        # A lone .opf (no ebook) is never a book: nothing to do for it.
    return groups


# ----------------------------------------------------------------- manifest

class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.files: dict[str, dict] = {}

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        m = cls(path)
        if not path.exists():
            return m
        try:
            data = json.loads(path.read_text())
            if data.get("version") != MANIFEST_VERSION or not isinstance(data.get("files"), dict):
                raise ValueError(f"unexpected manifest version {data.get('version')!r}")
            m.files = data["files"]
        except (ValueError, OSError) as exc:
            aside = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
            with contextlib.suppress(OSError):
                path.rename(aside)
            log(f"WARNING manifest unreadable ({exc}); moved to {aside.name}, "
                "starting empty (library content index still prevents duplicates)")
        return m

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"version": MANIFEST_VERSION, "files": self.files},
                                  indent=1, sort_keys=True))
        os.replace(tmp, self.path)

    def entry(self, rel: str) -> dict | None:
        return self.files.get(rel)

    def record(self, rel: str, size: int, mtime_ns: int, sha: str, status: str,
               ids: list[int] | None = None) -> None:
        self.files[rel] = {"size": size, "mtime_ns": mtime_ns, "sha256": sha,
                           "status": status, "ids": ids or [],
                           "at": time.strftime("%FT%T%z")}

    def forget(self, rel: str) -> bool:
        return self.files.pop(rel, None) is not None


@contextlib.contextmanager
def locked(state_dir: Path):
    """Serialise the loop and `--forget` on the manifest."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "manifest.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


# ------------------------------------------------------------ library index

class LibraryIndex:
    """sha256 of the ebook files already in the Calibre library (read-only)."""

    def __init__(self, root: Path | None):
        self.root = root
        self._cache: dict[str, tuple[int, int, str]] = {}  # path -> (size, mtime_ns, sha)
        self._by_size: dict[int, list[Path]] | None = None
        self.warned = False

    def refresh(self) -> None:
        self._by_size = {}
        if self.root is None or not self.root.is_dir():
            if not self.warned:
                log(f"WARNING library {self.root} not mounted: content dedupe off, "
                    "relying on the manifest and calibre's title/author check")
                self.warned = True
            return
        for dirpath, _dirs, names in os.walk(self.root):
            for name in names:
                p = Path(dirpath) / name
                if ext_of(p) in EBOOK_EXTS:
                    with contextlib.suppress(OSError):
                        self._by_size.setdefault(p.stat().st_size, []).append(p)

    def contains(self, size: int, sha: str) -> bool:
        if self._by_size is None:
            self.refresh()
        for p in self._by_size.get(size, []):
            try:
                st = p.stat()
                key = str(p)
                c = self._cache.get(key)
                if c is None or c[:2] != (st.st_size, st.st_mtime_ns):
                    c = (st.st_size, st.st_mtime_ns, sha256_file(p))
                    self._cache[key] = c
                if c[2] == sha:
                    return True
            except OSError:
                continue
        return False


# ----------------------------------------------------------------- calibredb

@dataclass
class AddResult:
    status: str            # added | merged | duplicate | failed
    ids: list[int]
    output: str


IDS_RE = re.compile(r"^(Added|Merged) book ids: ([\d, ]+)$", re.M)


def parse_add_output(rc: int, out: str) -> AddResult:
    if rc != 0 or "Traceback" in out or "Forbidden" in out:
        return AddResult("failed", [], out)
    added, merged = [], []
    for kind, ids in IDS_RE.findall(out):
        nums = [int(x) for x in re.findall(r"\d+", ids)]
        (added if kind == "Added" else merged).extend(nums)
    if added:
        return AddResult("added", added, out)
    if merged:
        return AddResult("merged", merged, out)
    if "already exist in the database" in out:
        return AddResult("duplicate", [], out)
    return AddResult("failed", [], out or "calibredb reported nothing")


def stage_group(stage_dir: Path, group: Group, files: list[Path]) -> Path:
    """Symlink one book's files into an empty dir calibre reads as one book."""
    shutil.rmtree(stage_dir, ignore_errors=True)
    book = stage_dir / "book"
    book.mkdir(parents=True)
    for f in files:
        (book / f.name).symlink_to(f)
    if group.sidecar is not None:
        # under the ebook's basename, so calibre pairs it with the formats
        (book / f"{files[0].stem}.opf").symlink_to(group.sidecar)
        for img in group.images:  # the opf's cover href is relative to it
            with contextlib.suppress(FileExistsError):
                (book / img.name).symlink_to(img)
    return book


def calibredb_add(cfg: Config, group: Group, files: list[Path]) -> AddResult:
    book = stage_group(cfg.stage_dir, group, files)
    cmd = [cfg.calibredb, "add", "--with-library", cfg.server, "--automerge", "ignore",
           "--one-book-per-directory"]
    for e in sorted(IMAGE_EXTS):
        cmd += ["--ignore", f"*.{e}"]
    cmd.append(str(book))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        res = parse_add_output(proc.returncode, (proc.stdout + proc.stderr).strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        res = AddResult("failed", [], f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(cfg.stage_dir, ignore_errors=True)
    return res


# ---------------------------------------------------------------------- pass

@dataclass
class Summary:
    added: int = 0       # books calibre created
    merged: int = 0      # books that gained a format
    duplicate: int = 0   # calibre's title/author guard said it exists
    in_library: int = 0  # files whose bytes are already a library format
    skipped: int = 0     # files unchanged since the manifest recorded them
    failed: int = 0      # books calibredb refused / errored on (retried next pass)

    def line(self) -> str:
        return " ".join(f"{k}={v}" for k, v in vars(self).items())


def run_pass(cfg: Config, manifest: Manifest, library: LibraryIndex, add=calibredb_add) -> Summary:
    s = Summary()
    library._by_size = None  # re-list the library each pass (cheap; hashes are cached)
    groups = scan(cfg.incoming)
    seen: set[str] = set()
    for g in groups:
        pending: list[tuple[Path, str, int, int, str]] = []
        for f in g.files:
            rel = f.relative_to(cfg.incoming).as_posix()
            seen.add(rel)
            try:
                st = f.stat()
            except OSError:
                continue
            e = manifest.entry(rel)
            if e and e["size"] == st.st_size and e["mtime_ns"] == st.st_mtime_ns:
                s.skipped += 1
                continue
            sha = sha256_file(f)
            if e and e["sha256"] == sha:  # touched, not changed
                e["mtime_ns"] = st.st_mtime_ns
                s.skipped += 1
                continue
            pending.append((f, rel, st.st_size, st.st_mtime_ns, sha))
        if not pending:
            continue
        to_add = []
        for item in pending:
            f, rel, size, mtime_ns, sha = item
            if library.contains(size, sha):
                manifest.record(rel, size, mtime_ns, sha, "in-library")
                s.in_library += 1
                log(f"in library already, recorded: {rel}")
            else:
                to_add.append(item)
        if to_add:
            res = add(cfg, g, [i[0] for i in to_add])
            label = (g.directory / g.stem).relative_to(cfg.incoming).as_posix()
            fmts = ",".join(ext_of(i[0]).upper() for i in to_add)
            if res.status == "failed":
                s.failed += 1
                log(f"FAILED {label} [{fmts}]: {res.output}")
            else:
                setattr(s, res.status, getattr(s, res.status) + 1)
                for f, rel, size, mtime_ns, sha in to_add:
                    manifest.record(rel, size, mtime_ns, sha, res.status, res.ids)
                ids = ",".join(map(str, res.ids)) or "-"
                sidecar = f" opf={g.sidecar.name}" if g.sidecar else ""
                log(f"{res.status} {label} [{fmts}] ids={ids}{sidecar}")
        manifest.save()  # after every book: a crash mid-pass loses nothing
    # Drop entries for files that left INCOMING, but never on an empty scan
    # (an unmounted volume must not wipe the manifest).
    if groups:
        gone = [rel for rel in manifest.files if rel not in seen]
        for rel in gone:
            manifest.forget(rel)
        if gone:
            log(f"forgot {len(gone)} manifest entr{'y' if len(gone) == 1 else 'ies'} "
                "for files no longer in the source")
    manifest.save()
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="run one pass and exit")
    ap.add_argument("--forget", nargs="+", metavar="REL_PATH",
                    help="drop these paths (relative to INCOMING) from the manifest")
    args = ap.parse_args(argv)
    cfg = Config.from_env()
    mpath = cfg.state_dir / "manifest.json"

    if args.forget:
        with locked(cfg.state_dir):
            m = Manifest.load(mpath)
            for rel in args.forget:
                rel = rel.removeprefix(str(cfg.incoming).rstrip("/") + "/")
                print(("forgot " if m.forget(rel) else "not in manifest: ") + rel)
            m.save()
        return 0

    library = LibraryIndex(cfg.library)
    rc = 0
    while True:
        cfg.heartbeat.write_text(str(int(time.time())))
        log(f"scanning {cfg.incoming} -> {cfg.server}")
        try:
            with locked(cfg.state_dir):
                manifest = Manifest.load(mpath)
                s = run_pass(cfg, manifest, library)
            log(f"pass done: {s.line()}")
            rc = 1 if s.failed else 0
        except Exception as exc:  # keep the loop alive; the next pass retries
            log(f"FAILED pass: {type(exc).__name__}: {exc}")
            rc = 1
        if args.once:
            return rc
        time.sleep(cfg.interval)


if __name__ == "__main__":
    sys.exit(main())
