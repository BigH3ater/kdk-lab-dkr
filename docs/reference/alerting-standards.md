---
title: Alerting standards
status: active
tier: infra
tags: [monitoring, prometheus, loki, alertmanager, alerts]
---

# Alerting standards

The contract every alert rule in this repo follows, for both engines:

- **metric rules** — Prometheus on [[kdk-mon-01]], `stacks/monitoring/prometheus/rules/*.yml`;
- **log rules** — the Loki ruler on [[kdk-log-01]], `stacks/loki/rules/*.yml`.

Both deliver to the one Alertmanager on kdk-mon-01
(`stacks/monitoring/alertmanager/alertmanager.yml`). This page is the standard;
[Alerting standard (Pushover + email)](../runbooks/pushover-alerts.md) is the
operational runbook (how to add a rule, what to do when one fires) and
[Add a Loki alert rule](../runbooks/add-loki-alert-rule.md) is the log-rule
how-to. When this page and a rule disagree, the rule is wrong.

## 1. Severity — what pages vs. what only records

Every rule carries exactly one `severity`. It decides the transport, nothing
else:

| `severity` | Meaning | Transport |
|---|---|---|
| `critical` | broken or unsafe **now**; act immediately | Pushover (high priority) **and** email |
| `warning` | degraded or suspicious; look soon | Pushover (high priority) |
| `info` | a provenance/app note; expected while you work on X | **email digest only** — not paged |

Rules of thumb:

- **Page (`critical`)** only for loss of a service, a host, the alerting
  pipeline, a security tripwire, or a thermal/UPS event — something worth
  waking up for. Keep the critical set small; every critical competes for
  attention with every other.
- **`warning`** is the default for "a human should look today": a near-full
  disk, a burst of failed logins, an exporter that stopped.
- **`info`** is a record, not an interruption. App notifications and change
  provenance (a user was created, an image was pulled, a stack was hand
  deployed) are `info`. They are **e-mailed as a batched digest** (owner
  decision, 2026-10-30) and never reach Pushover. Keep info rules low-rate or
  rely on the digest grouping — see §7.

## 2. Naming

- `PascalCase`, `<Subject><Condition>`: `ContainerOOMKilled`, `InstanceDown`,
  `TLSCertExpiringSoon`, `AutheliaFailedLoginBurst`, `KomodoManualDeploy`.
- The subject names the thing watched (service, host, app); the condition is
  what went wrong. No host or instance in the name — that belongs in labels.
- One alert name per condition across the fleet; the instance/host/container is
  a label, so `InstanceDown` covers every host.

## 3. Labels

Required on every rule:

| Label | Value | Used for |
|---|---|---|
| `severity` | `critical` \| `warning` \| `info` | routing (§1, §7) |
| `source` | `loki` \| `external-monitor` | routing + provenance; `loki` = ruler on kdk-log-01, `external-monitor` = Prometheus on kdk-mon-01 |
| `scope` | the rule family / file (`containers`, `hosts`, `jellyfin`, `komodo`, …) | grouping, dashboards, inhibition targeting |

Conventions:

- The cAdvisor scrape adds `host`, `stack` and `service` to container series
  (`stacks/monitoring/prometheus/prometheus.yml` `metric_relabel_configs`), so
  container rules get `host`/`service`/`stack` for free — use them in the
  summary and for inhibition.
