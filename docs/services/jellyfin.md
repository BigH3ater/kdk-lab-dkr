---
title: Jellyfin
host: kdk-dkr-dmz-01
stack: jellyfin
tier: dmz
status: live
storage: yes
secrets: [jellyfin-oidc]
updated: 2026-10-30
tags: [homelab/stack, tier/dmz, status/live]
---

# Jellyfin

Media server on the DMZ node with Intel Arc A380 QSV transcode; public via the Cloudflare tunnel with its own login (LDAP plugin, plus Authelia SSO on the web UI via the SSO Authentication plugin), no ForwardAuth.

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]] · [[media]] · [[seerr]] · [[identity]] · [[jellyfin-sso]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Streaming server for the TV/movie library |
| Status | 🟢 Live — login + playback work; plugins Active |
| Host | [[kdk-dkr-dmz-01]], `/dev/dri` mapped (i915) |
| Endpoint | `https://jellyfin.kmkdp.com` (LAN + Cloudflare tunnel) |
| Edge | Five-rule Cloudflare design (admin-API block, anonymous-stream block, OIDC skip, bots, non-US) plus a credential-path rate limit; verify with `scripts/edge-probe.sh` |
| Image | `ghcr.io/linuxserver/jellyfin:12.3ubu2604-ls56` — 12.x line, pinned; server and plugins move together |
| Deploy | Komodo stack `jellyfin` (git-linked) |
| Storage | `/opt/kdk-lab/jellyfin/config` on local disk (DB, cache, plugins; byte-for-byte from k3s); images/metadata on NFS `/mnt/appdata/jellyfin/metadata` → `/config/data/metadata`; library RO from `/mnt/media` |
| Blast radius | No streaming; requests via Seerr still queue |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://jellyfin.kmkdp.com/health   # 200
docker exec jellyfin ls /dev/dri                                             # renderD128 present
```
In the dashboard, an active transcode shows `(hw)`, watched state is present, all plugins read Active.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| No `/dev/dri` in container | host on the cloud kernel (no i915) | host runs `linux-image-amd64`, not the cloud kernel |
| SSO: Authelia shows `access_denied` | user not in `jellyfin-users` (or `lldap_admin`) | `users.kmkdp.com` → the user's groups; Authelia log `jellyfin_access` |
| SSO: "Error. Check permissions." from Jellyfin after Authelia | the plugin's **Roles** list does not match a value of the `groups` claim (exact, case-sensitive) | Jellyfin log line `has one or more incorrect role claims` lists the claims it saw |
| SSO: Authelia `invalid_request` / redirect URI mismatch | provider name in the plugin is not `authelia`, or Jellyfin built an `http://` redirect | plugin Scheme Override = `https`; redirect must be exactly `https://jellyfin.kmkdp.com/sso/OID/redirect/authelia` |
| SSO: "Error preparing login" / discovery error | Jellyfin cannot fetch `https://sso.kmkdp.com/.well-known/openid-configuration` (the DMZ resolves via public DNS, so this goes out through Cloudflare) or PAR not disabled | `docker exec jellyfin curl -s -o /dev/null -w '%{http_code}\n' https://sso.kmkdp.com/.well-known/openid-configuration` → 200; **Disable Pushed Authorization** ticked |
| SSO: `invalid_client` in the Authelia log | plugin secret ≠ the digest in `jellyfin_client_secret`, or the file is missing/unreadable | re-run the hash step in [[jellyfin-sso]]; `sudo stat -c '%a %U' …/jellyfin_client_secret` → `400 8091` |
| Plugins read "Restart"/broken after an image bump | plugin build targets an older ABI | reinstall the matching build from its repository (`stacks/jellyfin/plugins.yaml`) |
| Trickplay: "Error creating trickplay images" / Read-only file system | library option *Save trickplay images next to media* on a RO mount | keep `SaveTrickplayWithMedia=false` in both library options |
| Episode metadata: "Error in The Open Movie Database" JSON errors | OMDb episode endpoint returns malformed JSON | OMDb removed from the Shows library fetchers (movies keep it) |
| Intro Skipper: "ffmpeg did not exit within 60000ms" | two 4K software decodes in parallel on the DMZ VM | `MaxParallelism=1` in IntroSkipper.xml |
| Watched/resume state lost | config not copied byte-for-byte | restore `/opt/kdk-lab/jellyfin/config` from the migration tar |
| Playback buffers off-LAN | Cloudflare free-plan proxy throttling | test on LAN to isolate |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[dmz-proxy]] + tunnel | ingress + public access |
| Needs | A380 `/dev/dri` | hardware transcode (falls back to CPU) |
| Needs | `/mnt/media` (NFS, RO) | no library content |
| Needed by | [[seerr]] | request fulfilment + user auth |

