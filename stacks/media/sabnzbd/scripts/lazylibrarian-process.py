#!/usr/bin/env python3
# ruff: noqa
"""
SABnzbd -> LazyLibrarian post-processing trigger.

Assigned to SABnzbd's `lazylibrarian` category (Config -> Categories ->
lazylibrarian -> Script), with the Scripts Folder set to /scripts
(Config -> Folders -> Scripts Folder). When a job in that category finishes,
SABnzbd runs this script. On SUCCESS it calls LazyLibrarian's API:

    GET {LL_URL}/api?apikey=<key>&cmd=forceProcess

so the completed download is imported immediately.

Why this exists: LazyLibrarian only schedules its `PostProcessor` job while
something is Snatched, and it can fail to re-add that job after a snatch. On
2026-11-03 `showJobs` listed no PostProcessor and a completed download
(*Mavericks*, category `lazylibrarian`, in /data/media/staging/lazylibrarian)
sat ~25 min until `forceProcess` was called by hand. This event-driven trigger
removes that wait: SABnzbd, not LazyLibrarian's scheduler, kicks the import.

Both containers share the media stack `default` network, so LazyLibrarian is
reachable at its service name -- `http://lazylibrarian:5299` -- exactly as the
compose file has the arr services address each other (e.g. Prowlarr points at
`http://lazylibrarian:5299`). LL_URL carries that base URL.

SABnzbd passes job info on argv AND in SAB_* environment variables. The
post-processing status is `SAB_PP_STATUS` (positional argv[7]); `0` = OK (1 =
failed verification, 2 = failed unpack, 3 = both, -1 = failed post-processing).

  * status 0: call forceProcess now, so the completed download is imported.
  * status != 0: LazyLibrarian must still hear about it, or the book stays
    Snatched forever (A Clash of Kings, 2026-11-03: "RAR files failed to
    verify", AudioStatus stuck at Snatched). LazyLibrarian has no "mark
    failed" API, but every forceProcess run ends with its download-status pass
    (postprocess._manage_download_status): it looks the job up in SABnzbd's
    history, sees status Failed, marks the snatch Aborted -> Failed and sets
    the book back to Wanted, so the next search tries another release.
    SABnzbd only writes the history row AFTER this script exits, so an
    immediate call would find nothing. We therefore fork a detached child that
    waits FAIL_DELAY seconds and then calls forceProcess; this script itself
    returns at once.

Design rules (match the sibling hook, lazylibrarian/hooks/abs-notify.py):
  * Standard library only (urllib); the stock LinuxServer image needs no extra
    packages.
  * LazyLibrarian answers HTTP 200 even for API errors, with a JSON body like
    {"Success": false, "Error": {"Code": 405, ...}}. Such a reply is a failure
    (not retried for 4xx-style codes), not "requested (HTTP 200)".
  * Retry a transient forceProcess failure up to 3 times; a 401/403 (bad key)
    is not retried.
  * ALWAYS exit 0 -- a trigger error must never fail SABnzbd's post-processing
    or mark the job failed (SABnzbd treats a non-zero script exit as a failed
    job). A failed download keeps the status SABnzbd already gave it.
  * Print exactly one summary line. SABnzbd records a script's LAST stdout line
    in its history, so the final line is the status the owner sees.
  * Never print the API key (the request URL carries it, so the URL is never
    logged).

Environment (set on the sabnzbd service in stacks/media/compose.yaml):
  LL_URL       base URL of LazyLibrarian, e.g. http://lazylibrarian:5299
  LL_API_KEY   LazyLibrarian API key (SECRET; from /etc/kdk/sab-ll.env, op-read)
Optional:
  LL_FAIL_DELAY  seconds the failure child waits before forceProcess (default 90)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CMD = "forceProcess"
RETRIES = 3
TIMEOUT = 15
FAIL_DELAY = 90


def job_status(argv, env):
    """Post-processing status: SAB_PP_STATUS, else positional argv[7]. 0 = OK.
    Returns None if neither is present/parseable (treated as 'not OK')."""
    val = env.get("SAB_PP_STATUS")
    if val is None or str(val).strip() == "":
        val = argv[7] if len(argv) > 7 else None
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def reply_error(body):
    """Return an error string if LazyLibrarian's reply body reports failure.

    The API answers HTTP 200 for errors too, e.g.
    {"Success": false, "Error": {"Code": 405, "Message": "..."}}. A plain-text
    "OK" (or any non-JSON / JSON without Success=false) is success."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("Success") is False:
        err = data.get("Error") or {}
        if isinstance(err, dict):
            code = err.get("Code", "?")
            msg = str(err.get("Message", "")).strip()
            return f"LazyLibrarian error {code}" + (f": {msg}" if msg else "")
        return f"LazyLibrarian error: {err}"
    return None


