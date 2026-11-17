---
title: Immich
host: kdk-dkr-dmz-01
stack: immich
tier: dmz
status: live
storage: yes
secrets: [immich-postgres, authelia-app-secrets]
updated: 2026-11-16
tags: [homelab/stack, tier/dmz, status/live]
---

# Immich

Photo and video backup, LAN/tailnet only (router on the DMZ Traefik's `websecure` entrypoint only, not the Cloudflare tunnel; `IMMICH_TRUSTED_PROXIES=10.211.2.3/32` = that Traefik — see [[dmz-proxy]]); a fresh deploy (the k3s instance was unused), segmented on its own internal network.

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]] · [[identity]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Self-hosted photo backup with ML search |
| Status | 🟢 Live — web loads; mobile app reaches it off-LAN |
| Host | [[kdk-dkr-dmz-01]] |
| Endpoint | `https://immich.kmkdp.com` |
| Images | server + ML `v3.4.1`, valkey `9`, postgres `14-vectorchord` (digest-pinned) |
| Sign-in | Authelia OIDC (client `immich`, LLDAP group `immich-users`, 2FA) + local password login (break-glass) |
| Settings | repo-managed config file `stacks/immich/immich-config.json.tpl`; Administration → Settings is **read-only** |
| Runtime users | server + ML `1000:1000`, postgres + valkey `999:999` (all non-root, see [Hardening](#hardening)) |
| Deploy | Komodo stack `immich` (git-linked; pre_deploy chowns the library + model cache to 1000:1000, `op inject`s `.env`, then `render-config.sh` renders the config file) |
| Storage | library `/mnt/appdata/immich` (NFS); postgres `/opt/kdk-lab/immich/postgres` (VM disk, nightly dump) |
| Blast radius | No photo access/upload; nothing else affected (segmented) |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://immich.kmkdp.com/api/server/ping   # 200
ssh kdkadmin@192.168.191.20 'sudo docker exec immich-postgres pg_isready -U immich'               # accepting
ssh kdkadmin@192.168.191.20 'sudo docker ps --filter name=immich --format "{{.Names}} {{.Status}}"'   # 4x (healthy)
ssh kdkadmin@192.168.191.20 'sudo docker exec immich-server id -u; sudo docker exec immich-ml id -u'  # 1000, 1000
curl -s https://immich.kmkdp.com/api/server/features | python3 -m json.tool | grep -E '"(oauth|oauthAutoLaunch|passwordLogin|configFile)"'
#   "oauth": true, "oauthAutoLaunch": false, "passwordLogin": true, "configFile": true
ssh kdkadmin@192.168.191.20 'sudo docker exec immich-server ls -ln /config'
#   -r-------- 1 1000 1000 ... immich-config.json   (dir 0700, gitignored, host clone only)
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Server won't start | Postgres image mismatch (vectorchord/pgvecto.rs) | keep the digest-pinned `immich-app/postgres` image |
| ML search errors | ML container can't reach Hugging Face | `internal` network has egress; check `docker logs immich-ml` |
| Uploads fail | library NFS not mounted | `mountpoint /mnt/appdata` on the host |
| Uploads / thumbnails fail with `EACCES` | a file under the library not owned by 1000 (copied in by root) | `sudo find /mnt/appdata/immich ! -user 1000 \| head`; redeploy (pre_deploy re-chowns) |
| ML `Permission denied` on `/cache` | model cache not 1000:1000 | `stat -c %u:%g /opt/kdk-lab/immich/model-cache`; redeploy |
| Server restart-loops right after a config change | Immich rejected `IMMICH_CONFIG_FILE` (bad key or value; JSON syntax is caught before deploy) | `docker logs immich-server 2>&1 \| grep -i config`; fix the template or revert the PR and redeploy |
| Deploy fails in pre_deploy with `immich config invalid: ...` | `render-config.sh` validation refused the render; the running server and the old file are untouched | fix `immich-config.json.tpl`; `cd stacks/immich && sh render-config.sh --check` locally |
| `access_denied` from Authelia | the user is not in LLDAP group `immich-users` | add the group in `users.kmkdp.com` |
| OIDC `invalid_client` / login fails after the Authelia step | plaintext in 1P and the digest in Authelia's secrets file differ | re-hash the 1P plaintext into the digest file (see [[identity]]), redeploy identity |
| SSO user lands in the wrong account | Immich links an OIDC login to an existing user with the same e-mail | give the local account a different e-mail, or unlink in Administration → Users |
| `Read-only file system` in a log | the image started writing a new path outside the tmpfs list | add that path as a `tmpfs` (or a skip label with the reason) in `compose.yaml` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[dmz-proxy]] | ingress (LAN/tailnet) |
| Needs | `/mnt/appdata/immich` (NFS) | no library storage |
| Needs | [[identity]] (`sso.kmkdp.com`, reached over the internet like Tandoor/ABS) | no SSO login; password login still works |
| Needed by | — | none |

## Observability

No dashboard. `immich.kmkdp.com` probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

`immich-server:2283` joins both `internal` and `proxy-dmz`; ML, valkey, and postgres stay on `internal` only. ML keeps egress to Hugging Face for model downloads; no inbound is published except through the server.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/immich/compose.yaml` | Four-service segmented stack |
| `DB_*` / `REDIS_HOSTNAME` | internal service names |
| `/opt/kdk-lab/immich/model-cache` | ML model cache (owned 1000:1000) |
| `/opt/kdk-lab/immich/postgres` | PGDATA (owned 999, the image's postgres user) |
| `komodo/resources.toml` stack `immich` | pre_deploy: idempotent `find ... -exec chown 1000:1000` on the library + model cache, `op inject` `.env`, then `sh render-config.sh` |
| `stacks/immich/immich-config.json.tpl` | **all** admin settings (Immich `IMMICH_CONFIG_FILE`); keys not listed take Immich's defaults |
| `stacks/immich/render-config.sh` | renders the template (OIDC secret from 1P) to `config/immich-config.json` (gitignored, dir 0700 / file 0400, 1000:1000), validates it before swapping it in, appends `IMMICH_CONFIG_SHA` to `.env` |
| `./config:/config:ro` + `kdk.config-sha` label | directory mount (a single-file bind keeps the old inode); the label recreates the server when the rendered file changes |

### Settings in the config file

| Setting | Value |
|---|---|
| `oauth` | on; issuer `https://sso.kmkdp.com/.well-known/openid-configuration`, client `immich`, `client_secret_post`, RS256; button "Sign in with Authelia"; auto-register on; auto-launch off; mobile override off (Authelia has `app.immich:///oauth-callback` registered) |
| `oauth.storageLabelClaim` | `preferred_username` (= the LLDAP username) |
| `oauth.roleClaim` / `storageQuotaClaim` | claim names Authelia never issues, so no OIDC login can become admin or get a quota; `defaultStorageQuota` null (unlimited) |
| `passwordLogin` | on (break-glass for `kdkadmin`, the only admin) |
| `storageTemplate` | on, default pattern (`library/<label>/<year>/<year-month-day>/<file>`) |
| `map`, `newVersionCheck`, SMTP | off |
| `trash` | on, 30 days |

### Changing a setting

The admin UI cannot save settings while the config file is set. To change one: find the key
in Immich's [config file reference](https://docs.immich.app/install/config-file/), edit
`stacks/immich/immich-config.json.tpl` in a PR, run `cd stacks/immich && sh render-config.sh --check`
(CI runs it too), merge, and deploy `immich`. The new sha label recreates `immich-server`.

## Sign-in

- Web: **Sign in with Authelia** on the login page; Authelia requires 2FA and LLDAP group
  `immich-users`. First login creates the Immich user (storage label = LLDAP username, not admin).
- Password login stays on for `kdkadmin` (local admin, storage label `admin`).
- Mobile: server URL `https://immich.kmkdp.com`, then **Sign in with Authelia** (callback `app.immich:///oauth-callback`).
- Immich links an OIDC login to an existing user with the same e-mail. Keep the local
  `kdkadmin` e-mail different from every `immich-users` member's LLDAP e-mail.

## Hardening

Meets the [compose baseline](../reference/compose-baseline.md) with no skip or exception
labels (`x-hardened` anchor: `no-new-privileges`, `cap_drop: [ALL]`, `read_only`, `pids_limit`,
`mem_limit`, json-file 10m x 3, explicit healthcheck). Applied 2026-11-16, while the
instance was empty (1 admin, 0 assets).

| Service | User | Writable paths | Limits (mem / pids) | Healthcheck |
|---|---|---|---|---|
| `immich-server` | `1000:1000` | `/data` (library NFS), `/tmp` tmpfs (`HOME=/tmp`) | 4g / 1024 | `immich-healthcheck` (`/api/server/ping`) |
| `immich-machine-learning` | `1000:1000` | `/cache` (model cache), `/tmp` tmpfs (`HOME=/tmp`) | 4g / 512 | `python3 healthcheck.py` (`:3003/ping`) |
| `immich-valkey` | `999:999` | `/data` tmpfs (64m), `/tmp` | 256m / 128 | `valkey-cli ping` |
| `immich-postgres` | `999:999` | PGDATA bind; tmpfs `/etc/postgresql`, `/run/postgresql`, `/tmp` | 2g / 256 | image `healthcheck.sh` (pg_isready + checksum failures), every 30s |

- No capabilities are added for server, ML or valkey (unprivileged ports, no setuid).
  Postgres keeps `cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE, FOWNER]`, a no-op while it
  runs as 999; it is there so a rollback that deletes only `user:` still starts.
- `/etc/postgresql` is a tmpfs because the image entrypoint copies the SSD config template
  there on every start. Valkey's `/data` is a tmpfs because the image snapshots `dump.rdb`
  into its workdir; it only holds job queues, so a restart drops queued jobs (re-run them
  from Administration -> Jobs). A recreate already did that before hardening.
- The server waits for postgres and valkey to be healthy (`depends_on: service_healthy`).
- The appdata NFS export (`172.16.30.2:/mnt/tankz3/appdata`) does not squash root, so the
  pre_deploy chown works. If it ever squashes root, the chown fails and the deploy stops.
- Service/container `immich-postgres` and the `immich_internal` network names are fixed:
  the `backup-dmz` stack's `pgdump-immich` ([[backups]]) joins that network to dump the DB.

**Rollback:** revert the PR and redeploy. The 1000:1000 ownership left behind is harmless
for a root-run container.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `immich-postgres` | DB auth (server + ML + dump) | 🟢 self-service | rotate DB + item together, redeploy |
| `authelia-app-secrets` (`immich_oidc_plaintext`) | OIDC client secret (rendered into the config file; Authelia holds the pbkdf2 digest `immich_client_secret`) | 🟢 self-service | new plaintext → `op item edit` → hash it into the Authelia digest file (see [[identity]]) → redeploy identity, then immich (the sha label recreates the server); SSO is down between the two deploys, password login works |

## User Guide

Household users sign in with Authelia (they must be in `immich-users`); the first login creates their account. They install the Immich mobile app, point it at `immich.kmkdp.com`, sign in with Authelia and enable background backup.
