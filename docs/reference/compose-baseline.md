---
title: Compose hardening baseline
status: active
tier: infra
tags: [docker, compose, security, ci]
---

# Compose hardening baseline

Every service in every `stacks/*/compose.yaml` (all hosts, not only the DMZ)
meets this baseline. If it can't, the service carries a documented exception or
skip label. `scripts/compose-baseline-check.py` enforces it locally and in the
`compose-baseline` GitHub Action, so a new stack can't fall below it.
New stacks start from [`stacks/_template`](https://github.com/BigH3ater/kdk-lab-dkr/tree/main/stacks/_template).

Source: the docker-hosts security review of 2026-10 (vault,
`10.Homelab/Security/docker-hosts-posture-2026-10.md`, "Baseline") and plan item A3.

## The standard

Put the baseline in an `x-hardened` anchor at the top of the file. Each service
starts with `<<: *hardened` and only adds or overrides what it needs. A key set
on the service replaces the anchor's value; it is not merged with it. The check
reads the **merged** service, so anything that comes from an anchor counts.

```yaml
x-hardened: &hardened
  security_opt: ["no-new-privileges:true"]
  cap_drop: [ALL]
  read_only: true
  tmpfs: [/tmp]
  restart: unless-stopped
  pids_limit: 256
  mem_limit: 512m
  logging:
    driver: json-file
    options: { max-size: "10m", max-file: "3" }
```

### Checked items

| Item | Required value | Why | Exception label key |
|---|---|---|---|
| `no-new-privileges` | `security_opt` contains `no-new-privileges:true` | setuid binaries in the image cannot gain privileges | skip only |
| `cap_drop` | `cap_drop: [ALL]`, then the smallest `cap_add`, each with a comment | root inside the container keeps no kernel capabilities it does not use | skip only |
| `read_only` | `read_only: true` plus `tmpfs` for scratch paths | a compromised process cannot drop tools or change the image | `kdk.baseline.skip: read_only` |
| `pids_limit` | `pids_limit:` (or `deploy.resources.limits.pids`) | a fork bomb can't take down the host | skip only |
| `mem_limit` | `mem_limit:` (or `deploy.resources.limits.memory`) | one leak can't OOM the whole host | skip only |
| `logging` | `json-file` (or `local`) with `max-size` and `max-file` (`10m` × `3`) | logs can't fill the disk; Alloy reads json-file | skip only |
| `restart` | `unless-stopped`; one-shot jobs `"no"` or `on-failure` | `always` restarts containers that were stopped on purpose | skip only |
| `image-pin` | tag at least `x.y` (aim for `x.y.z`, a date tag is allowed), never `latest`/`stable`/major-only; a `@sha256:` digest also passes; images built in the stack pass | deploys are reproducible and Renovate can track them | skip only |
| `healthcheck` | a `healthcheck:` that is not `disable: true` | the watchdog and `depends_on: service_healthy` can see a hung app | `kdk.baseline.skip: healthcheck` |
| `privileged` | not `privileged: true` | full host access; use `devices` + `cap_add` instead | `privileged` |
| `host-net` | not `network_mode: host` | host networking bypasses compose network isolation and DOCKER-USER | `host-net` |
| `devices` | no `devices:` and no `deploy.resources.reservations.devices` | direct hardware access | `devices` |
| `docker-sock` | no bind of `/var/run/docker.sock` | socket access gives root on the host | `docker-sock` |
| `rw-mount` (warning) | binds from `/etc`, `/opt/kdk-lab/*/secrets*`, `*.env` or `*secret*` are mounted `:ro` | a compromised app cannot change secrets or host config | `rw-mount` |

### Reviewed but not machine-checked

- `user:` is set wherever the image runs as root without needing it (uid ≥ 1000 or the
  image's documented non-root uid). LinuxServer images use `PUID`/`PGID` instead.
- `init: true` for node/python apps and shell loops, so SIGTERM reaches the process
  and children are reaped.
- Bind mounts are `:ro` unless the service writes to them.
- DB and cache tiers sit on an `internal: true` network. The app is the only service on both networks.
- Published ports bind to the host's VLAN IP or `127.0.0.1`. Anything only Traefik needs uses `expose:`.
- Docker socket access goes through the per-host read-only socket proxy. That proxy is the
  only `docker.sock` mount and carries the `docker-sock` exception.
- Secrets come only from `.env`, which the Komodo `pre_deploy` renders from `.env.tpl` (`op inject`).
  `/etc/kdk/pushover.env` is mounted as a single file, `:ro`.

## Exceptions and skips

Two labels, both on the service:

```yaml
labels:
  # Gated features: "<key>: <why>" pairs, separated by ";".
  kdk.baseline.exception: "host-net: HomeKit + mDNS advertising; devices: /dev/ttyUSB0 Zigbee dongle"
  # Required items the image genuinely cannot satisfy, comma separated.
  # Give the reason in a YAML comment on the same or the previous line.
  kdk.baseline.skip: "read_only,healthcheck"   # s6 writes /run at start; image has no health endpoint
```

- **`kdk.baseline.exception`** covers the gated items (`privileged`, `host-net`,
  `devices`, `docker-sock`, `rw-mount`). Each entry needs a key from that list and a
  non-empty reason.
- **`kdk.baseline.skip`** lists required items (`read_only`, `healthcheck`, …).
  Use it only after you have tried the item and the image broke. The reason goes in
  the YAML comment.
- An unknown key, an exception without a reason, or a skip entry that is not a
  required item counts as a `bad-label` gap. The check prints which entry is wrong.

Planned exceptions:

| Exception | Services |
|---|---|
| `host-net` | home-assistant, scrypted-homebridge, NUT, tailscale subnet router + DNS, node-exporter, net-exporters, observability-agent, and one-shots that need the host network (they move to bridge where they can) |
| `privileged` | none once the sweep is done: HA → `cap_add: [NET_ADMIN, NET_RAW]`, cAdvisor → `SYS_PTRACE` + devices |
| `devices` | `/dev/dri` (jellyfin, tdarr), `/dev/ttyUSB*` (zigbee2mqtt), the UPS USB device (NUT), `/dev/net/tun` (tailscale) |
| `docker-sock` | the per-host socket proxy only; the `docker:29-cli` sidecars until they move to the proxy |

## Choosing `cap_add`

1. Start from nothing:
   `docker run --rm --cap-drop ALL --security-opt no-new-privileges:true <image>`.
   Read the first error and add the capability it points to (`chown: Operation
   not permitted` → `CHOWN`, `setgroups` / `setuid` → `SETGID` / `SETUID`, bind
   below 1024 → `NET_BIND_SERVICE`). Repeat until it starts.
2. On a running container that already works, see what it actually holds:
   `pscap` (package `libcap-ng-utils`) on the host, or
   `grep Cap /proc/<pid>/status` and `capsh --decode=<hex>`.
3. Common sets:
   - **LinuxServer.io images (s6-overlay)**: `CHOWN, SETUID, SETGID, DAC_OVERRIDE, FOWNER`
     (s6 chowns `/config` and drops to `abc`). They usually also need `read_only` skipped,
     or a `tmpfs` for `/run`.
   - **postgres**: `CHOWN, SETUID, SETGID, DAC_OVERRIDE, FOWNER`, plus `tmpfs: [/tmp, /var/run/postgresql]`.
   - **Traefik on :80/:443**: on a bridge network, Docker (20.10 and later) sets
     `net.ipv4.ip_unprivileged_port_start=0` in the container, so binding :80/:443 works
     with no capability. `userland-proxy` makes no difference because the publish
     happens on the host side. Add `NET_BIND_SERVICE` only with `network_mode: host` or
     if the bind fails.
   - **tailscale**: `NET_ADMIN` (plus `NET_RAW` for some features) and `devices: [/dev/net/tun]`
     with the `devices` exception.
   - **Home Assistant (instead of `privileged`)**: `NET_ADMIN, NET_RAW` (Bluetooth, ping, DHCP discovery).
   - **cAdvisor (instead of `privileged`)**: `SYS_PTRACE`, usually with `devices: [/dev/kmsg]` (OOM events).
4. Comment every `cap_add` entry with the reason.

## Running the check

```bash
python3 scripts/compose-baseline-check.py                    # table of every service with gaps; exit 1 if any
python3 scripts/compose-baseline-check.py --summary          # + counts per item and the top 10 stacks
python3 scripts/compose-baseline-check.py --stack identity   # one stack (repeatable)
python3 scripts/compose-baseline-check.py --allow-known scripts/compose-baseline-known-gaps.txt
python3 scripts/compose-baseline-check.py --write-known scripts/compose-baseline-known-gaps.txt
scripts/compose-config-check.sh [stack ...]                  # docker compose config -q, placeholder env files
```

The script is stdlib-only and can parse the files in two ways:

- **PyYAML** (`import yaml` works): used automatically. It is the fast path, and the
  Action installs it.
- **No PyYAML** (bare Pi, minimal host): the script runs
  `docker compose -f <file> config --format json --no-interpolate --no-env-resolution --no-path-resolution`.
  This needs no `.env` or host files. Force either path with `--loader yaml|docker`.
  Both give the same result on the current tree.

Output is one row per service with gaps: `stack | service | missing items | exception`.
Exit codes: `0` clean, `1` gaps (or regressions with `--allow-known`), `2` a compose file
could not be loaded.

### Known gaps and the Action

`scripts/compose-baseline-known-gaps.txt` lists every gap in the tree today
(`stack/service: item,item`). It is the work list for the hardening sweep.
With `--allow-known`:

- a gap not in the file fails. That covers a known service that gets worse, a new
  service that isn't clean, and a `bad-label`. New items are marked `+` in the table;
  under GitHub Actions they appear as `::error` annotations on the compose file.
- a service that closed gaps passes. The script then prints a hint to regenerate the
  file. Regenerate it with `--write-known` in the same hardening PR and commit it,
  so the bar only moves up.
- never add lines to the file by hand to hide a regression. Use a skip or
  exception label with a reason.

The workflow `.github/workflows/compose-baseline.yml` runs on every pull request and
on pushes to `main`, with `contents: read` only. It has two jobs:

- **baseline**: installs PyYAML and runs
  `compose-baseline-check.py --allow-known scripts/compose-baseline-known-gaps.txt --summary`.
- **compose-config**: runs `scripts/compose-config-check.sh --ci`. The script works on a
  temp copy of `stacks/`. It turns each `.env.tpl` into a placeholder `.env` (every
  `KEY=…` becomes `KEY=placeholder`, so `op://` refs are never resolved), creates empty
  files for host-managed `env_file:` paths such as `/opt/kdk-lab/home-assistant/secrets.env`,
  and runs `docker compose config -q` per stack. It prints the placeholders it created
  and any stack it skipped. Run locally, it skips stacks whose host env file is missing
  instead of creating it with sudo.

When the sweep is done (the known-gaps file holds only the planned exceptions), drop
`--allow-known` from the workflow so every gap fails.

## Rollout order

Each hardening PR covers one stack or a few small related stacks. It applies the anchor,
redeploys, verifies the service, and regenerates the known-gaps file.

1. **DMZ (kdk-dkr-dmz-01)**: dmz-proxy, identity, wizarr, tandoor, audiobookshelf,
   jellyfin, immich, dmz-apps, then the one-shots (wizarr-sync, backup-dmz).
2. **kdk-dkr-01**: proxy, op-connect, komodo, docs, the long-running apps (media,
   apps, dashboard, actual, calibre, trailarr, jellystat, net-exporters,
   obsidian/readwise/remarkable pipelines), then the one-shots (arr-*, tdarr-*,
   backup-*, storage-health, dns-reconcile, docs-to-vault).
3. **kdk-log-01**: loki, advisory-watch, trivy-scan-log01 (hardened at build; verify with the check).
4. **kdk-dkr-02 / kdk-dkr-03**: home-assistant (drop `privileged`), scrypted-homebridge, nut.
5. **kdk-mon-01 and multi-host stacks**: monitoring, node-exporter, observability-agent
   (drop cAdvisor `privileged`).

Daemon-level defaults (`/etc/docker/daemon.json` `no-new-privileges`, log rotation,
`userland-proxy: false`) are host work from the same review. They add a second layer
but do not replace the per-service settings, which are what the check reads.

## Related

- [Docker network IPAM plan](docker-ipam.md): pin a stack's subnets.
- [Komodo GitOps](../komodo-gitops.md): adding a stack.
- [Deploy a new service](../runbooks/deploy-new-service.md)
