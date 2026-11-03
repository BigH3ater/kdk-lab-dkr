# dee-wrapper

FastAPI wrapper around **DeeZy / the Dolby Encoding Engine** that encodes
TrueHD / DTS-HD MA lossless audio to Dolby DD+ (`.ec3`) for the Tdarr media
pipeline. It runs **on the Windows DEE VMs** (`kdk-dee-01` = `10.1.1.130`,
`kdk-dee-02` = `10.1.1.131`), not in the Docker/Komodo cluster — DEE's TrueHD
support is Windows-only, so there is deliberately no Dockerfile or compose
stack for it.

> **Operations and site-specific values live in the runbook**, not here:
> [`docs/runbooks/windows-vm-dee.md`](../../docs/runbooks/windows-vm-dee.md)
> (health checks, SSH/PowerShell access, failure modes, scheduled task) and
> [`docs/runbooks/dee-second-instance.md`](../../docs/runbooks/dee-second-instance.md)
> (the two-VM + `dee-lb` layout). Config is env-only; no hostname, IP, or
> secret is baked into this code.

This directory is the **git-tracked source of truth** for the VM's
`C:\dee-wrapper\main.py`. The two VMs ran an identical, untracked copy until it
was brought into the repo on 2026-11-03. Deploy by `scp` (see the runbook and
the PR body) — there is no build/CI deploy path for this Windows service.

> `run.cmd` (the VM launcher that mounts the SMB exchange, then starts python)
> is **intentionally not in the repo**: it carries an inline SMB credential.
> Keep it on the VM only.

## What it does

- `GET /healthz` — liveness + pre-flight state (exchange mount reachable, free
  space on the exchange and on DeeZy's local work drive vs `min_free_gb`, and
  the count/size of leftover DeeZy work folders: `temp_dirs` / `temp_gb`).
- `POST /encode` — bearer-auth; resolves a bare filename under the exchange
  `inbound/`, runs DeeZy, writes the `.ec3` to `outbound/`. One encode at a
  time (a concurrent caller gets `503`, retryable per the Tdarr insertion
  contract).

## DeeZy temp-folder hygiene (2026-11-03)

DeeZy creates a per-job folder under its work dir
(`%LOCALAPPDATA%\deezy\<SafeName>_<8hex>_t0[_<4hex>]`) and deletes it **only on
success**. A failed or killed encode leaves the folder (and its multi-GB
intermediates) behind, and the wrapper never cleaned them up — 84 folders
(55 GB) once pinned `C:` at the `min_free_gb` floor, after which every encode
failed the pre-flight and `kdk-dee-01` paged hourly as `degraded` / low disk.

This wrapper now:

- **Sweeps** stale DeeZy job folders (older than
  `DEE_WRAPPER_TEMP_MAX_AGE_HOURS`, default 6 h) at startup and before every
  encode, while holding the single-encode lock so an in-flight job's folder is
  never touched. Only directories directly under the work dir whose names match
  DeeZy's job-folder pattern are ever removed.
- **Cleans up** the specific job folder left behind by a failed/killed encode.
- **Logs** to a rotating file (`<logs dir>\wrapper.log`, ~5 MB × 5): each
  encode's start/end, exit code, duration, a short stderr tail on failure,
  kill/timeout events, and the sweep's removed folders and bytes freed. The log
  never records the bearer token or request headers.

## Config (environment, machine env on the VM)

| Var | Default | Meaning |
|---|---|---|
| `DEE_WRAPPER_TOKEN` | *(required)* | bearer token `/encode` callers must present |
| `DEE_EXCHANGE_ROOT` | *(required)* | Windows path of the shared exchange volume |
| `DEEZY_PATH` | `deezy` | DeeZy executable |
| `DEE_WRAPPER_TIMEOUT_SECONDS` | `7200` | per-encode subprocess budget |
| `DEE_WRAPPER_MIN_FREE_GB` | `20` | pre-flight free-space floor (both drives) |
| `DEE_WRAPPER_WORK_DIR` | `%LOCALAPPDATA%\deezy` | DeeZy work drive override |
| `DEE_WRAPPER_TEMP_MAX_AGE_HOURS` | `6` | sweep folders idle longer than this |
| `DEE_WRAPPER_LOG_DIR` | `<this dir>\logs` | rotating-log directory |
| `DEE_WRAPPER_HOST` / `DEE_WRAPPER_PORT` | `0.0.0.0` / `8484` | bind address / port |
| `DEEZY_EXTRA_ARGS` | *(empty)* | extra DeeZy CLI args |

## Tests

Offline, with `uv` (matches `pyproject.toml`; needs Python 3.14 + FastAPI):

```sh
cd tools/dee-wrapper
uv run --with 'fastapi[standard-no-fastapi-cloud-cli]>=0.115' --with 'pytest>=8.3' --with httpx \
  pytest -q
```

The suite uses temp dirs (no real DeeZy, no VM): it covers the sweep (old vs
fresh vs non-matching names), the post-failure cleanup selection, and — via a
mocked subprocess through `TestClient` — that the pre-encode sweep only ever
runs while the single-encode lock is held.
