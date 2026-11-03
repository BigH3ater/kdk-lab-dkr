"""HTTP wrapper around DeeZy/DEE for Dolby-certified DD+ encoding of TrueHD
sources (media pipeline design §7.4b/§8.5).

This service runs ON THE WINDOWS DEE VM, not in the k3s cluster -- DEE's
TrueHD encoding support is Windows-only, so there is deliberately no
Dockerfile, no build-images.yaml job, and no k8s manifest for it (see
docs/plans/completed/2026-08-20-0938-feat-dee-vm-windows-encoder-plan.md, KTD1).
Install/runbook: README.md next to this file, plus the vault runbook for
site-specific values.

Contract (the "Tdarr insertion contract" in the plan and vault runbook):
Tdarr's flow POSTs /encode with a bare filename relative to the shared
exchange volume's inbound/ directory; this wrapper resolves it against its
own DEE_EXCHANGE_ROOT (a Windows drive letter or UNC path for the same
volume), runs DeeZy, and writes the .ec3 next door in outbound/. Neither
side ever sends the other an absolute path -- that is what keeps cross-OS
path translation out of the contract entirely.

Config is env-only; no internal hostname or IP is baked in here (this repo
may become public -- concrete site values live in the vault runbook only):

- DEE_WRAPPER_TOKEN            required; bearer token /encode callers must present
- DEE_EXCHANGE_ROOT            required; Windows path of the exchange volume
                               (UNC recommended -- a Scheduled Task running as
                               SYSTEM has no mapped drive letters)
- DEEZY_PATH                   optional; DeeZy executable (default "deezy")
- DEE_WRAPPER_TIMEOUT_SECONDS  optional; per-encode subprocess budget (default 7200)
- DEE_WRAPPER_HOST             optional; bind address (default 0.0.0.0 -- uvicorn's
                               own default is loopback, unreachable remotely; safe
                               only behind the scoped Windows Firewall rule the
                               runbook creates)
- DEE_WRAPPER_PORT             optional; listen port (default 8484)
- DEEZY_EXTRA_ARGS             optional; whitespace-split extra CLI args appended
                               to every DeeZy invocation (absorbs DeeZy version
                               differences without a code change)

Single-encode serialization: one encode at a time (the VM has 4 vCPUs and DEE
is the heavy consumer); a concurrent caller gets 503, which the insertion
contract defines as retryable-with-backoff, not a fallback trigger.

Timeout handling kills the whole process tree, not just the direct child --
on Windows, killing DeeZy alone leaves its spawned dee.exe running, holding
CPU and file handles after the lock frees.
"""

from __future__ import annotations

import hmac
import logging
import logging.handlers
import os
import re
import shutil
import subprocess  # nosec B404 -- wrapping a local CLI is this service's whole job
import tempfile
import threading
import time
from typing import Literal
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

log = logging.getLogger("dee_wrapper")

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

DEFAULT_BITRATE_KBPS = 1024
DEFAULT_TIMEOUT_SECONDS = 7200
DEFAULT_PORT = 8484
STDERR_DETAIL_LIMIT = 2000
# Minimum free space (GB) required on BOTH the exchange volume and DeeZy's local
# work drive before an encode is allowed to start. DeeZy's --keep-temp writes
# multi-GB WAV intermediates to its work dir on the OS drive; if that drive (or
# the exchange mount) is near-full the encode must be refused, not run -- a
# 2026-09-15 outage filled C: this way when the exchange SMB mount dropped.
DEFAULT_MIN_FREE_GB = 20
# How stale a leftover DeeZy job folder must be before the sweep reclaims it.
DEFAULT_TEMP_MAX_AGE_HOURS = 6
# Rotating wrapper log: ~5 MB x 5 files.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
# Short stderr tail written to the log on a failed encode (the HTTP response
# still carries the longer STDERR_DETAIL_LIMIT tail).
STDERR_LOG_TAIL = 500

