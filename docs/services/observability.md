---
title: Observability (metrics, logs, fan control)
host: kdk-mon-01, kdk-log-01
stack: monitoring, loki, observability-agent, node-exporter
tier: infra
status: live
storage: yes
secrets: [pushover-kdk-lab, kdk-lab-idrac-fanctl-admin]
updated: 2026-10-29
tags: [homelab/stack, tier/infra, status/live]
---

# Observability — metrics, logs, fan control

Metrics + alerting live on the independent monitor [[kdk-mon-01]]; logs live on the dedicated log VM [[kdk-log-01]] (moved off [[kdk-dkr-01]] 2026-10). Fans on both PowerEdge BMCs are steered by a community controller.

**Related:** [[kdk-mon-01]] · [[kdk-log-01]] · [[kdk-dkr-01]] · [[komodo]]

> `https://alertmanager.kmkdp.com` (Authelia, `internal-users`) fronts Alertmanager via the internal Traefik; direct fallback `http://10.1.20.30:9093` from an allowed source.

## At a glance

| Field | Value |
|---|---|
| Metrics/alerts | Prometheus + Alertmanager (→ Pushover) + Grafana on [[kdk-mon-01]] |
| Logs | Loki 3.7.9 on [[kdk-log-01]] (`10.1.20.21`), storage on the VM disk (`/opt/kdk-lab/loki/data`, PBS vzdump of VM 320), 30-day retention; ruler: 11 rule files / 76 alerts (`stacks/loki/rules/`, see Alert rules) → Alertmanager |
| Collection | per-host `observability-agent` (cAdvisor + Alloy + **docker-event-watch**); `node-exporter` on the two VMs |
| Weekly security jobs | [[advisory-watch]] (upstream advisories vs pinned tags, kdk-log-01) and [[trivy-scan]] (Trivy on every host), both into Loki; Grafana → Security → Security Advisories |
| Fan control | two `tigerblue77` containers on [[kdk-mon-01]]; `fanctl-hyp` **active** (R720 ~3000 RPM, 20%), `fanctl-nas` **active** (R730xd ~4800 RPM, 20%) |
| Web UI | Grafana `https://grafana.kmkdp.com` (via Traefik on kdk-dkr-01; direct fallback `http://10.1.20.30:3000` when dkr-01 is down) — Prometheus + Loki datasources; Explore for logs |
| Blast radius | Loss of dashboards/log search; the mon-01 alerting path is independent of the rest |

## Verify

