---
title: PostgreSQL major upgrade (16 → 18)
tags: [homelab/process, postgres, komodo, backups]
updated: 2026-10-30
---

# PostgreSQL major upgrade (16 → 18)

A PostgreSQL **major** bump is never an in-place tag change: the on-disk cluster
format changes between majors, so starting an 18 server on a 16 data directory
fails with `database files are incompatible with server` and the container
**crash-loops**. Because a merge to `main` deploys within 15 minutes through
`gitops-sync` and **any image-string change recreates the container**
([[komodo-gitops]]), a bare tag bump on a DB server is a planned outage waiting
to happen. This runbook does the migration as a **logical dump → fresh init →
restore**, per stack, in an attended window.

This plan unblocks the Renovate rule that holds postgres majors back for the
three DB-server stacks (see [Re-enable the Renovate rule](#6-re-enable-the-renovate-rule)).

**Scope.** Three stacks own a PostgreSQL *server* that this runbook migrates:

| Stack | Host | DB container | db / user | Data dir (host) | Stack network | pgdump dir (host) |
|---|---|---|---|---|---|---|
| [[jellystat]] | kdk-dkr-01 `10.1.20.20` | `jellystat-db` | `jfstat` / `jellystat` | `/opt/kdk-lab/jellystat/postgres` | `jellystat_default` | `/mnt/backup/dkr/kdk-dkr-01/pgdump/` |
| [[tandoor]] | kdk-dkr-dmz-01 `192.168.191.20` | `tandoor-db` | `tandoor` / `tandoor` | `/opt/kdk-lab/tandoor/postgres` | `tandoor_default` | `/mnt/backup/dkr/kdk-dkr-dmz-01/pgdump/` |
| [[identity]] | kdk-dkr-dmz-01 `192.168.191.20` | `authelia-postgres` | `authelia` / `authelia` | `/opt/kdk-lab/identity/postgres` | `identity_default` | `/mnt/backup/dkr/kdk-dkr-dmz-01/pgdump/` |

**Order (low blast radius first):** `jellystat` → `tandoor` → `identity`. Do one
stack per window; verify it fully before the next.

> **Immich is out of scope.** `stacks/immich` runs
> `ghcr.io/immich-app/postgres:14-vectorchord…` (VectorChord / pgvecto.rs on
> PG14), pinned to Immich's own release compose with Renovate bumps disabled
> ([[komodo-gitops]] §Dependency updates). It is **not** a stock `postgres`
> image and must track Immich's upstream DB, not the community postgres major.
> Do not touch it here.

**Why logical dump/restore (not `pg_upgrade`).** A dump → restore rebuilds every
index on the new server, so the glibc/ICU **collation-version** change between
the 16-alpine and 18-alpine base images cannot silently corrupt text indexes —
the failure mode of a binary `pg_upgrade` or an OS libc bump. It is also the only
method that works cleanly across the container recreate that GitOps forces.

---

## 0. One-time facts and prerequisites

- **The pg_dump client is already PG18.** The backup sidecars in
  `stacks/backup-dkr-01` (`pgdump-jellystat`) and `stacks/backup-dmz`
  (`pgdump-authelia`, `pgdump-tandoor`) already run `postgres:18.6-alpine`
  against these 16 servers nightly (a newer client dumping an older server is
  supported). So the 18 client ↔ 16 server dump path on each host is **proven in
  production** — you are reusing it, not inventing it. See [[backups]].
- **PGDATA default changed in PG18 — the critical gotcha.** The official
  `postgres:18` image moved its default `PGDATA` to `/var/lib/postgresql/18/docker`
  and its declared `VOLUME` to `/var/lib/postgresql` (16 used
  `/var/lib/postgresql/data`). Our three stacks all bind-mount the host dir at
  **`/var/lib/postgresql/data`**. If you bump the tag without pinning PGDATA, PG18
  initialises a *new empty* cluster at `/var/lib/postgresql/18/docker` **inside an
  anonymous volume**, the bind mount stays empty, and the app comes up on a blank
  database — silent data loss, not a crash. **Every 18 tag bump in this runbook
  also adds `PGDATA: /var/lib/postgresql/data`** to the db service so the existing
  bind mount stays authoritative.
  (docker-library/docs#2582, hub.docker.com/_/postgres.)
- **These DB roles are superusers.** The official image makes `POSTGRES_USER` a
  superuser and creates the role + `POSTGRES_DB` from the stack `.env` on first
  init, so a single-database `pg_dump`/`pg_restore` (no globals) is sufficient —
  you do not need `pg_dumpall`.
- **Pick the target tag** from the Dependency Dashboard once the Renovate rule is
  re-enabled, or set it by hand. Use the current 18 alpine patch
  (e.g. `postgres:18.6-alpine`, digest-pinned to match the sidecars) so the
  server and the dump client are the same build.
- **Keep locale/encoding identical.** None of the three set
  `POSTGRES_INITDB_ARGS`, so the fresh 18 cluster initialises with the alpine
  image defaults (UTF-8), same as the 16 cluster. Do **not** add initdb args
  during the migration.
- **Secrets never printed.** The stack `.env` (rendered by the Komodo
  `op inject` pre_deploy) already holds the DB password on the host. The one-off
  client containers below read it via `--env-file` and reference it as a shell
  variable inside the container — it never reaches your screen. `op` runs on the
  laptop only ([rotate-a-secret](rotate-a-secret.md)).

**Clone paths** (where `docker compose` on the host sees the stack's rendered
compose + `.env`; see [[komodo-gitops]]):

- kdk-dkr-01 (periphery `stack_dir` = `/opt/kdk-lab/stacks`): jellystat →
  `/opt/kdk-lab/stacks/jellystat/stacks/jellystat/`
- kdk-dkr-dmz-01 (older periphery default `/etc/komodo/stacks`): tandoor →
  `/etc/komodo/stacks/tandoor/stacks/tandoor/`, identity →
  `/etc/komodo/stacks/identity/stacks/identity/`

Confirm the exact path on the host before you lean on it:
`ssh kdkadmin@<host> 'sudo -n ls -d /opt/kdk-lab/stacks/<stack>/stacks/<stack> /etc/komodo/stacks/<stack>/stacks/<stack> 2>/dev/null'`.

**Expected window / data volumes** (gzipped nightly dump sizes on 2026-10-30):
`jellystat` ≈ 1.1 MB, `tandoor` ≈ 0.1 MB, `authelia` ≈ 0.3 MB. The databases are
tiny; the dump and restore are seconds. Budget the window for the attended deploy
+ verify, not the data: **~20–30 min** for jellystat and tandoor each, **~45 min
(attended, announced)** for identity because SSO is down for the whole lab while
it runs.

---

## The per-stack sequence

The steps are identical for all three; the per-stack table at the bottom fills in
the names. Placeholders: `<host>`, `<net>` (stack network), `<dbc>` (db
container), `<db>`, `<user>`, `<datadir>`, `<dumpdir>`, `<clone>` (clone dir),
`<dbsvc>` (db compose service), `<appsvc>` (app compose service),
`<PWVAR>` (the `.env` key holding the DB password).

### 1. Pre-checks (nothing changes yet)

```bash
ssh kdkadmin@<host> 'sudo -n docker ps --filter name=<dbc> --format "{{.Names}} {{.Image}} {{.Status}}"'   # 16.x-alpine, healthy
ssh kdkadmin@<host> 'sudo -n docker exec <dbc> pg_isready -U <user> -d <db>'                               # accepting connections
# a recent, valid nightly dump exists (belt-and-braces; step 2 takes a fresh one anyway)
ssh kdkadmin@<host> 'sudo -n ls -t <dumpdir>/<db>-*.sql.gz 2>/dev/null | head -1'
```

Announce the window. For **identity**, tell the household SSO is down; for
tandoor/jellystat the single service is down.

### 2. Fresh pre-migration dump with the PG18 client

Taken live against the running 16 server, custom format (`-Fc`) so `pg_restore`
can run it and so you can list it to prove it is complete. Runs on the stack
network, as root inside the one-off container, writing to the host pgdump dir.

```bash
ssh kdkadmin@<host> 'DUMP=<dumpdir>/<db>-pre18-$(date +%F).dump; \
  sudo -n docker run --rm --network <net> \
    --env-file <clone>/.env \
    -v <dumpdir>:/dest \
    postgres:18.6-alpine \
    sh -c "PGPASSWORD=\$<PWVAR> pg_dump -h <dbc> -U <user> -Fc -f /dest/$(basename "$DUMP") <db>" && \
  sudo -n docker run --rm -v <dumpdir>:/dest postgres:18.6-alpine \
    pg_restore --list /dest/$(basename "$DUMP") | head'
```

The `--list` output (table/sequence/index entries) proves the dump is readable
and non-empty **before** you take the live DB down. Note the dump filename.

### 3. Stop the stack and move the 16 data dir aside

```bash
cd <clone>
sudo docker compose stop <appsvc> <dbc-service-or-dbsvc>   # app first, then db; no writers now
# retire the 16 cluster (NEVER delete it — this is the rollback)
sudo mv <datadir> <datadir>.pg16-$(date +%F)
sudo mkdir <datadir>
# identity ONLY: the postgres service runs as user "70:70", so the fresh dir must be owned 70
sudo chown 70:70 <datadir>      # identity only; omit for jellystat/tandoor
```

### 4. Merge the per-stack tag-bump PR, then deploy by hand (attended)

Each stack gets its **own small PR** (one `compose.yaml`) that:

- changes the db image to `postgres:18.6-alpine@sha256:…` (digest-pinned), and
- adds to the **db service** environment:
  ```yaml
  environment:
    PGDATA: /var/lib/postgresql/data   # PG18 default moved; keep our bind mount authoritative
  ```
  (identity already has `POSTGRES_*` under `environment:`; add `PGDATA` there.
  jellystat's `jellystat-db` and tandoor's `db` set env via `env_file`/literals —
  add an `environment:` block with `PGDATA`.)

Merge it, then **do not wait for the 15-minute loop** — deploy immediately so the
work is attended:

- Komodo UI → **Stacks → `<stack>` → Deploy** (or Komodo → Procedures → Execute
  `gitops-sync`). Komodo pulls HEAD into `<clone>`, re-renders `.env`, and runs
  `docker compose up -d`.

PG18 runs `initdb` into the now-empty bind mount, creates `<user>` + `<db>` from
the stack `.env`, and the app starts against a **blank** database. That is
expected — the restore in step 5 replaces it. Confirm the db is 18 and healthy:

```bash
ssh kdkadmin@<host> 'sudo -n docker exec <dbc> postgres --version'     # 18.x
ssh kdkadmin@<host> 'sudo -n docker exec <dbc> pg_isready -U <user> -d <db>'
```

### 5. Restore into the fresh 18 server (app stopped)

Stop only the app so nothing writes, then drop + recreate the database to clear
anything the app wrote in the seconds it was up, and restore the pre-18 dump.
Drop/recreate makes the restore clean regardless of what the app touched.

```bash
cd <clone>
sudo docker compose stop <appsvc>
DUMP=<dumpdir>/<db>-pre18-<date>.dump      # the file from step 2
sudo docker run --rm --network <net> \
  --env-file <clone>/.env \
  -v <dumpdir>:/dest \
  postgres:18.6-alpine sh -c '
    PGPASSWORD=$<PWVAR>
    export PGPASSWORD
    # connect to the maintenance db to drop/recreate the app db (app is stopped, no sessions)
    psql -h <dbc> -U <user> -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS <db>;"
    psql -h <dbc> -U <user> -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE <db> OWNER <user>;"
    pg_restore -h <dbc> -U <user> --no-owner --no-acl -d <db> /dest/'"$(basename "$DUMP")"'
  '
```

Then bring the app back:

```bash
sudo docker compose up -d <appsvc>
```

- **identity:** on start Authelia runs its storage migrations; the restored
  schema is already at the running Authelia version, so this is a no-op. The
  Authelia **storage encryption key is unchanged** (same DB contents, same key in
  1Password) so all TOTP/WebAuthn secrets stay valid — the key↔DB pairing
  ([[identity]]) is preserved because you restored the same data.
- **tandoor:** Django runs `migrate` on start; restored schema is current → no-op.
- **jellystat:** the node backend checks/creates its schema on start → no-op.

### 6. Verify

```bash
# jellystat
curl -s -o /dev/null -w '%{http_code}\n' https://jellystat.kmkdp.com/   # 302→sso then 200 after login; dashboard shows history
ssh kdkadmin@10.1.20.20 'sudo -n docker exec jellystat-db psql -U jellystat -d jfstat -c "\dt" | head'

# tandoor
curl -s -o /dev/null -w '%{http_code}\n' https://recipes.kmkdp.com/     # 200/302; log in, recipes + meal plan present

# identity (do this one first and carefully)
curl -s https://sso.kmkdp.com/api/health                                # {"status":"OK"}
ssh kdkadmin@192.168.191.20 'sudo -n docker exec authelia-postgres pg_isready -U authelia'
# then log in at sso.kmkdp.com with an existing user + TOTP (TOTP must still work),
# and load one gated app (e.g. https://radarr.kmkdp.com/) to confirm ForwardAuth.
```

Confirm Komodo shows the stack **Deployed at HEAD** (step 4's hand deploy already
recorded it, so the 15-minute loop will leave it alone). If you deployed via
`gitops-sync`, nothing more is needed.

### 7. Clean up (after a retention window)

Keep `<datadir>.pg16-<date>` until the service has run clean for a few days, then
remove it. No `rm -f`:

```bash
ssh kdkadmin@<host> 'sudo -n du -sh <datadir>.pg16-<date>'   # confirm it is the old one
ssh kdkadmin@<host> 'sudo rm -r <datadir>.pg16-<date>'
```

---

## Rollback (any step, before cleanup)

The 16 data dir is untouched at `<datadir>.pg16-<date>`, so rollback is a swap +
revert:

```bash
cd <clone>
sudo docker compose stop <appsvc> <dbc-service-or-dbsvc>
sudo mv <datadir> <datadir>.pg18-failed-$(date +%F)
sudo mv <datadir>.pg16-<date> <datadir>
```

Then **revert the tag-bump PR** (restore the `postgres:16.15-alpine` image line
and remove the `PGDATA` env) and Deploy the stack in Komodo. PG16 starts on its
original cluster; no data was lost because the restore only ever wrote to the
fresh 18 dir. For identity, the storage key still matches the (original) DB, so
logins and 2FA are intact.

---

## Per-stack placeholder values

| Placeholder | jellystat | tandoor | identity |
|---|---|---|---|
| `<host>` | `kdkadmin@10.1.20.20` | `kdkadmin@192.168.191.20` | `kdkadmin@192.168.191.20` |
| `<net>` | `jellystat_default` | `tandoor_default` | `identity_default` |
| `<dbc>` | `jellystat-db` | `tandoor-db` | `authelia-postgres` |
| `<db>` / `<user>` | `jfstat` / `jellystat` | `tandoor` / `tandoor` | `authelia` / `authelia` |
| `<datadir>` | `/opt/kdk-lab/jellystat/postgres` | `/opt/kdk-lab/tandoor/postgres` | `/opt/kdk-lab/identity/postgres` |
| `<dumpdir>` | `/mnt/backup/dkr/kdk-dkr-01/pgdump` | `/mnt/backup/dkr/kdk-dkr-dmz-01/pgdump` | `/mnt/backup/dkr/kdk-dkr-dmz-01/pgdump` |
| `<clone>` | `/opt/kdk-lab/stacks/jellystat/stacks/jellystat` | `/etc/komodo/stacks/tandoor/stacks/tandoor` | `/etc/komodo/stacks/identity/stacks/identity` |
| `<appsvc>` | `jellystat` | `web` | `authelia` |
| db compose service | `jellystat-db` | `db` | `postgres` |
| `<PWVAR>` (`.env` key) | `POSTGRES_PASSWORD` | `POSTGRES_PASSWORD` | `AUTHELIA_POSTGRES_PASSWORD` |
| fresh-dir chown | — | — | `chown 70:70` |
| extra care | internal only | own auth, DMZ | **SSO — lab-wide outage; go last, announce** |

---

## Re-enable the Renovate rule

`renovate.json` holds a packageRule that disables `postgres` **majors** for
`stacks/{identity,jellystat,tandoor}/**` with the note "No PR until the plan
exists". Once all three stacks are on 18 and verified, in a separate PR set that
rule's `"enabled": false` → `"enabled": true` and update its `description` to
point at this runbook, e.g.:

```jsonc
{
  "description": "postgres DB servers (identity, jellystat, tandoor): a major is a dump/restore — follow docs/runbooks/postgres-major-upgrade.md. Majors still need a Dependency Dashboard tick (global majors rule); minors/patches/digests flow normally.",
  "matchDatasources": ["docker"],
  "matchPackageNames": ["postgres", "docker.io/postgres"],
  "matchFileNames": ["stacks/identity/**", "stacks/jellystat/**", "stacks/tandoor/**"],
  "matchUpdateTypes": ["major"],
  "enabled": true
}
```

With `enabled: true` these majors fall back under the global majors rule
(`dependencyDashboardApproval: true`), so the next postgres major (18 → 19) still
waits for a human to tick the Dependency Dashboard **and** to run this runbook —
it never auto-PRs onto a live DB server. Do **not** add `postgres` to the
`backup-*` exception (those are the pg_dump client only). Leave Immich's
`ghcr.io/immich-app/postgres` rule as-is.

---

## Gotchas recap

- **PGDATA default moved in PG18** (`/var/lib/postgresql/18/docker`). Pin
  `PGDATA: /var/lib/postgresql/data` on every migrated db service or the bind
  mount is bypassed and the app comes up on an empty DB.
- **Crash-loop on a 16 dir.** Never let an 18 image start on the 16 cluster —
  always move the dir aside and let 18 `initdb` fresh, then restore. A bare tag
  bump that GitOps deploys in 15 min is exactly this failure.
- **Collation/ICU.** Logical restore rebuilds indexes, so the libc/ICU collation
  change across base images is harmless here — but it is the reason to use
  dump/restore and *not* a binary `pg_upgrade`.
- **Authelia key↔DB pairing.** You restore the same data, so the storage
  encryption key still matches; do not rotate it during the migration or all 2FA
  dies ([[identity]]).
- **Tandoor env.** `POSTGRES_DB`/`POSTGRES_USER` are literals (`tandoor`) in
  `.env.tpl`; the password is `op://kdk-cluster/authelia-app-secrets/tandoor_postgres_password`.
  The fresh 18 init uses these, so the restore targets the same db/user.
- **One PR per stack.** Three separate tag-bump PRs merged one window at a time,
  not one combined PR — so a problem on jellystat never blocks tandoor/identity.