# DeeZy writes one work folder per job under local_work_dir(), named
# "<SafeName>_<8hex>_t0" with an optional "_<4hex>" suffix, and deletes it ONLY
# on a successful encode. A failed or killed encode leaves it behind, and the
# wrapper never cleaned them up: 84 such folders (55 GB, incl. a 25 GB killed
# Atmos intermediate) once pinned C: at the min_free_gb floor and made every
# subsequent encode fail the pre-flight (2026-11-03 incident). The sweep and
# post-failure cleanup below reclaim them. Only directories DIRECTLY under the
# work dir whose names match this pattern are ever deleted -- nothing else in
# the work dir (or anywhere else) is touched.
# DeeZy names each job folder from the first <=20 chars of the INPUT filename,
# with characters other than [A-Za-z0-9-] turned into "_", then
# _<8 hex>_t0[_<4 hex>]. Real examples from kdk-dee-01 (2026-11-03):
# "The_Lord_of_the_Ring_4bf1f260_t0_beab", "Bluey__2018__-_S01E2_6f432da1_t0".
JOB_DIR_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}_[0-9A-Fa-f]{8}_t0(?:_[0-9A-Fa-f]{4})?$")

_ENCODE_LOCK = threading.Lock()


class ConfigError(RuntimeError):
    """Raised at startup when required environment variables are missing."""