- Client-keyed log rules derive a single `client` label with the
  `label_format client="{{ if .request_Cf_Connecting_Ip }}…{{ else }}…{{ end }}"`
  pattern, so the Cloudflare client IP (not Traefik's peer) is what you see.
- `kdk_inferred: "true"` marks a rule written against log text **not yet seen
  in this lab**. Promote it (drop the label) once it has fired on a real event,
  or delete it, within 30 days.

## 4. Annotations

Every rule carries:

- **`summary`** — one line, the house format
  `<subject>: <what> (<host/instance>, <actor/client if any>)`. It is the
  Pushover title line and the email subject body.
  Example: `tdarr-node OOM-killed on kdk-dkr-01`.
- **`description`** — one or two sentences: what it means and the first thing to
  check. Name the likely cause when it is known.
- **`runbook`** — a repo-relative path **with an anchor** into
  `docs/runbooks/`, e.g. `docs/runbooks/pushover-alerts.md#containers`. Every
  rule has one; the anchor must exist.

## 5. `for:` defaults

`for:` is how long the condition must hold before the alert fires — it debounces
flaps.

| Rule kind | `for:` | Why |
|---|---|---|
| Host / endpoint reachability (`up == 0`, `probe_success == 0`) | **≥ 5m** (smart-home 3m) | ride out a restart or a scrape blip |
| Resource pressure (disk, memory, temp) | 10–30m | slow-moving; a brief spike is not an incident |
| Security tripwire / burst counter (log rules) | `0m` | the count window (`[10m]`) already debounces; catch the first occurrence |
| Point-in-time event (OOM kill, manual deploy) | `0m` | it either happened in the window or it didn't |

Default to **5m** for anything metric-based and reachability-shaped; drop to
`0m` only for log-burst counters and discrete events.

## 6. Inhibition

One incident should be one page. Add an `inhibit_rules` entry when alert B is a
**guaranteed consequence** of alert A, so B is redundant while A fires. Source
and target must share the `equal:` labels (or be targeted by a specific
`alertname`), and never inhibit something that could be an independent fault.

Live examples (`alertmanager.yml`):

- `AlertmanagerDown` inhibits `LokiRulerCannotNotify` and
  `LokiRulerNoAlertmanager` — if the published `:9093` is down, the ruler
  obviously cannot deliver, so that is the same incident; but
  `LokiRulerConfigReloadFailed` (a bad rule file) is **not** inhibited, because
  it is unrelated.
- `MonitoringBlackout` (service.yml `availability` group; fires when
  `count(up==0) + count(probe_success==0) >= 10`) inhibits `InstanceDown`,
  `EndpointDown`, `SmartHomeEndpointDown`, `CadvisorDown` and `NetExporterDown`,
  so a correlated reachability loss pages **once** instead of 15+ times (the
  2026-10-27 02:10–02:50 blackout, ~18 targets down across 4 hosts/subnets). The
  per-target detail stays visible in the Alertmanager UI.

Future work: a finer, **per-host** cascade — a host's `InstanceDown` inhibiting
only that host's `EndpointDown` / `CadvisorDown` / `ContainerOOMKilled` — so a
single dead host (below the blackout threshold) is also one page. That needs a
shared `host` label on the probe/host rules first, so it is deferred;
`MonitoringBlackout` covers the fleet-wide case today.

## 7. Routing

Set in `alertmanager.yml`; routing keys on labels, never on rule names:

| Match | Receiver | Transport |
|---|---|---|
| `severity="critical"` | `email-ops` | Pushover (high) **+** email, 1 h repeat |
| `severity="info"` | `email-info` | email only, grouped into a ~6 h digest, 24 h repeat |
| `source="loki"` (the rest → `warning`) | `pushover` | Pushover, per-host grouping |
| everything else | `pushover` (default) | Pushover |

- **Pushover** gets every page-worthy alert (critical + warning). It is a SaaS
  that shares no failure domain with the lab, so it is the channel that must
  always work. Critical is delivered to **both** Pushover and email, so a mail
  outage never drops a page.
- **Email** goes through the in-lab Proton bridge smarthost (`10.1.20.20:25`,
  STARTTLS, `smtp_auth_password_file`), the same relay Authelia uses. It carries
  criticals (a durable second record) and the info digest.
- Two info rules are high-rate (2026-10-31: `KomodoManualDeploy` ~35/day,
  `RequestAppsContainerChange` ~32/day). The info route groups hard
  (`group_interval: 6h`, `repeat_interval: 24h`) so a busy info rule is one
  digest mail, not a flood. If an info rule is noisier than it is useful, raise
  its threshold or lengthen its window rather than widening the route.

## 8. Testing a rule before it ships

Validate locally with the pinned engine images (same digests the hosts run) so
"it parses here" means "it parses there".

**Metric rules — syntax:**

```sh
docker run --rm -v "$PWD/stacks/monitoring/prometheus/rules:/r:ro" \
  prom/prometheus:v3.15.1 promtool check rules /r/*.yml
```

**Metric rules — behaviour (unit tests):** test files live in
`stacks/monitoring/prometheus/tests/` — **not** in `rules/`, which Prometheus
globs as `rules/*.yml` and would try to load as rules.

```sh
docker run --rm -v "$PWD/stacks/monitoring/prometheus:/p:ro" \
  prom/prometheus:v3.15.1 promtool test rules /p/tests/*_test.yml
```

**Alertmanager config:**

```sh
docker run --rm -v "$PWD/stacks/monitoring/alertmanager:/a:ro" \
  --entrypoint amtool prom/alertmanager:v0.34.1 \
  check-config /a/alertmanager.yml
```

(The secret files — `pushover-*`, `proton-bridge-smtp-password` — are installed
on the host by the monitoring `pre_deploy`, not in git; `amtool check-config`
validates structure and templates without them.)

**Log rules (LogQL):** there is no `lokitool` in the ruler image; lint with the
logcli/loki image or confirm against the live ruler on kdk-log-01:

```sh
# syntax of every rule file (cortextool-style check via the loki image)
docker run --rm -v "$PWD/stacks/loki/rules:/r:ro" \
  grafana/loki:3.7.9 -config.file=/dev/null -verify-config 2>/dev/null || true
# authoritative: the ruler loads clean and nothing is unhealthy
curl -s http://10.1.20.21:3100/prometheus/api/v1/rules | jq '.data.groups | length'
```

Prefer replaying a new log rule's LogQL over a past window in Grafana Explore
before shipping — a rule on a path regex must be checked against real client
and edge-probe (`kdk-edge-probe/1` UA) traffic first, so it does not page on a
probe or a 403.
