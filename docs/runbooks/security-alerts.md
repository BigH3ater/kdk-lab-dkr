---
title: Security alerts (Loki ruler)
---

# Security alerts (Loki ruler)

What a security page means and what to check first. The rules live in
`stacks/loki/rules/<family>.yml`; the full list with severities is in
[Observability → Alert rules](../services/observability.md#alert-rules).
Pages arrive through Alertmanager on kdk-mon-01 (`source="loki"`); `info`
alerts come quietly (priority -1) at most once a day.

## General

- **Find the log line.** Every alert is a LogQL count. Open Grafana → Explore →
  Loki, paste the rule's `expr` without the outer `sum(...) > N`, and look at the
  lines (the selector names host and container). For the DMZ Traefik:
  `{host="kdk-dkr-dmz-01", container="traefik"} | json | ...`.
- **Internet or LAN?** `entryPointName="tunnel"` = came through cloudflared;
  `websecure` = LAN, tailnet or a DMZ container. `ClientHost` is the real client
  on both (cloudflared's X-Forwarded-For on the tunnel).
- **Silence** (planned work, a known false positive):
  ```bash
  ssh kdkadmin@10.1.20.30 'sudo docker exec alertmanager-ext amtool --alertmanager.url=http://127.0.0.1:9093 \
    silence add alertname=<Name> --duration=4h --comment="<why>"'
  ```
  Or in the UI: `https://alertmanager.kmkdp.com` (Authelia) → Silences; the
  Pushover link opens it. A permanent false positive is a rule fix (PR), not a
  long silence. Adding or changing a rule: [Add a Loki alert rule](add-loki-alert-rule.md).
- **Inferred wording** (`kdk_inferred: "true"` annotation): the log text was
  guessed from upstream docs, not seen here. When one fires, check the real line
  and tighten the regex; when you test one and it does not fire, fix the regex.
- **Real compromise?** Stop exposure first (Cloudflare dashboard → tunnel →
  remove the public hostname, or `docker stop` the app), keep the logs (Loki keeps
  30 days), then rotate the credentials the app held.

## Edge

`edge.yml`: Cloudflare tunnel traffic at the DMZ Traefik, and cloudflared itself.

| Alert | First checks |
|---|---|
| `EdgeNonUSTraffic` | Which host/path? Cloudflare → Security → WAF: the geo rule's expression and any skip rule that matches the path. Security Events shows what the edge let through |
| `EdgeAuthFailureBurst` | `sum by (ClientHost, RequestHost)` of the 401/403 lines; one IP = add it to a WAF block or rate-limit rule; many IPs on one host = credential stuffing, check that app's own lockout |
| `EdgeJellyfinAdminPathHit` (critical) | An admin path answered **2xx** through the tunnel (a rejected 4xx/5xx scan does not fire this — see `EdgeJellyfinAdminPathProbe`). The WAF rule that blocks Jellyfin admin paths failed. Check the path in the line, the WAF rule expression, and Jellyfin's activity log for what the request did. Remove `jellyfin.kmkdp.com` from the tunnel until fixed |
| `EdgeJellyfinAdminPathProbe` (info) | A non-lab client hit a Jellyfin admin path over the tunnel and was rejected (non-2xx) — the WAF/Jellyfin handled it. Visibility only; confirm the WAF still blocks these paths, no action if the scan was rejected. The lab's own `kdk-edge-probe` is excluded |
| `EdgeSpoofedCfHeaders` | A LAN client sent `Cf-*` headers and `strip-cf` was not applied on `websecure` (dmz-proxy compose: `--entrypoints.websecure.http.middlewares=strip-cf@file`). Fix the flag; find the client by `ClientHost` |
| `CloudflaredOutdated` (info) | Merge the Renovate PR for `cloudflare/cloudflared` in `stacks/dmz-proxy` |
| `CloudflaredEdgeFlapping` | WAN: UniFi internet health; Cloudflare status page; `docker logs cloudflared` on dmz-01 |

## Identity

`identity.yml`: Authelia and lldap on kdk-dkr-dmz-01.

| Alert | First checks |
|---|---|
| `AutheliaFailedLoginBurst` | Postgres truth: `select time, username, remote_ip, auth_type from authentication_logs where successful = false and time > now() - interval '1 hour';` (`docker exec -it authelia-postgres psql -U authelia`). A public IP = block it at the WAF |
| `AutheliaRegulationBan` | `select * from banned_user where revoked = false;` A family member locked out: `authelia storage bans user revoke <name>` |
| `AutheliaOidcClientAuthFailure` | Which client (line has `client_id`)? A wrong/rotated client secret after a change is the usual cause; an unknown client = probe |
| `AutheliaForbiddenNamedUser` | The user is signed in but the access rule denies them: expected after a group change, else check `access_control` in `stacks/identity/authelia/configuration.yml` |
| `AutheliaForwardAuthBadHeadersPublic` | Someone hit `/api/authz/forward-auth` directly from the internet: the tunnel must not route it. Check the public hostnames in the tunnel config |
| `AutheliaSecondFactorChanged` (info) | Expected right after you or a family member enrolled a key. If not: `select username, created_at from webauthn_credentials order by created_at desc;` and remove the device |
| `LldapBindFailures` | Which service binds as a user (Jellyfin LDAP plugin, Wizarr, Grafana)? A burst = password spraying through that service's login page |

## Jellyfin

`jellyfin.yml`: write paths in the DMZ Traefik log for `jellyfin.kmkdp.com`, and the Jellyfin text log.

| Alert | First checks |
|---|---|
| `JellyfinClientLogUpload`, `JellyfinSubtitleUpload`, `JellyfinLiveTvTunerChange` | These features are disabled for every user, so any write is a probe or a settings drift. Dashboard → Users → that user's permissions; Dashboard → Logs |
| `JellyfinApiKeyChange` | Dashboard → API Keys: is the key yours? Delete unknown keys |
| `JellyfinUserPolicyChange` | Dashboard → Activity: who changed which user? Writes from the proxy-dmz network (Wizarr, wizarr-sync) are excluded; anything else should be you |
| `JellyfinFfmpegSuspiciousArgs` (critical) | The ffmpeg argument-injection pattern. Stop the container, keep `/config/log`, check `/config` for new files (`find /config -newer /config/system.xml -type f`), rotate the admin password and API keys, then upgrade |
| `JellyfinFailedLoginBurst` | `docker logs jellyfin \| grep "has been denied"` for the IPs; Jellyfin has no lockout of its own, block at the WAF |
| `JellyfinLoginFromInternet` (info) | Expected for remote family use; act only if the user or IP is unexpected |
| `JellyfinPluginInstalled` | Dashboard → Plugins: is the plugin and its repository expected? |

## Wizarr

`wizarr.yml`.

| Alert | First checks |
|---|---|
| `WizarrInviteGuessBurst` | One client walking `/j/<code>`: block the IP at the WAF; check that no unknown invitation got used (Wizarr → Invitations) |
| `WizarrAdminRouterNon302` (critical) | The admin router answered a tunnel client without the SSO redirect: the ForwardAuth middleware is gone or the router gained `tunnel`. Check `stacks/wizarr/compose.yaml` labels; remove the public hostname until fixed |
| `WizarrLdapExistingUserTouched` (critical) | An invite changed an existing lldap account (posture finding F1). In lldap, check that account's groups and reset its password; find the invite code in Wizarr and delete it |
| `WizarrLdapUserCreated` (info) | Expected right after you sent an invite |
| `WizarrAdminLogin` (info) | Expected admin login; the Authelia session already passed (Wizarr does no login of its own). Unexpected = check who reached the admin UI |
| `WizarrInviteAccepted` (info) | An invite was accepted; expected right after you sent one. Unexpected = check Wizarr → Invitations for a code you did not issue |
| `EdgeWizarrAdminPathHit` | An admin path was requested over the tunnel: confirm it was you; the admin router should force the SSO redirect, so a tunnel client reaching it is worth a look |
| `WizarrAdminMutation` (info) | Expected only while you work in Wizarr |

## Request apps

`request-apps.yml`: Seerr and Libreseerr.

| Alert | First checks |
|---|---|
| `SeerrFailedLoginBurst`, `SeerrLoginFailuresPerClient`, `LibreseerrLoginFailures` | Which client? One public IP = WAF block |
| `SeerrAdminWriteFromInternet` | A settings/user write succeeded through the tunnel: who (Seerr → Users → activity)? Only admins should manage settings |
| `SeerrUserCreated`, `LibreseerrUserProvisioned` (info) | Expected after a Wizarr invite (wizarr-sync imports into Seerr) |
| `LibreseerrAdminWrite` (info) | Expected while you administer it |
| `RequestAppsNonUSTraffic` | Same as `EdgeNonUSTraffic` for these two hosts |
| `SeerrErrorRate` | `docker logs seerr \| grep "\[error\]"`: usually Jellyfin/Radarr connectivity, not security |
| `RequestAppsContainerChange` (info) | Expected after a deploy (Renovate merge); otherwise check `docker events` history and the image digest |

## Mobile apps

`mobile-apps.yml`: Audiobookshelf, Immich, Tandoor.

| Alert | First checks |
|---|---|
| `AbsLoginFailures`, `ImmichLoginFailures`, `TandoorLoginFailures` | Which client? ABS and Tandoor have no lockout; block at the WAF |
| `AbsNewUserOrApiKey` | ABS → Settings → Users / API keys: expected? |
| `ImmichAdminApiFromInternet`, `TandoorAdminFromInternet` (critical) | Admin API reached through the tunnel: the WAF path block failed. Remove the public hostname until fixed |
| `ImmichMassDownload` | Which user session? Immich → Administration → Users → sessions: revoke unknown sessions |

## Home Assistant

`home-assistant.yml`: kdk-dkr-02 (HA, Node-RED, zigbee2mqtt, Mosquitto) and kdk-dkr-03 (Scrypted; Homebridge was removed 2026-10-28). Several rules need a config change before they can fire (noted in the file).

| Alert | First checks |
|---|---|
| `HaFailedLogins`, `HaIpBanned` | HA log has the source IP; `ip_bans.yaml` in the HA config dir lists bans |
| `HaOidcLoginUnexpectedUser` | Settings → People: who is it? Remove the user and check the Authelia group that grants HA |
| `HaSecurityEvent` | User added/changed/removed: Settings → People |
| `NodeRedFlowsDeployed` (info), `NodeRedAuthFailures` | Expected when you deploy flows; failures = someone at the editor |
| `ZigbeePermitJoinOrNewDevice` | Expected while pairing; otherwise disable permit-join in z2m and check the new device list |
| `MosquittoAuthFailure` | A client with old credentials (after a rotation) or a probe on the LAN |
| `ScryptedHomebridgeAuthEvent` | Scrypted UI: users and plugins (the name predates the Homebridge removal) |

## Hosts

`hosts.yml`: syslog from every host plus docker-event-watch on the DMZ host.

| Alert | First checks |
|---|---|
| `HostSshLoginFromUnexpectedSource` (critical) | `journalctl -u ssh --since -1h` on that host. Admin sources: 10.1.30.0/24, 10.1.20.20, 100.64.0.0/10, 192.168.2.0/24. 192.168.2.3 (posture L-05) is not in the set until identified. Unknown = rotate `op://kdk-ops/kdkadmin-ssh-key` and check `~/.ssh/authorized_keys` on every host |
| `HostSudoFailure`, `HostSudoByUnexpectedUser` | Who ran it (`journalctl _COMM=sudo`)? Only `kdkadmin` has keys; any other account = lock it |
| `HostUnknownImageOnDmz` | An image no DMZ stack uses was pulled or run on kdk-dkr-dmz-01: `docker ps -a`, `docker images`; a legit new stack = add its repository to the allowlist in `hosts.yml` in the same PR |

## Advisories

`advisories.yml`: weekly [advisory-watch](../services/advisory-watch.md) and [trivy-scan](../services/trivy-scan.md).

| Alert | First checks |
|---|---|
| `AdvisoryPinnedImageVulnerable` | Open the `url`. Bump the image (merge the Renovate PR or change the pin to `fixed_in` or later); if not exposed (feature unused), note the decision in the weekly log |
| `AdvisoryNeedsReview` (info) | The tag is not comparable (`latest`, floating, wrapper, NVD/RSS item): read the advisory and decide by hand; pinning an exact tag makes the next run decide itself |
| `TrivyFixableCritical` | Grafana → Security Advisories → Trivy table: which package (`top_pkg`, `top_fixed`)? A newer image tag usually carries the fix; for local `kdk/*` images rebuild on a current base |
| `AdvisoryWatchSilent`, `TrivyScanSilent` | The weekly job did not report: Komodo → Procedures → run history; `docker logs advisory-watch` / `trivy-scan-report`. Fires once after the first deploy until the first run |

## Shipping

`shipping.yml`: one `LokiShippingStalled` rule per host (no `scope`/`runbook`
labels; the `host` label names the silent host).

| Alert | First checks |
|---|---|
| `LokiShippingStalled` | No line at all from that host for 30 min. On the host: `docker ps --filter name=alloy`, `docker logs alloy --tail 20`; `LOKI_HOST` must be `10.1.20.21`. Path: the host's IP must be in kdk-log-01's `log_clients_v4` set (`sudo nft list set inet kdk-input log_clients_v4`) and, for the DMZ, the UniFi rule `192.168.191.20 → 10.1.20.21:3100`. All hosts at once = kdk-log-01 or Loki down: `curl -s http://10.1.20.21:3100/ready` from mon-01 |

## Cloudflare watch

`cloudflare.yml`: [cloudflare-watch](../services/cloudflare-watch.md), a
read-only edge watch for the `kmkdp.com` zone on [[kdk-log-01]]. Hourly lines
land on `{container="cloudflare-watch"}`, the weekly line on
`{container="cloudflare-watch-weekly"}`. Each alert names the watch line's
`check`/`drift` fields to open in Grafana → Explore → Loki.

| Alert | First checks |
|---|---|
| `CloudflareTunnelUnhealthy` | The tunnel was not `healthy` on 2+ hourly checks. Cloudflare dashboard → Zero Trust → Networks → Tunnels; `docker logs cloudflared` on kdk-dkr-dmz-01; `curl -I https://sso.kmkdp.com`. Two runs = not a blip |
| `CloudflareEdgeCertExpiring` | Universal SSL edge cert under 14 days (`days_left` in the summary). Normally Cloudflare auto-renews; check the zone's SSL/TLS → Edge Certificates for a stuck validation |
| `CloudflareZoneSettingDrift` | A zone setting left the expected policy. Open the latest `check="zone_settings"` line, read `drift` (e.g. `min_tls_version='1.1' want '1.2'`); fix in the Cloudflare dashboard or update `EXPECTED_SETTINGS` in `watch.py` if the change was intended |
| `CloudflareRuleDrift` | The weekly `rule_drift` sha changed: a custom-firewall / rate-limit rule or a DNS record was edited. Review the change in the Cloudflare dashboard (Security → WAF, DNS). Intended? It is now the new baseline — the next weekly run records it and the alert resolves |
| `CloudflareWatchMissing` | No hourly output for 3h: Komodo → Procedures → `cloudflare-watch` run history; `docker logs cloudflare-watch`; confirm `/etc/kdk/cloudflare-watch.env` exists (token). Fires once after the first deploy until the first run |
