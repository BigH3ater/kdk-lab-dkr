# monitoring (kdk-mon-01)

The out-of-cluster monitoring stack: Prometheus, Grafana, Alertmanager (→ Pushover),
blackbox + idrac exporters, and the iDRAC fan controllers. It runs on **kdk-mon-01**
(Pi 5) so alerting survives an outage of everything it watches.

## Status: adopted (files-on-host), mirrored here

which Komodo deploys as the git-linked stack `monitoring-ext` (`komodo/resources.toml`:
`repo` + `run_directory = stacks/monitoring`, `project_name = monitoring`). Two things
are still rendered on the host rather than from 1Password, because kdk-mon-01 has no
`op` CLI and no `/etc/komodo/op.env`:

- `.env` — hand-rendered at `/opt/kdk-lab/monitoring/.env`; `pre_deploy` copies it
  next to the cloned compose. `.env.tpl` here is the cutover source (every item it
  names now exists in `kdk-cluster`: `grafana-admin`, `kdk-lab-idrac-user`,
  `kdk-ops/kdk-lab-idrac-fanctl-admin`).
- Alertmanager's secret **files** (`pushover-user-key`, `pushover-app-token`,
  `proton-bridge-smtp-password`) — pre-rendered once at
  `/opt/kdk-lab/monitoring/alertmanager/` (0400, uid 65534 = the alertmanager user)
  from `kdk-cluster/pushover-kdk-lab` and `proton-bridge-smtp-incluster`; `pre_deploy`
  installs them into the clone before every `up`.

**Why the install step exists (incident 2026-10-19):** git does not carry those files,
so on the first git-linked deploy (2026-09-21) Docker created empty *directories* at
the bind-mount paths. Alertmanager loaded fine, Prometheus kept firing, and every
notification failed with `read token_file: ... is a directory` — paging was silently
dead for four weeks (five alert batches, EndpointDown/InstanceDown/CadvisorDown, never
reached Pushover). Fix = replace the placeholders with the real files and **recreate**
(not restart) the container: a restart fails with "not a directory" because the
container rootfs still has directories at the mount points. The `pre_deploy` now does
the rmdir+install every deploy so a re-clone cannot regress it.

**To finish the cutover (follow-up):** install the op CLI + `op.env` on kdk-mon-01,
then switch `pre_deploy` to `op inject` for `.env` and `op read` for the three files.
Named volumes (`monitoring_*`) are unaffected.

## Fan control

Two `tigerblue77/dell_idrac_fan_controller` containers (`fanctl-hyp` → R720 BMC
10.1.3.86, `fanctl-nas` → R730xd BMC 10.1.3.25), replacing the retired custom
`ghcr.io/bigh3ater/kdk-lab-idrac-fan-control` image. Both controllers are **enabled**. `fanctl-hyp` (R720) (`MONITORING_ONLY_MODE=false`) — it applies a
static 20% profile below a 72°C CPU threshold and disables the third-party-PCIe
fan boost; the R720 dropped from ~12000 to ~3000 RPM (20%, headroom to 72°C to avoid 100% slam spikes). `fanctl-nas` (R730xd) runs a static 20% profile (~8400 → ~4800 RPM); its NVMe are cooled by a dedicated riser fan and the SAS HDDs sit ~39°C.

**To enable/adjust the R730xd:** set `MONITORING_ONLY_MODE: "false"` on `fanctl-nas`
(and/or tune `FAN_SPEED`) and redeploy — a reviewed one-line change. Watch
idrac-exporter fan RPM in Grafana afterward. Thermal safety is alerted independently via `prometheus/rules/thermal.yml`
(idrac-exporter metrics), so no controller self-metric is required.

## Dashboards and Prometheus config

Grafana provisions every JSON file under `grafana/dashboards/`, and each top-level
directory becomes a folder (`foldersFromFilesStructure`). New or edited files appear
within about 10 seconds of a deploy, with no restart, because the directory is
bind-mounted. Dashboards are read-only in the UI; change them here.

| Folder | Dashboards | Data |
|---|---|---|
| Overview | External Monitor | Prometheus |
| Hosts | Node Exporter Full (grafana.com 1860) | `node-external` |
| Hardware | ZFS Zpool Health, BMC overview (idrac) | `node-external` ZFS collectors, `idrac` |
| Services | Service Health | `blackbox-*` probes (the same probes the alerts use) |
| Logs | Logs Explorer, Traffic Access Logs | Loki: Docker logs (`host`/`stack`/`container`), syslog, Traefik JSON access logs from kdk-dkr-01 and dmz-01 |
| Network | UniFi Network, AdGuard Home, SNMP Interface Traffic | `unpoller`, `adguard`, `snmp` (stacks/net-exporters on kdk-dkr-01) |
| Containers | Containers Overview, Stack Detail, Backups | `cadvisor` (per container, `host`/`stack`/`service` labels); Backups uses docker-event-watch exit codes in Loki |
| Security | Security Advisories | Loki `{job="advisory-watch"}` (stacks/advisory-watch) and `{job="trivy"}` (stacks/trivy-scan), weekly |

The ZFS, BMC, Node Exporter, UniFi, AdGuard, SNMP and Traffic dashboards are ported from the k3s-era ConfigMaps in
`kdk-lab-old/apps/platform/monitoring-support/manifests/dashboards/`; their uids are
kept the same.

`prometheus/` is mounted as a **directory**. Komodo's `post_deploy` POSTs
`/-/reload`, so scrape-job and rule edits apply on deploy without a restart. An
invalid config makes the reload return 500, and that fails the deploy.
