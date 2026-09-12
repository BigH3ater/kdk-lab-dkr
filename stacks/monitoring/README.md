# monitoring (kdk-mon-01)

The out-of-cluster monitoring stack: Prometheus, Grafana, Alertmanager (→ Pushover),
blackbox + idrac exporters, and the iDRAC fan controllers. It runs on **kdk-mon-01**
(Pi 5) so alerting survives an outage of everything it watches.

## Status: adopted (files-on-host), mirrored here

This directory **mirrors** the live stack at `/opt/kdk-lab/monitoring` on kdk-mon-01,
which Komodo currently manages as `files_on_host` (see `komodo/resources.toml`
`monitoring-ext`). Changes are applied on the host and copied here for version
tracking. It is **not yet git-linked-deployed** (Komodo cloning this repo) because:

- Alertmanager reads secret **files** (`pushover-user-key`, `pushover-app-token`,
  `proton-bridge-smtp-password`) that must be owned by the container UID
  (`nobody:65534`) — rendering those in a git-clone run directory needs extra
  `pre_deploy` handling.
- kdk-mon-01 does not yet have the `op` CLI + `/etc/komodo/op.env`.

**To git-link (follow-up):** install op CLI + `op.env` on kdk-mon-01, resolve the
secret-file rendering (render pushover/smtp files in `pre_deploy` and `chown 65534`),
confirm the `.env.tpl` item names, then switch `monitoring-ext` in `resources.toml`
to a repo stack (`repo`, `run_directory = stacks/monitoring`, `pre_deploy` op inject,
`project_name = monitoring`). Named volumes (`monitoring_*`) persist across the switch.

## Fan control

Two `tigerblue77/dell_idrac_fan_controller` containers (`fanctl-hyp` → R720 BMC
10.1.3.86, `fanctl-nas` → R730xd BMC 10.1.3.25), replacing the retired custom
`ghcr.io/bigh3ater/kdk-lab-idrac-fan-control` image. `fanctl-hyp` (R720) is **enabled** (`MONITORING_ONLY_MODE=false`) — it applies a
static 20% profile below a 72°C CPU threshold and disables the third-party-PCIe
fan boost; the R720 dropped from ~12000 to ~3000 RPM (20%, headroom to 72°C to avoid 100% slam spikes). `fanctl-nas` (R730xd)
remains in `MONITORING_ONLY_MODE=true` (reads + logs, writes nothing).

**To enable/adjust the R730xd:** set `MONITORING_ONLY_MODE: "false"` on `fanctl-nas`
(and/or tune `FAN_SPEED`) and redeploy — a reviewed one-line change. Watch
idrac-exporter fan RPM in Grafana afterward. Thermal safety is alerted independently via `prometheus/rules/thermal.yml`
(idrac-exporter metrics), so no controller self-metric is required.