def force_process(base, key):
    """GET {base}/api?apikey=<key>&cmd=forceProcess.

    Returns (ok, detail). Retries a transient failure up to RETRIES times;
    401/403 (bad key) and an API-level error reply are not retried. The request
    URL contains the key and is therefore never returned or logged."""
    base = base.rstrip("/")
    query = urllib.parse.urlencode({"apikey": key, "cmd": CMD})
    url = f"{base}/api?{query}"  # contains the key -> never logged
    last = "no attempt made"
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(4096)
                if isinstance(body, bytes):
                    body = body.decode("utf-8", "replace")
                err = reply_error(body)
                if err:
                    # A wrong key or a disabled API is not going to fix itself.
                    return False, f"HTTP {resp.status} but {err} (not retried)"
                return True, f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (401, 403):
                return False, f"{last} (bad API key; not retried)"
        except Exception as e:  # noqa: BLE001 - any transport error is retryable
            last = type(e).__name__
        if attempt < RETRIES:
            time.sleep(2 * attempt)
    return False, f"{last} after {RETRIES} attempts"


def fail_delay(env):
    try:
        return max(0, int(env.get("LL_FAIL_DELAY", FAIL_DELAY)))
    except (TypeError, ValueError):
        return FAIL_DELAY


def schedule_failed_followup(base, key, delay):
    """Fork a detached child that waits `delay` s, then calls forceProcess so
    LazyLibrarian reads the (by then written) SABnzbd history row, marks the
    snatch Failed and returns the book to Wanted.

    Returns True if the child was started. The parent never waits for it, so
    SABnzbd's post-processing is not held up; the child's output goes nowhere
    (SABnzbd only keeps the parent's last line)."""
    try:
        pid = os.fork()
    except OSError:
        return False
    if pid:
        return True
    # Child: detach from SABnzbd's process group and stdio, then run.
    try:
        os.setsid()
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(devnull, fd)
        time.sleep(delay)
        force_process(base, key)
    except Exception:  # noqa: BLE001
        pass
    finally:
        os._exit(0)


def main(argv, env):
    status = job_status(argv, env)
    name = (
        env.get("SAB_FINAL_NAME")
        or (argv[3] if len(argv) > 3 and argv[3] else "")
        or "?"
    )
    cat = env.get("SAB_CAT") or (argv[5] if len(argv) > 5 and argv[5] else "") or "?"

    base = (env.get("LL_URL") or "").strip()
    key = (env.get("LL_API_KEY") or "").strip()
    if not base or not key:
        print(
            f"lazylibrarian-process: {name!r} status={status}; LL_URL / LL_API_KEY "
            "not both set; skipping forceProcess (is /etc/kdk/sab-ll.env present?)"
        )
        return

    if status != 0:
        delay = fail_delay(env)
        if schedule_failed_followup(base, key, delay):
            print(
                f"lazylibrarian-process: {name!r} (cat={cat}) status={status}; "
                f"download failed, forceProcess in {delay}s so LazyLibrarian "
                f"marks it Failed and the book returns to Wanted"
            )
        else:
            print(
                f"lazylibrarian-process: {name!r} (cat={cat}) status={status}; "
                f"download failed and the follow-up could not be started; "
                f"LazyLibrarian's own PostProcessor will catch it"
            )
        return

    ok, detail = force_process(base, key)
    if ok:
        print(f"lazylibrarian-process: forceProcess requested for {name!r} ({detail})")
    else:
        print(f"lazylibrarian-process: forceProcess FAILED for {name!r}: {detail}")


if __name__ == "__main__":
    try:
        main(sys.argv, os.environ)
    except Exception as e:  # noqa: BLE001
        print(f"lazylibrarian-process: unexpected error: {type(e).__name__}: {e}")
    sys.exit(0)  # never fail SABnzbd post-processing