## Observability

No dashboard. `jellyfin.kmkdp.com` probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

Listens on `8096`, joins `proxy-dmz`. `/dev/dri` gives QSV. Library mounted read-only; transcode scratch is a 6G tmpfs at `/config/cache/transcodes` (Jellyfin's real transcode path), never backed up. In-container library paths match k3s (`/data/media/...`) so item ids stay attached. Ramdisk + Arc QSV tuning: see [[jellyfin-transcoding]].

## Configuration
| LDAP-Auth plugin (dashboard, host-managed) | Server `lldap.kmkdp.com`, port 6360, SSL on, **SkipSslVerify off** (LE wildcard via lldap's certs-dumper, 2026-10-27); the name resolves through the `lldap.kmkdp.com` alias on the internal `identity_ldap` network, which the jellyfin service joins (lldap left `proxy-dmz` on 2026-10-28; the network is created by [[identity]], so deploy identity first); base `ou=people,dc=kmkdp,dc=com`; filter `(memberof=cn=jellyfin-users,ou=groups,dc=kmkdp,dc=com)`; create users from LDAP on |
| SSO Authentication plugin (dashboard, host-managed) | K0lin fork 5.1.1, provider `authelia` → `https://sso.kmkdp.com`, client `jellyfin` (secret `op://kdk-cluster/jellyfin-oidc/password`), scopes `groups`+`email`, role claim `groups`, roles `jellyfin-users`/`lldap_admin`, admin role `lldap_admin`, default provider = the LDAP plugin; full field list in [[jellyfin-sso]] |
| Networking (dashboard, host-managed) | Known proxies `10.211.2.3/32` (the DMZ Traefik, pinned on proxy-dmz since 2026-10-28; was `10.211.2.0/24`, which let any proxy-dmz container forge `X-Forwarded-For` — set by the owner in Dashboard → Networking in the same window as the dmz-proxy redeploy; cloudflared `10.211.2.2` is not needed: the tunnel entrypoint passes Cloudflare's `X-Forwarded-For` through without appending its own hop); LAN networks `10.1.1.0/24, 10.1.3.0/24, 10.1.20.0/24, 10.1.30.0/24, 192.168.191.0/24` (Tailscale clients arrive un-masqueraded as 100.64.0.0/10 since 2026-10-26 and are therefore remote; before that 10.1.20.0/24 had to be left out); auto discovery off; Internet streaming bitrate limit applies to remote clients (set 2026-10-26, raise for more remote viewers) |

| Path / setting | What it is |
|---|---|
| `/opt/kdk-lab/jellyfin/config` | jellyfin.db (users, watched), plugins, config |
| `/mnt/appdata/jellyfin/metadata` → `/config/data/metadata` | posters/images/NFO metadata (8+ GB) on the rw appdata NFS since 2026-10-28, off the 59 GB root disk. Same in-container path, so no dashboard change. `1000:1000` (PUID/PGID). Regenerable: excluded from backup-dmz, tankz3 snapshots only. The DB (`/config/data/data`) stays local — never on NFS |
| `stacks/jellyfin/plugins.yaml` | plugin GUIDs + pinned versions |
| `tmpfs /config/cache/transcodes` | 6G RAM-disk scratch (`mode=1777`), excluded from backup — see [[jellyfin-transcoding]] |
| `/opt/kdk-lab/jellyfin/config/encoding.xml` | Arc QSV/HDR encoding settings (host state, not in git) |

## SSO

Added in batch 5b (2026-10-28). Install and settings: [[jellyfin-sso]].

- **Web login page.** The normal username/password form stays. Under it, the Branding *login disclaimer* holds a **Sign in with SSO** button that opens `https://jellyfin.kmkdp.com/sso/OID/start/authelia`. The plugin has no auto-redirect option. For a one-click login, bookmark that URL or use a dashboard tile: with a live Authelia session it goes straight back into Jellyfin without a prompt.
- **What Authelia asks for.** `jellyfin_access` is `two_factor`. A passkey alone counts as one factor in Authelia 4.39, so after a passkey Authelia also asks for the password (or the reverse: password, then passkey or TOTP). An existing 2FA session on `sso.kmkdp.com` skips the prompt.
- **Who may sign in.** Members of `jellyfin-users` or `lldap_admin`. Authelia gates this first, and the plugin's **Roles** list checks it again. The account is matched **by username** (`preferred_username` = the LLDAP uid), so existing LDAP and Wizarr users keep their watch history. The plugin re-sets the login provider to the LDAP plugin, so the user's LDAP password keeps working everywhere.
- **Admin mapping.** Members of `lldap_admin` become Jellyfin administrators at SSO login. *Preserve existing admin permissions* is on, so SSO never demotes anyone. `kdkadmin` is local and never logs in through SSO.
- **TVs and phones (Neptune, Swiftfin, Android TV).** Native apps cannot run the SSO flow. Use **Quick Connect**: the device shows a 6-character code, and you approve it in a signed-in web session under *Settings → Quick Connect*. SSO does not change Quick Connect. Never approve a code you did not request.
- **Apps and API clients.** Seerr, Wizarr, `wizarr-sync` and Jellystat use API keys or the user's LDAP password. They do not go through SSO and are unaffected.
- **Logout.** Signing out of Jellyfin does not sign you out of Authelia (the plugin has no logout callback).

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `kdk-cluster/jellyfin-oidc` (`password`) | SSO plugin client secret (plaintext in the plugin config; pbkdf2 digest in Authelia's `jellyfin_client_secret`) | 🟡 SSO breaks until both sides match | yes: regenerate in 1P, re-run the hash step in [[jellyfin-sso]], `RestartStack identity services=[authelia]`, paste the new plaintext into the plugin. Password and Quick Connect logins are unaffected meanwhile |

User passwords are LLDAP's (LDAP plugin).

## Backup and restore

Jellyfin's state lives on local disk at `/opt/kdk-lab/jellyfin/config` (bind-mounted to `/config`); only the artwork/metadata tree is on NFS (`/mnt/appdata/jellyfin/metadata` → `/config/data/metadata`). The config dir is captured by the DMZ backup chain, so a restore can rebuild users, API keys, plugin settings and the library without touching the metadata cache.

**The database.** On this 12.x line the library is **not** a separate `library.db` — it is consolidated into `config/data/data/jellyfin.db` (~110 MB), which holds **users, API keys, devices, activity and the full library/metadata index**. `playback_reporting.db` sits beside it. There is no standalone `library.db` to back up.

### What is backed up

- **Nightly (on-site):** the `backup-dmz` stack's `rsync` service mirrors `/opt/kdk-lab/` → `/mnt/backup/dkr/kdk-dkr-dmz-01/state/` on the NAS (NFS), run at 03:20 by the `nightly-backups` procedure and gated so the deploy blocks until it finishes. A `.last-backup` marker is touched on success; `backup-verify` is the single Pushover alert source.
- **Off-site:** `backup-offsite` (on [[kdk-dkr-01]]) packs each host's NAS tree into one `tar.zst` (`pack.sh`) and `rclone`s it to the Proton Drive crypt remote.
- **In scope for Jellyfin (verified present and current in both the on-site tree and the off-site pack):** `config/data/data/jellyfin.db` (+ `-wal`/`-shm`), `playback_reporting.db`, every `config/data/plugins/configurations/*.xml` **including `LDAP-Auth.xml` and `SSO-Auth.xml`** (these embed auth secrets — treat the backup as sensitive), and `config/system.xml`, `network.xml`, `encoding.xml`, `branding.xml`.

### Excluded on purpose (regenerable)

| Path | Why |
|---|---|
| `config/data/metadata` | Downloaded artwork/metadata, re-fetchable (~6.5 G); also lives on NFS, not local disk. Dropped by the rsync *and* the off-site `pack.sh`. |
| `config/data/transcodes` | Transient transcode scratch. |
| `config/cache` | Regenerable cache. |

### Consistency caveat (live SQLite copy)

The rsync runs while Jellyfin is up, so `jellyfin.db` is copied in WAL mode. The `-wal` and `-shm` files are copied alongside it, but the three files are read non-atomically, so a copy taken across a WAL checkpoint could in theory be torn. Two consequences:

- A **strict read-only** open of the raw backup fails (`unable to open database file`, SQLite error 14): read-only mode cannot build the WAL index without write access. A real restore does not hit this — Jellyfin opens the DB read-write on start and replays the WAL cleanly.
- Jellyfin's own scheduled database backup (which would write a consistent archive to `config/data/data/SQLiteBackups/`) is **not enabled** — that directory is empty.

**Mitigation, if a stronger guarantee is ever needed:** enable Jellyfin's scheduled *Back up* task, or add an atomic snapshot step before the rsync (`sqlite3 jellyfin.db "VACUUM INTO '/…/jellyfin-snapshot.db'"`) and back up the snapshot instead of the live file. Not currently required — the latest copy verified clean (below).

### Restore procedure

On [[kdk-dkr-dmz-01]] (`sudo docker` for every command):

```bash
# 1. Quiesce the DB (never copy over a running instance)
sudo docker stop jellyfin

# 2. Restore from the on-site NAS copy. Bring -wal/-shm from the SAME snapshot
#    so the WAL replays; preserve ownership.
S=/mnt/backup/dkr/kdk-dkr-dmz-01/state/jellyfin/config/data/data
D=/opt/kdk-lab/jellyfin/config/data/data
sudo cp -p "$S"/jellyfin.db "$S"/jellyfin.db-wal "$S"/jellyfin.db-shm "$D"/

# 3. Start — Jellyfin opens the DB read-write and replays the WAL
sudo docker start jellyfin
```

From the off-site archive instead, extract first (on [[kdk-dkr-01]], which has `zstd`):
`zstd -dc /mnt/backup/dkr/offsite/kdk-dkr-dmz-01-<DATE>.tar.zst | tar -xf - <member-path>`.

To restore plugin or server settings only, copy the relevant `*.xml` back the same way (stop/copy/start).

### Restore test (2026-10-30)

The 03:00 on-site copy of `jellyfin.db` (plus `-wal`/`-shm`) was copied to a throwaway dir and opened with `sqlite3` in a pinned `alpine` container (the live backup was never modified):

- `PRAGMA integrity_check` → **ok** (WAL replayed on open).
- Counts: **3 users (1 administrator), 3 API keys, 15 devices.**
- Compared against the live server (`GET /Users`, `GET /Auth/Keys`): the user list and admin flag **match exactly**; all 3 backed-up API keys are present live. The live server had 2 additional API keys created after the 03:00 snapshot — expected, since the backup RPO is nightly (~24 h).

Takeaway: the backup opens, is internally consistent, and reproduces production users and keys. Changes made during the day (new users, API keys, watch state) are only captured at the next 03:20 run.

## User Guide

Users log in at `jellyfin.kmkdp.com` with their directory account (Wizarr issues invites). In a browser, use the username/password form or **Sign in with SSO**. On a TV or phone, use Quick Connect (approve the code from a signed-in browser) or the directory password. Playback, resume, and favourites carry over from the k3s instance.