@dataclass(frozen=True)
class Config:
    token: str
    exchange_root: str
    deezy_path: str = "deezy"
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    extra_args: list[str] = field(default_factory=list)
    min_free_gb: int = DEFAULT_MIN_FREE_GB
    work_dir: str = ""  # override for DeeZy's --keep-temp work drive; else LOCALAPPDATA\deezy
    temp_max_age_hours: int = DEFAULT_TEMP_MAX_AGE_HOURS  # sweep folders idle longer than this
    log_dir: str = ""  # override for the rotating log dir; else <this file's dir>\logs

    @classmethod
    def from_env(cls) -> "Config":
        missing = [
            name
            for name in ("DEE_WRAPPER_TOKEN", "DEE_EXCHANGE_ROOT")
            if not os.environ.get(name)
        ]
        if missing:
            raise ConfigError(
                "missing required environment variables: " + ", ".join(missing)
            )
        return cls(
            token=os.environ["DEE_WRAPPER_TOKEN"],
            exchange_root=os.environ["DEE_EXCHANGE_ROOT"],
            deezy_path=os.environ.get("DEEZY_PATH", "deezy"),
            timeout_seconds=int(
                os.environ.get("DEE_WRAPPER_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            ),
            extra_args=os.environ.get("DEEZY_EXTRA_ARGS", "").split(),
            min_free_gb=int(
                os.environ.get("DEE_WRAPPER_MIN_FREE_GB", DEFAULT_MIN_FREE_GB)
            ),
            work_dir=os.environ.get("DEE_WRAPPER_WORK_DIR", ""),
            temp_max_age_hours=int(
                os.environ.get(
                    "DEE_WRAPPER_TEMP_MAX_AGE_HOURS", DEFAULT_TEMP_MAX_AGE_HOURS
                )
            ),
            log_dir=os.environ.get("DEE_WRAPPER_LOG_DIR", ""),
        )

    def inbound_dir(self) -> str:
        return os.path.join(self.exchange_root, "inbound")

    def outbound_dir(self) -> str:
        return os.path.join(self.exchange_root, "outbound")

    def inbound(self, filename: str) -> str:
        return os.path.join(self.inbound_dir(), filename)

    def outbound(self, filename: str) -> str:
        return os.path.join(self.outbound_dir(), filename)

    def local_work_dir(self) -> str:
        """Drive DeeZy writes its --keep-temp WAV intermediates to. Defaults to
        DeeZy's per-user cache on the OS drive (%LOCALAPPDATA%\\deezy); this is
        what filled C: during the 2026-09-15 outage, so its free space is
        pre-flighted before every encode."""
        if self.work_dir:
            return self.work_dir
        local_appdata = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        return os.path.join(local_appdata, "deezy")

    def logs_dir(self) -> str:
        """Directory for the rotating wrapper log. Defaults to a `logs/` folder
        next to this file (C:\\dee-wrapper\\logs on the VM)."""
        if self.log_dir:
            return self.log_dir
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


class EncodeRequest(BaseModel):
    filename: str = Field(min_length=1)
    track_index: int | None = Field(default=None, ge=0)
    bitrate_kbps: int = Field(default=DEFAULT_BITRATE_KBPS, gt=0)
    # "atmos" selects DeeZy's truehdd-assisted DD+ JOC path (E-AC-3 with the
    # Atmos object metadata preserved -- what Apple TV / streaming ship as
    # "DD+ Atmos"); "ddp" is plain channel-bed DD+. Same .ec3 container either
    # way. Only use "atmos" when the source track actually carries Atmos;
    # DeeZy's atmos subcommand fails on a non-Atmos TrueHD input.
    format: Literal["ddp", "atmos"] = "ddp"


class EncodeResponse(BaseModel):
    status: str
    output_filename: str
    duration_seconds: float


class HealthResponse(BaseModel):
    status: str
    deezy_present: bool
    exchange_reachable: bool
    exchange_free_gb: float | None = None
    work_free_gb: float | None = None
    min_free_gb: int = DEFAULT_MIN_FREE_GB
    # Leftover DeeZy job folders currently under the work dir (the thing that
    # filled C: in the 2026-11-03 incident). temp_gb is their total size.
    temp_dirs: int | None = None
    temp_gb: float | None = None


def _is_windows() -> bool:
    return os.name == "nt"


KILL_GRACE_SECONDS = 30


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the DeeZy process AND its children (dee.exe survives a plain kill).

    Both the kill command and the post-kill drain are bounded: an
    unresponsive taskkill or a child that ignores SIGKILL must never hang
    this call indefinitely, since the caller holds the single-encode lock
    until this returns -- an unbounded wait here would wedge every future
    /encode behind a lock that never releases.
    """
    if _is_windows():
        try:
            subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=KILL_GRACE_SECONDS,
            )
        except subprocess.TimeoutExpired:
            pass  # still attempt to drain below; nothing more we can do here
    else:
        proc.kill()
    try:
        proc.communicate(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        # The kill was issued; this is best-effort cleanup only. The caller
        # already treats the encode as failed regardless of whether the
        # pipes ever finish draining.
        pass


def _run_deezy(cmd: list[str], timeout_seconds: int) -> tuple[int, str]:
    # stdout is never consumed (only the exit code and a stderr tail feed the
    # API response), so don't buffer up to two hours of DeeZy progress output.
    proc = subprocess.Popen(  # nosec B603 -- fixed argv from config, no shell
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raise
    return proc.returncode, stderr


def _validate_filename(filename: str) -> None:
    """Reject anything that is not a bare file name inside inbound/.

    Rejecting NUL and other C0 control characters here (not just letting
    os.path.isfile raise) matters because a NUL byte makes os.path.isfile
    raise ValueError, not return False -- without this check that input
    would 500 instead of reporting the intended 422.
    """
    if (
        "/" in filename
        or "\\" in filename
        or ":" in filename
        or filename in {".", ".."}
        or any(ord(c) < 0x20 for c in filename)
    ):
        raise HTTPException(
            status_code=422,
            detail="filename must be a bare file name inside inbound/ (no separators, drives, control characters, or '..')",
        )


def _check_auth(request: Request, config: Config) -> None:
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme != "Bearer" or not hmac.compare_digest(
        presented.encode(), config.token.encode()
    ):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


def _output_filename(input_filename: str) -> str:
    base = input_filename.rsplit(".", 1)[0] if "." in input_filename else input_filename
    return f"{base}.ec3"


def _deezy_safe_name(output_name: str) -> str:
    """Alphanumeric-only stand-in for output_name, safe to pass as DeeZy's
    --output value.

    Upstream DeeZy bug (confirmed 2026-08-20 against jessielw/DeeZy main,
    v1.3.13): deezy/audio_encoders/dee/json/dee_json_generator.py builds its
    DEE job JSON's filename via clean_string(stem), which strips every
    character outside [A-Za-z0-9] -- while deezy/audio_encoders/dee/ddp.py
    builds the intermediate .wav filename from the RAW, unsanitized stem.
    Any non-alphanumeric character in the --output stem (a hyphen, a dot
    from a release-year-style name like "Movie.2024", an underscore) makes
    the two disagree, so DEE's job config ends up looking for a .wav file
    DeeZy never actually wrote -- reproduced live: "Movie.2024" -> DEE
    looks for "Movie.2024.ddp.wav" but the JSON job is
    "Movie2024.ddp.json", and the encode fails with "Could not access
    mediainfo input file" after DeeZy's own progress bar reports 100%.

    A stem that is already a fixed point of clean_string() can't diverge
    between DeeZy's two code paths regardless of which one runs, so this
    sidesteps the bug instead of depending on an upstream fix. The
    caller renames DeeZy's actual output back to the real, readable
    output_name once the encode succeeds -- this function never reaches
    the API response or the exchange volume's final outbound/ filename.
    """
    stem = output_name.rsplit(".", 1)[0] if "." in output_name else output_name
    safe_stem = re.sub(r"[^A-Za-z0-9]", "", stem) or "output"
    return f"{safe_stem}.ec3"


def build_command(config: Config, req: EncodeRequest, output_path: str) -> list[str]:
    """The §7.4b DeeZy invocation, in current DeeZy's argument syntax.

    NOTE: the media pipeline design doc's §7.4b example shows an older DeeZy
    CLI (`-i <input> -t <idx> -b 1024 -k`). Current DeeZy takes the input
    POSITIONALLY and last, and exposes only long flags — verified against
    upstream's docs/cli/encode-ddp.md. Using the doc's short flags verbatim
    would fail at runtime, so the design doc's intent (full-quality DD+) is
    preserved with today's spelling.

    `--keep-temp` is deliberately NOT passed: it retains the multi-GB WAV
    intermediates in DeeZy's work dir (%LOCALAPPDATA%\\deezy) forever, which
    repeatedly filled the OS drive (2026-09-15 outage; 2026-09-16 the pre-flight
    caught it at 56 GB / 18.7 GB free). The workflow is validated, so let DeeZy
    clean its own intermediates. The pre-flight still guards the work drive.
    """
    cmd = [
        config.deezy_path,
        "encode",
        req.format,
        "--bitrate",
        str(req.bitrate_kbps),
        "--output",
        output_path,
    ]
    if req.track_index is not None:
        cmd += ["--track-index", str(req.track_index)]
    # Input goes last: DeeZy requires the INPUT positional after the flags.
    return cmd + config.extra_args + [config.inbound(req.filename)]


# Bound on every filesystem probe that can touch the exchange (SMB) mount.
# A dropped \\TrueNAS mount makes os.path.isdir / os.path.exists / disk_usage
# block on the Windows SMB redirector indefinitely -- the 2026-09-15 outage
# wedged the whole process (and /healthz) in an unkillable kernel I/O wait
# this way. Bounding them turns a dead mount into a fast "unreachable" result.
FS_PROBE_TIMEOUT_SECONDS = 4.0


def _within(seconds: float, fn, *, default):
    """Run fn() in a daemon thread; return its result, or `default` if it does
    not finish within `seconds`.

    The worker thread is a daemon and is deliberately abandoned on timeout: a
    call blocked in a dead-SMB kernel wait cannot be interrupted from userland,
    so the only safe move is to stop waiting on it. At the wrapper's poll rate
    (tdarr-verify, hourly) the handful of abandoned threads is negligible."""
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["v"] = fn()
        except Exception:  # noqa: BLE001 -- any probe failure == unreachable
            box["v"] = default

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    return box.get("v", default) if not t.is_alive() else default


def _free_gb(path: str) -> float | None:
    """Free space (GB) on the volume containing path, or None if unreachable.

    Probes the nearest existing ancestor so a not-yet-created work dir still
    reports its target drive's free space instead of failing outright. The
    whole probe runs under a timeout so a dead exchange mount reports None
    (unreachable) fast instead of hanging the caller."""

    def probe() -> float | None:
        current = path
        while current and not os.path.exists(current):
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        try:
            return shutil.disk_usage(current).free / (1024**3)
        except OSError:
            return None

    return _within(FS_PROBE_TIMEOUT_SECONDS, probe, default=None)


def _exchange_mounted(config: "Config") -> bool:
    """True if the exchange inbound/ and outbound/ dirs are reachable, bounded
    by a timeout so a dead SMB mount answers False fast rather than blocking."""
    return bool(
        _within(
            FS_PROBE_TIMEOUT_SECONDS,
            lambda: os.path.isdir(config.inbound_dir())
            and os.path.isdir(config.outbound_dir()),
            default=False,
        )
    )


def _preflight(config: Config) -> None:
    """Refuse to start an encode unless the exchange volume is mounted AND both
    the exchange and DeeZy's local work drive have enough free space.

    Without this, a dropped exchange SMB mount or a near-full OS drive lets
    DeeZy silently write multi-GB --keep-temp WAV intermediates to
    %LOCALAPPDATA%\\deezy on C: until the OS drive fills (2026-09-15 outage).
    Failing fast here turns that into a clean, retryable API error instead.
    503 (mount / transient) is retryable-with-backoff per the insertion
    contract; 507 (space) needs operator attention."""
    if not _exchange_mounted(config):
        raise HTTPException(
            status_code=503,
            detail=(
                "exchange volume not mounted (inbound/ or outbound/ missing at "
                f"{config.exchange_root}); retry with backoff"
            ),
        )
    for label, path in (
        ("exchange volume", config.exchange_root),
        ("DeeZy work drive", config.local_work_dir()),
    ):
        free = _free_gb(path)
        if free is None:
            raise HTTPException(
                status_code=503,
                detail=f"{label} not reachable ({path}); retry with backoff",
            )
        if free < config.min_free_gb:
            raise HTTPException(
                status_code=507,
                detail=(
                    f"insufficient free space on {label}: {free:.1f}GB free < "
                    f"{config.min_free_gb}GB required ({path})"
                ),
            )


def _dir_size_bytes(path: str) -> int:
    """Total size of the files under path (symlinks counted as their own link
    size, never followed)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _newest_mtime(path: str) -> float:
    """Most recent mtime anywhere in the tree rooted at path (the directory
    itself and everything under it). Using the newest inner mtime -- not just
    the top folder's -- means a job folder with an old top-level timestamp but
    a file DeeZy is actively writing inside still reads as recent, so the sweep
    never races a live encode even if its own directory mtime looks stale."""
    newest = 0.0
    try:
        newest = os.lstat(path).st_mtime
    except OSError:
        return newest
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                m = os.lstat(os.path.join(root, name)).st_mtime
                if m > newest:
                    newest = m
            except OSError:
                pass
    return newest


def _job_dirs(work_dir: str):
    """Yield (name, full_path) for each immediate subdirectory of work_dir whose
    name matches a DeeZy job folder (JOB_DIR_RE). Never descends into them,
    never yields files or non-matching names -- so only DeeZy's own per-job
    folders are ever eligible for deletion by the sweep / cleanup below."""
    try:
        entries = os.listdir(work_dir)
    except OSError:
        return
    for name in entries:
        if not JOB_DIR_RE.match(name):
            continue
        full = os.path.join(work_dir, name)
        if os.path.isdir(full):
            yield name, full


def _sweep_work_dir(
    work_dir: str, max_age_hours: int, *, now: float | None = None
) -> tuple[list[str], int]:
    """Remove DeeZy job folders under work_dir not written to in the last
    max_age_hours. Returns (removed_names, bytes_freed).

    DeeZy deletes a job folder only on success; a failed or killed encode leaves
    it behind forever (84 folders / 55 GB once pinned C: at the free-space
    floor, 2026-11-03). This reclaims them. Only directories DIRECTLY under
    work_dir whose names match JOB_DIR_RE are considered, so nothing outside
    DeeZy's own job folders is ever touched.

    Callers run this under the single-encode lock (or at startup, with no encode
    running), so a folder an in-flight encode is writing -- whose newest mtime
    is recent -- is both too fresh for the age cut and protected by the lock
    from ever racing a live job."""
    now = time.time() if now is None else now
    cutoff = now - max_age_hours * 3600
    removed: list[str] = []
    freed = 0
    for name, full in _job_dirs(work_dir):
        try:
            if _newest_mtime(full) >= cutoff:
                continue
            size = _dir_size_bytes(full)
            shutil.rmtree(full)
        except OSError as exc:
            log.warning("sweep: could not remove %s: %s", name, exc)
            continue
        removed.append(name)
        freed += size
    if removed:
        log.info(
            "sweep: removed %d stale job folder(s), freed %.2f GB: %s",
            len(removed),
            freed / (1024**3),
            ", ".join(removed),
        )
    return removed, freed


def _cleanup_failed_job(
    work_dir: str, safe_prefix: str, started_at: float
) -> str | None:
    # safe_prefix is kept for the call signature but not used to match: DeeZy
    # names the folder from the INPUT filename (see JOB_DIR_RE), not from
    # _deezy_safe_name, so a prefix match never hit a real folder.
    """Remove the job folder DeeZy left behind for one failed/killed encode.

    Identify it as the newest job folder (by mtime) directly under work_dir
    that was active at or after the encode started. If none can be pinned down, return
    None and leave it to the periodic sweep rather than risk deleting the wrong
    folder. Runs under the single-encode lock, so the newest matching folder is
    unambiguously this encode's."""
    best: tuple[float, str, str] | None = None  # (mtime, name, path)
    for name, full in _job_dirs(work_dir):
        m = _newest_mtime(full)
        if m + 1.0 < started_at:  # folder predates this encode (1s slack)
            continue
        if best is None or m > best[0]:
            best = (m, name, full)
    if best is None:
        return None
    _, name, full = best
    try:
        shutil.rmtree(full)
    except OSError as exc:
        log.warning("post-failure cleanup: could not remove %s: %s", name, exc)
        return None
    log.info("post-failure cleanup: removed job folder %s", name)
    return name


def _temp_usage(work_dir: str) -> tuple[int, float]:
    """(count, GB) of DeeZy job folders currently under work_dir, for /healthz."""
    count = 0
    total = 0
    for _name, full in _job_dirs(work_dir):
        count += 1
        total += _dir_size_bytes(full)
    return count, total / (1024**3)


def _configure_logging(config: Config) -> None:
    """Attach a rotating file log (~5 MB x 5) so each encode's outcome and the
    sweep's actions are recorded. The wrapper wrote no log before, so the reason
    an encode failed was never captured (2026-11-03). Never logs the bearer
    token or request headers -- only filenames, exit codes, durations, a short
    stderr tail, and the sweep's removed folders / bytes freed."""
    log.setLevel(logging.INFO)
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in log.handlers):
        return  # idempotent -- a second create_app() must not double-log
    try:
        logs_dir = config.logs_dir()
        os.makedirs(logs_dir, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(logs_dir, "wrapper.log"),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        log.addHandler(handler)
    except OSError as exc:
        # A log we cannot open must never stop the service from encoding.
        if not log.handlers:
            log.addHandler(logging.NullHandler())
        log.warning("could not open log file in %s: %s", config.logs_dir(), exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config.from_env()
    app.state.config = config
    _configure_logging(config)
    work_dir = config.local_work_dir()
    log.info(
        "dee-wrapper starting; work_dir=%s temp_max_age_hours=%d min_free_gb=%d",
        work_dir,
        config.temp_max_age_hours,
        config.min_free_gb,
    )
    try:
        _sweep_work_dir(work_dir, config.temp_max_age_hours)
    except Exception as exc:  # noqa: BLE001 -- a sweep failure must not block boot
        log.warning("startup sweep failed: %s", exc)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="dee-wrapper",
        description=(
            "DeeZy/DEE wrapper for Dolby DD+ encoding of TrueHD sources; "
            "runs on the Windows DEE VM (media pipeline design §7.4b/§8.5)."
        ),
        lifespan=lifespan,
    )

    @app.get("/healthz", response_model=HealthResponse)
    def healthz(request: Request) -> HealthResponse:
        config: Config = request.app.state.config
        deezy_present = bool(
            os.path.isfile(config.deezy_path) or shutil.which(config.deezy_path)
        )
        exchange_reachable = _exchange_mounted(config)
        exchange_free = _free_gb(config.exchange_root) if exchange_reachable else None
        work_free = _free_gb(config.local_work_dir())
        # "degraded" whenever an encode would be refused by _preflight, so a
        # health probe (e.g. tdarr-verify) catches a dropped mount OR low disk
        # BEFORE the queue starts erroring / filling the OS drive.
        low = (
            exchange_free is not None and exchange_free < config.min_free_gb
        ) or (work_free is not None and work_free < config.min_free_gb)
        # Status mirrors exactly what _preflight would refuse an encode for: the
        # exchange mount being down or either drive being low on space. It does
        # NOT gate on deezy_present -- _preflight doesn't either (a missing DeeZy
        # is a separate config problem, surfaced via the deezy_present field, not
        # a reason to flag the mount/queue as degraded).
        healthy = exchange_reachable and work_free is not None and not low
        try:
            temp_dirs, temp_gb = _temp_usage(config.local_work_dir())
        except OSError:
            temp_dirs, temp_gb = None, None
        return HealthResponse(
            status="ok" if healthy else "degraded",
            deezy_present=deezy_present,
            exchange_reachable=exchange_reachable,
            exchange_free_gb=round(exchange_free, 1) if exchange_free is not None else None,
            work_free_gb=round(work_free, 1) if work_free is not None else None,
            min_free_gb=config.min_free_gb,
            temp_dirs=temp_dirs,
            temp_gb=round(temp_gb, 2) if temp_gb is not None else None,
        )

    @app.post("/encode", response_model=EncodeResponse)
    def encode(request: Request, req: EncodeRequest) -> EncodeResponse:
        config: Config = request.app.state.config
        _check_auth(request, config)
        _validate_filename(req.filename)
        # Pre-flight: exchange must be mounted and both drives must have room,
        # BEFORE DeeZy runs -- otherwise --keep-temp intermediates fill C:.
        _preflight(config)

        input_path = config.inbound(req.filename)
        if not os.path.isfile(input_path):
            raise HTTPException(
                status_code=404, detail="input file not found in inbound/"
            )

        output_name = _output_filename(req.filename)
        output_path = config.outbound(output_name)
        # DeeZy gets a sanitized --output, not output_path directly -- see
        # _deezy_safe_name's docstring for the upstream naming bug this
        # avoids. The real output_name is restored below once DeeZy exits.
        deezy_name = _deezy_safe_name(output_name)
        deezy_path = config.outbound(deezy_name)
        # DeeZy names its per-job work folder after this same safe stem; the
        # post-failure cleanup finds the folder by this prefix.
        safe_prefix = deezy_name.rsplit(".", 1)[0]
        cmd = build_command(config, req, deezy_path)
        work_dir = config.local_work_dir()

        if not _ENCODE_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=503,
                detail="encoder busy (one encode at a time); retry with backoff",
            )
        started = time.monotonic()
        started_wall = time.time()
        log.info(
            "encode start: file=%s format=%s bitrate=%d track=%s",
            req.filename,
            req.format,
            req.bitrate_kbps,
            req.track_index,
        )
        try:
            # Reclaim stale DeeZy job folders BEFORE DeeZy runs, while holding
            # the single-encode lock, so a sweep can never delete the folder of
            # an in-flight encode (there is none -- the lock is ours).
            try:
                _sweep_work_dir(work_dir, config.temp_max_age_hours)
            except Exception as exc:  # noqa: BLE001 -- a sweep failure must not block the encode
                log.warning("pre-encode sweep failed: %s", exc)

            try:
                returncode, stderr = _run_deezy(cmd, config.timeout_seconds)
            except subprocess.TimeoutExpired:
                log.warning(
                    "encode timeout: file=%s after %ds; process tree killed",
                    req.filename,
                    config.timeout_seconds,
                )
                _cleanup_failed_job(work_dir, safe_prefix, started_wall)
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"DeeZy timed out after {config.timeout_seconds}s; "
                        "process tree killed"
                    ),
                )
            except FileNotFoundError:
                log.error(
                    "DeeZy executable not found (DEEZY_PATH=%s)", config.deezy_path
                )
                raise HTTPException(
                    status_code=500,
                    detail="DeeZy executable not found (check DEEZY_PATH)",
                )

            duration = time.monotonic() - started
            if returncode != 0:
                log.warning(
                    "encode failed: file=%s rc=%d duration=%.1fs stderr_tail=%s",
                    req.filename,
                    returncode,
                    duration,
                    stderr[-STDERR_LOG_TAIL:].replace("\n", " "),
                )
                # DeeZy only cleans its work folder on success; remove this
                # failed job's folder so it can never pile up and fill C:.
                _cleanup_failed_job(work_dir, safe_prefix, started_wall)
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "DeeZy failed with exit code "
                        f"{returncode}: {stderr[-STDERR_DETAIL_LIMIT:]}"
                    ),
                )
            if not os.path.isfile(deezy_path):
                log.warning(
                    "encode produced no output: file=%s rc=0 but %s missing",
                    req.filename,
                    deezy_name,
                )
                _cleanup_failed_job(work_dir, safe_prefix, started_wall)
                raise HTTPException(
                    status_code=500,
                    detail="DeeZy reported success but no output file was produced",
                )
            if deezy_path != output_path:
                os.replace(deezy_path, output_path)
            log.info(
                "encode ok: file=%s -> %s duration=%.1fs",
                req.filename,
                output_name,
                duration,
            )
            return EncodeResponse(
                status="ok",
                output_filename=output_name,
                duration_seconds=duration,
            )
        finally:
            _ENCODE_LOCK.release()

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("DEE_WRAPPER_HOST", "0.0.0.0"),  # nosec B104 -- LAN service behind scoped firewall rule
        port=int(os.environ.get("DEE_WRAPPER_PORT", DEFAULT_PORT)),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