```bash
curl -s http://10.1.20.21:3100/ready                                  # ready (Loki; from an allowlisted source)
curl -s http://10.1.20.30:9090/api/v1/targets | grep -c '"up"'        # cadvisor/node/blackbox targets up
curl -sG http://10.1.20.21:3100/loki/api/v1/label/host/values         # every host present (6)
curl -s http://10.1.20.21:3100/loki/api/v1/rules | grep -c 'alert:'    # 76 (11 groups, see Alert rules)
ssh kdkadmin@10.1.20.30 'sudo docker logs --tail 3 fanctl-hyp'        # temps logged, profile applied
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Logs missing from a host (`LokiShippingStalled`) | that host's Alloy down, wrong LOKI_HOST, or its IP not allowed by kdk-log-01's nft / UniFi | `docker logs alloy` on the host; every host uses `LOKI_HOST=10.1.20.21` |
| "timestamp too old" / "entry too far behind" after an Alloy recreate | before 2026-10-29 Alloy kept positions in the container FS (`--storage.path` unset → cwd-relative `data-alloy/`), so a recreate replayed every container log | fixed: positions now persist on the `alloy-data` volume (see Architecture); a first-ever start still does one benign replay |
| DMZ host metrics/logs absent | DMZ→internal path | node/cadvisor pulled over LAN→DMZ; Alloy pushes via the UniFi rule `192.168.191.20 → 10.1.20.21:3100` |
| `FansPinnedHigh` firing | that BMC on auto (loud) | expected only if a controller is monitoring-only/stopped |
| Per-container panels empty | cAdvisor older than v0.55 can't read containers under the containerd-snapshotter image store (`failed to identify the read-write layer ID`) | runs `ghcr.io/google/cadvisor:v0.60.6` (gcr.io stops at v0.55.1) with `/var/lib/containerd` mounted; `count by (host) (container_memory_usage_bytes{name!=""})` should be > 0 on every host. Crash/unhealthy alerting is still `docker-event-watch`, not cAdvisor |
| A security rule never fires | log text drifted, or `entryPointName="tunnel"` missing (dmz-proxy before the two-entrypoint split) | run the rule's expr in Grafana Explore over 7 d; see [security alerts runbook](../runbooks/security-alerts.md) |
| One-shot job output missing in Loki | Alloy's `discovery.docker` lists running containers, and the backup jobs exit within seconds | the Backups dashboard counts runs from `docker-event-watch` die events instead; capturing their output needs a follow-up (exited-container discovery or pushing logs from the job) |

## Alert rules

Loki ruler rules live in `stacks/loki/rules/*.yml` (only `.yml` files there: the ruler parses every file and `RULES_SHA` hashes `rules/*.yml`). Every rule carries `severity` (critical / warning / info), `source: loki`, `scope: <file>` and a `runbook` annotation. Alertmanager routes `source="loki"` to Pushover grouped by `alertname, host`; `severity="info"` goes to the quiet receiver (`pushover-quiet`, priority -1, repeat 24 h). Sources and LogQL come from the 2026-10 posture reports (vault `10.Homelab/Security/`). DMZ Traefik rules read `{host="kdk-dkr-dmz-01", container="traefik"} | json`; "from the internet" is `entryPointName="tunnel"` (fallback `ClientAddr=~"10\\.211\\.2\\.2:.*"`), never `request_Cf_Connecting_Ip!=""` (it is `-` on LAN lines). What to do per family: [security alerts runbook](../runbooks/security-alerts.md).

| Rule | File | Severity | Fires when | Runbook |
|---|---|---|---|---|
| `EdgeNonUSTraffic` | `edge.yml` | warning | Non-US request reached the origin through the tunnel (…): the Cloudflare geo rule or a skip rule has a gap | [edge](../runbooks/security-alerts.md#edge) |
| `EdgeAuthFailureBurst` | `edge.yml` | warning | More than 60 401/403 per 5m from tunnel traffic for 5m (credential stuffing or a scanner) | [edge](../runbooks/security-alerts.md#edge) |
| `EdgeJellyfinAdminPathHit` | `edge.yml` | critical | A Jellyfin admin path reached the origin through the tunnel: the Cloudflare WAF block rule was bypassed or is missing | [edge](../runbooks/security-alerts.md#edge) |
| `EdgeSpoofedCfHeaders` | `edge.yml` | warning | Cf-Connecting-Ip seen on the LAN entrypoint (websecure): the strip-cf middleware is not applied there | [edge](../runbooks/security-alerts.md#edge) |
| `CloudflaredOutdated` | `edge.yml` | info | cloudflared reports a newer release: bump the pinned tag in stacks/dmz-proxy (or merge the Renovate PR) | [edge](../runbooks/security-alerts.md#edge) |
| `CloudflaredEdgeFlapping` | `edge.yml` | warning | cloudflared lost its edge connection more than 4 times in 30m (WAN/ISP or Cloudflare side) | [edge](../runbooks/security-alerts.md#edge) |
| `AutheliaFailedLoginBurst` | `identity.yml` | warning | Authelia: more than 10 failed authentications in 15m from … | [identity](../runbooks/security-alerts.md#identity) |
| `AutheliaRegulationBan` | `identity.yml` | warning | Authelia regulation banned a user or IP | [identity](../runbooks/security-alerts.md#identity) |
| `AutheliaOidcClientAuthFailure` | `identity.yml` | warning | Authelia: an OIDC authorization request failed (bad client, redirect URI or secret) | [identity](../runbooks/security-alerts.md#identity) |
| `AutheliaForbiddenNamedUser` | `identity.yml` | warning | Authelia denied a signed-in user by policy: … | [identity](../runbooks/security-alerts.md#identity) |
| `AutheliaForwardAuthBadHeadersPublic` | `identity.yml` | warning | Authelia forward-auth endpoint called with bad headers from public IP … (direct probe of /api/authz) | [identity](../runbooks/security-alerts.md#identity) |
| `AutheliaSecondFactorChanged` | `identity.yml` | info | Authelia: a 2FA device (TOTP/WebAuthn) was registered or removed (inferred wording) | [identity](../runbooks/security-alerts.md#identity) |
| `LldapBindFailures` | `identity.yml` | warning | lldap: more than 5 failed LDAP binds in 10m (password spraying via a service that binds as the user) (inferred wording) | [identity](../runbooks/security-alerts.md#identity) |
| `JellyfinClientLogUpload` | `jellyfin.yml` | warning | Jellyfin: POST to /ClientLog/Document (client log upload is disabled) | [jellyfin](../runbooks/security-alerts.md#jellyfin) |
| `JellyfinSubtitleUpload` | `jellyfin.yml` | warning | Jellyfin: subtitle upload (no user should have subtitle management) | [jellyfin](../runbooks/security-alerts.md#jellyfin) |
| `JellyfinLiveTvTunerChange` | `jellyfin.yml` | warning | Jellyfin: LiveTV tuner host or listing provider changed | [jellyfin](../runbooks/security-alerts.md#jellyfin) |
| `JellyfinApiKeyChange` | `jellyfin.yml` | warning | Jellyfin: API key created or deleted | [jellyfin](../runbooks/security-alerts.md#jellyfin) |
| `JellyfinUserPolicyChange` | `jellyfin.yml` | warning | Jellyfin: user created/deleted or user policy changed (from …) | [jellyfin](../runbooks/security-alerts.md#jellyfin) |
| `JellyfinFfmpegSuspiciousArgs` | `jellyfin.yml` | critical | Jellyfin started ffmpeg with drawtext/lavfi/system paths: possible argument injection | [jellyfin](../runbooks/security-alerts.md#jellyfin) |
| `JellyfinFailedLoginBurst` | `jellyfin.yml` | warning | Jellyfin: more than 10 denied logins in 10m | [jellyfin](../runbooks/security-alerts.md#jellyfin) |
| `JellyfinLoginFromInternet` | `jellyfin.yml` | info | Jellyfin: password login through the tunnel from … | [jellyfin](../runbooks/security-alerts.md#jellyfin) |
| `JellyfinPluginInstalled` | `jellyfin.yml` | warning | Jellyfin: a plugin was installed or updated | [jellyfin](../runbooks/security-alerts.md#jellyfin) |
| `WizarrInviteGuessBurst` | `wizarr.yml` | warning | Wizarr: … hit /j or /join more than 30 times in 5m (invite-code guessing) | [wizarr](../runbooks/security-alerts.md#wizarr) |
| `WizarrAdminRouterNon302` | `wizarr.yml` | critical | Wizarr admin router served a tunnel client without an SSO redirect | [wizarr](../runbooks/security-alerts.md#wizarr) |
| `WizarrLdapExistingUserTouched` | `wizarr.yml` | critical | Wizarr: an invite modified an existing lldap account (posture finding F1) | [wizarr](../runbooks/security-alerts.md#wizarr) |
| `WizarrLdapUserCreated` | `wizarr.yml` | info | Wizarr created an lldap user (expected only right after you sent an invite) | [wizarr](../runbooks/security-alerts.md#wizarr) |
| `WizarrAdminAuthFail` | `wizarr.yml` | warning | Wizarr: more than 3 failed admin logins in 10m (behind Authelia, so the caller already passed SSO) | [wizarr](../runbooks/security-alerts.md#wizarr) |
| `WizarrAdminMutation` | `wizarr.yml` | info | Wizarr admin/settings mutation (expected only while you are working in Wizarr) | [wizarr](../runbooks/security-alerts.md#wizarr) |
| `SeerrFailedLoginBurst` | `request-apps.yml` | warning | Seerr: more than 10 failed sign-ins in 10m | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `SeerrLoginFailuresPerClient` | `request-apps.yml` | warning | Seerr: more than 5 failed login POSTs in 10m from … | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `SeerrAdminWriteFromInternet` | `request-apps.yml` | warning | Seerr: settings or user write succeeded through the tunnel | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `SeerrUserCreated` | `request-apps.yml` | info | Seerr: a user was created/imported or permissions changed (wizarr-sync imports invitees) | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `RequestAppsNonUSTraffic` | `request-apps.yml` | warning | Non-US request (…) reached Seerr/Libreseerr through the tunnel | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `LibreseerrLoginFailures` | `request-apps.yml` | warning | Libreseerr: more than 5 failed logins in 10m | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `LibreseerrUserProvisioned` | `request-apps.yml` | info | Libreseerr auto-provisioned an OIDC user | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `LibreseerrAdminWrite` | `request-apps.yml` | info | Libreseerr users/config/OIDC/LDAP write (expected only while you administer it) | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `SeerrErrorRate` | `request-apps.yml` | warning | Seerr logged more than 50 errors in 15m | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `RequestAppsContainerChange` | `request-apps.yml` | info | Seerr/Libreseerr image pulled, container recreated or crashed on kdk-dkr-dmz-01 | [request-apps](../runbooks/security-alerts.md#request-apps) |
| `AbsLoginFailures` | `mobile-apps.yml` | warning | Audiobookshelf: more than 5 failed logins in 10m (inferred wording) | [mobile-apps](../runbooks/security-alerts.md#mobile-apps) |
| `AbsNewUserOrApiKey` | `mobile-apps.yml` | warning | Audiobookshelf: a user or API key was created (inferred wording) | [mobile-apps](../runbooks/security-alerts.md#mobile-apps) |
| `ImmichLoginFailures` | `mobile-apps.yml` | warning | Immich: more than 5 failed logins in 10m | [mobile-apps](../runbooks/security-alerts.md#mobile-apps) |
| `ImmichAdminApiFromInternet` | `mobile-apps.yml` | critical | Immich admin API reached through the tunnel | [mobile-apps](../runbooks/security-alerts.md#mobile-apps) |
| `ImmichMassDownload` | `mobile-apps.yml` | warning | Immich: more than 500 original/download requests in 15m (bulk export or a stolen session) | [mobile-apps](../runbooks/security-alerts.md#mobile-apps) |
| `TandoorLoginFailures` | `mobile-apps.yml` | warning | Tandoor: more than 5 failed logins in 10m | [mobile-apps](../runbooks/security-alerts.md#mobile-apps) |
| `TandoorAdminFromInternet` | `mobile-apps.yml` | critical | Tandoor /admin reached through the tunnel | [mobile-apps](../runbooks/security-alerts.md#mobile-apps) |
| `HaFailedLogins` | `home-assistant.yml` | warning | Home Assistant: more than 3 invalid logins/tokens in 10m | [home-assistant](../runbooks/security-alerts.md#home-assistant) |
| `HaIpBanned` | `home-assistant.yml` | warning | Home Assistant banned an IP after failed logins | [home-assistant](../runbooks/security-alerts.md#home-assistant) |
| `HaOidcLoginUnexpectedUser` | `home-assistant.yml` | warning | Home Assistant: OIDC login by a user other than jmack/rach | [home-assistant](../runbooks/security-alerts.md#home-assistant) |
| `HaSecurityEvent` | `home-assistant.yml` | warning | Home Assistant user added/changed/removed (SECURITY log line) | [home-assistant](../runbooks/security-alerts.md#home-assistant) |
| `NodeRedFlowsDeployed` | `home-assistant.yml` | info | Node-RED flows were deployed or restarted | [home-assistant](../runbooks/security-alerts.md#home-assistant) |
| `NodeRedAuthFailures` | `home-assistant.yml` | warning | Node-RED: more than 3 failed admin logins / permission denials in 10m | [home-assistant](../runbooks/security-alerts.md#home-assistant) |
| `ZigbeePermitJoinOrNewDevice` | `home-assistant.yml` | warning | zigbee2mqtt: permit-join opened or a new device joined | [home-assistant](../runbooks/security-alerts.md#home-assistant) |
| `MosquittoAuthFailure` | `home-assistant.yml` | warning | Mosquitto rejected a client (not authorised / bad credentials) | [home-assistant](../runbooks/security-alerts.md#home-assistant) |
| `ScryptedHomebridgeAuthEvent` | `home-assistant.yml` | warning | …: login failure, new user or plugin install (inferred wording) | [home-assistant](../runbooks/security-alerts.md#home-assistant) |
| `HostSshLoginFromUnexpectedSource` | `hosts.yml` | critical | SSH login as … on … from … (not an admin source) | [hosts](../runbooks/security-alerts.md#hosts) |
| `HostSudoFailure` | `hosts.yml` | warning | sudo refused a request on … | [hosts](../runbooks/security-alerts.md#hosts) |
| `HostSudoByUnexpectedUser` | `hosts.yml` | warning | sudo used by … on … (only kdkadmin is expected) | [hosts](../runbooks/security-alerts.md#hosts) |
| `HostUnknownImageOnDmz` | `hosts.yml` | warning | Unknown image … pulled or run on kdk-dkr-dmz-01 | [hosts](../runbooks/security-alerts.md#hosts) |
| `AdvisoryPinnedImageVulnerable` | `advisories.yml` | warning | …:… is affected by … (…) | [advisories](../runbooks/security-alerts.md#advisories) |
| `AdvisoryNeedsReview` | `advisories.yml` | info | … (…) for …: pinned tag … not comparable, check by hand | [advisories](../runbooks/security-alerts.md#advisories) |
| `TrivyFixableCritical` | `advisories.yml` | warning | … on … has a fixable CRITICAL vulnerability (…) | [advisories](../runbooks/security-alerts.md#advisories) |
| `AdvisoryWatchSilent` | `advisories.yml` | warning | No advisory-watch output in Loki for 8 days | [advisories](../runbooks/security-alerts.md#advisories) |
| `TrivyScanSilent` | `advisories.yml` | warning | No trivy-scan output in Loki for 8 days | [advisories](../runbooks/security-alerts.md#advisories) |
| `LokiShippingStalled` | `shipping.yml` | warning | No log line from a host for 30m (one rule per host) | Troubleshooting above |
| `CloudflareTunnelUnhealthy` | `cloudflare.yml` | warning | Cloudflare tunnel for kmkdp.com reported unhealthy on 2+ hourly checks | [cloudflare-watch](../runbooks/security-alerts.md#cloudflare-watch) |
| `CloudflareEdgeCertExpiring` | `cloudflare.yml` | warning | Cloudflare edge cert for kmkdp.com expiring (… days left) | [cloudflare-watch](../runbooks/security-alerts.md#cloudflare-watch) |
| `CloudflareZoneSettingDrift` | `cloudflare.yml` | warning | A Cloudflare zone setting for kmkdp.com drifted from policy | [cloudflare-watch](../runbooks/security-alerts.md#cloudflare-watch) |
| `CloudflareRuleDrift` | `cloudflare.yml` | warning | Cloudflare custom/rate-limit rules or DNS records for kmkdp.com changed since last week | [cloudflare-watch](../runbooks/security-alerts.md#cloudflare-watch) |
| `CloudflareWatchMissing` | `cloudflare.yml` | warning | No cloudflare-watch output in Loki for 3h (hourly check silent) | [cloudflare-watch](../runbooks/security-alerts.md#cloudflare-watch) |

Not in Loki: long-lived HA tokens (`ha_llat_count`, Prometheus on kdk-mon-01). Rules marked *inferred wording* match log text that was not yet observed in this lab; confirm with one test event and tighten.

Check after a deploy:

```bash
curl -s http://10.1.20.21:3100/loki/api/v1/rules | grep -c 'alert:'          # 76
ssh kdkadmin@10.1.20.21 'sudo docker logs loki 2>&1 | grep -i -E "rule|ruler" | grep -i -E "err|fail" | tail'   # empty
```

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[kdk-log-01]] (VM 320 on kdk-hyp-01) | no log ingestion or search |
| Needs | idrac-exporter (BMC Redfish) | thermal alerting is blind |
| Needed by | operator | the only alerting + log-search path for the lab |

## Observability

This *is* the observability. Prometheus jobs: `idrac`, `node-external` (12), `cadvisor` (6, incl. kdk-log-01), `blackbox-icmp/-dns/-http-app`, `blackbox-service` (14 migrated `*.kmkdp.com`). Alert rules: `thermal.yml` (idrac-exporter), `service.yml` (host/container/endpoint/cert), `smart-home.yml`. All route to Pushover; Loki ruler alerts arrive at the same Alertmanager (`source="loki"`).

## Architecture

`loki:3100` on kdk-log-01, published on `10.1.20.21` only and fenced by its nft allowlist, with an Alloy syslog receiver on `:514` tcp+udp (`syslog.kmkdp.com` → 10.1.20.21). The Loki ruler reads `stacks/loki/rules/` and sends to Alertmanager `10.1.20.30:9093`, which routes `source="loki"` to Pushover grouped by `alertname, host`. Each host runs cAdvisor (`:9280`) + Alloy (Docker log discovery → Loki); that Alloy pins `--storage.path=/var/lib/alloy/data` to a named `alloy-data` volume, so its `loki.source.docker` positions (`positions.yml`) persist across a `--force-recreate` and the agent resumes tailing instead of replaying old lines (which Loki rejects as "entry too far behind"). Only positions are written there — `loki.write` has no WAL enabled, so none is kept. The three VMs (dkr-01, dmz-01, log-01) add node-exporter (`:9100`); the Pis have it as a package. Fan controllers reach the R720/R730xd BMCs by IPMI as `idracrw`. Container-state alerting is per-host: `docker-event-watch` tails the Docker events API and pages Pushover on a container crash (die with a non-graceful exit) or unhealthy status, and logs (never pages) every container `create` and image `pull` with its image reference for the Loki rules; its script is a compose `config` hashed into the `kdk.config-sha` label by the agent `pre_deploy`, so an edit recreates it (compose itself does not hash `configs:` content); the watcher sends a quiet "kdk container watch online" push each time it starts; user-facing apps are also covered by `EndpointDown` (blackbox HTTPS probes of the 14 `*.kmkdp.com` services).

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/loki/` | Loki + syslog Alloy (git-linked to kdk-log-01); `rules/` = Loki ruler rules |
| `stacks/observability-agent/`, `stacks/node-exporter/` | per-host agents |
| `stacks/monitoring/` | the mon-01 stack, git-linked as Komodo stack `monitoring-ext` (`auto_pull = false`; secrets hand-rendered on the Pi, mon-01 has no `op`); `post_deploy` reloads Alertmanager |
| `/opt/kdk-lab/loki/data` on kdk-log-01 | Loki data (VM disk). The old `/mnt/loki` NFS dataset on kdk-dkr-01 is released after 2026-11-28 |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `pushover-kdk-lab` | alert delivery | 🟡 external party | rewrite the two 0600 token files (uid 65534), recreate alertmanager |
| `kdk-lab-idrac-fanctl-admin` (kdk-ops, `idracrw`) | BMC fan control | 🟡 admin creds | rotate on both BMCs + 1P, redeploy the fan controllers |

## User Guide

Open Grafana at `https://grafana.kmkdp.com` and sign in with **Authelia** (members of `internal-users` land as Admin); the local `admin` login (1P `grafana-admin`) stays as break-glass, and `http://10.1.20.30:3000` works when kdk-dkr-01 is down. Explore → Loki datasource to search logs by label (`{host="kdk-dkr-01"}`, then `|= "error"`). To make the servers quiet, set `MONITORING_ONLY_MODE: "false"` on a fan controller in the mon-01 stack and redeploy; watch fan RPM drop in Grafana.
