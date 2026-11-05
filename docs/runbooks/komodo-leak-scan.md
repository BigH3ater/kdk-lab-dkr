---
title: Komodo leak scan (count-only)
---

# Komodo leak scan (count-only)

Repeats the 2026-10-31 check after the Komodo "Compose Config" log leak
(upstream moghtech/komodo#1636; our fix is the `kdk-compose-config-redact`
wrapper, see [Komodo GitOps](../komodo-gitops.md)). The script
`scripts/komodo-leak-scan-counts.py` reads the Komodo Core database (FerretDB,
Mongo wire protocol) and prints **counts per collection only**: documents
scanned, `Compose Config` log stages, `<redacted>` markers, and secret-shaped
values in three classes. It never prints a value, a key name, a document id or
a stack name, so its output is safe to paste into a PR or note.

Run it after a Komodo upgrade, after adding an `op inject` stack, or whenever
the wrapper is changed or removed.

## What it counts

| Column | Meaning |
|---|---|
| `docs` | documents read (Update: only `start_ts >= --since` when given) |
| `compose_config_logs` | Update log stages named `Compose Config` |
| `redacted_markers` | `<redacted>` strings (the wrapper's output) |
| `assign` | `KEY=value` / `KEY: value` where the key says PASSWORD, SECRET, TOKEN, API_KEY, PASSKEY, CREDENTIAL, PRIVATE_KEY or AUTH_KEY and the value is 8+ chars and not a placeholder (`${...}`, `{{ op://... }}`, a `/path`, `<redacted>`, a boolean) |
| `known_token` | GitHub/Slack/AWS/Tailscale token prefixes, JWTs, PEM private-key headers |
| `url_credential` | `scheme://user:password@host` |
| `docs_with_hits` | documents with at least one secret-shaped value |

Exit status: `0` none found, `1` at least one secret-shaped value, `2` error.
Expected today: `0`, with `redacted_markers` growing with every deploy.
`assign` can false-positive on prose (`token: expired-session`); a non-zero
count is a reason to look in the Komodo UI, not proof of a leak.

Read-only: it only runs `find()` with projections. It uses the Komodo DB
login because there is no read-only DB role; treat it like any admin login.

## Run it (from the laptop, against kdk-dkr-01)

The script ships in Komodo's repo checkout on dkr-01
(`/etc/komodo/repos/kdk-lab-dkr/scripts/`, updated on every sync). It runs in a
throwaway Python container on the `komodo_default` network, where `ferretdb`
resolves. The username and password go over ssh **stdin** as two lines, never
on a command line:

```sh
{ op read op://kdk-cluster/komodo-db/username
  op read op://kdk-cluster/komodo-db/password; } |
ssh kdkadmin@10.1.20.20 'sudo -n docker run -i --rm --network komodo_default \
  -v /etc/komodo/repos/kdk-lab-dkr/scripts/komodo-leak-scan-counts.py:/scan.py:ro \
  python:3.13-alpine sh -c "pip install -q --disable-pip-version-check pymongo >/dev/null && python -I /scan.py --since 2026-10-31"'
```

`--network container:komodo-core-1` also works (it shares Core's namespace,
which is on `komodo_default`). Drop `--since` to scan the whole Update history
(the 2026-10-31 cleanup pulled every pre-fix Compose Config log, so the full
scan should also be 0). Add `--json` for machine-readable counts.

If `op read` is unavailable, stop: do not copy the DB password out of
`/etc/komodo` or the rendered `.env` on the host.

The Komodo API is not an alternative for this check: reading update logs
through it returns the same secret-bearing text to the caller, which is exactly
what this script avoids.

## Self-test (no DB, no network)

**Laptop, repo root**

```sh
python3 scripts/komodo-leak-scan-counts.py --fixture scripts/tests/fixtures/komodo-leak-scan-fixture.json
python3 -m unittest discover -s scripts/tests -p 'test_komodo_leak*'
```

The fixture holds fake values only; the first command exits `1` with
`secret-shaped values total: 4`.
