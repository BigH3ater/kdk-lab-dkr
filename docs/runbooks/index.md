---
title: Runbooks
---

# Runbooks

Step-by-step procedures for the recurring operational tasks on this homelab.
Each one is written from a task actually performed — commands are real, not
illustrative.

- [Deploy a new service (Komodo + Traefik + GitOps)](deploy-new-service.md) — add a stack, route it through Traefik, ship it via the GitOps loop.
- [Add a Pushover alert](pushover-alerts.md) — wire a new alert into the per-host watchdog or a verify one-shot.
- [Troubleshoot the Windows DEE VM](windows-vm-dee.md) — the FastAPI DeeZy wrapper, its SMB mount, and the scheduled task.
- [Add a second DEE instance (kdk-dee-02)](dee-second-instance.md) — clone the Windows DEE VM as a second Tdarr DEE worker.
- [Add a book to LazyLibrarian](add-a-book.md) — staging import, adding the record (ISBN / OpenLibrary work id), the library-tree fallback; the HardCover 1-day block and the Prowlarr key in download URLs.
- [Troubleshoot Tdarr transcodes](tdarr-troubleshooting.md) — transcode/health errors, the DEE dependency, node flapping.
- [Jellyfin transcoding (ramdisk + Arc QSV)](jellyfin-transcoding.md) — the RAM-disk transcode scratch and the Arc A380 QSV/HDR settings.
- [Jellyfin SSO (Authelia OIDC)](jellyfin-sso.md) — install the SSO Authentication plugin, the `jellyfin` client secret, the login button, Quick Connect tests.
- [Security alerts (Loki ruler)](security-alerts.md) — what each security page means, first checks, where the log line is, how to silence.
- [Add a Loki alert rule](add-loki-alert-rule.md) — rule file shape, label conventions, test in Explore, verify the ruler load, fire, route, silence.
- [Add a hardened stack](add-hardened-stack.md) — from `stacks/_template` to a merged PR: baseline check, catalog entry, DNS/Authelia, PR checklist.
- [Rotate a secret](rotate-a-secret.md) — 1Password on the laptop, values over ssh stdin, hashes inside the container, verify without printing.
- [Komodo leak scan (count-only)](komodo-leak-scan.md) — repeat the 2026-10-31 Compose Config leak check against the Komodo DB; counts per collection, never values.
- [Handle Renovate PRs](handle-renovate-prs.md) — dashboard, merge order, what recreates, NUT and Pi pull rules, known-gaps refresh.
- [PostgreSQL major upgrade (16 → 18)](postgres-major-upgrade.md) — per-stack logical dump/restore for identity, jellystat and tandoor; the PG18 PGDATA gotcha; order, rollback, re-enabling the Renovate rule.
- [Post-merge validation](post-merge-validation.md) — the reviewer's read-only checks and the table posted on every PR.
- [linux-tools share](linux-tools-share.md) — `vmpool/linux-tools` NFS share on kdk-dkr-04 for portable build kits (thud): NAS create steps, mount, seed, verify, rollback.
- [Storage network & NFS](storage-network.md) — the `172.16.30.0/24` storage plane, the Tailscale-CGNAT collision that broke NFS, renumber + hung-mount procedures.
- [Host kernel crash, watchdog reset and pending kernels](host-kernel-crash.md) — the ICH9 TCO watchdog as the recovery path, crash signatures, triage after an unexpected reboot, the `panic_on_oops` baseline, the weekly pending-kernel check, crash-evidence options.
- [Host firewall (storage fence + DOCKER-USER)](host-firewall.md) — the nft storage-net fence and the DOCKER-USER egress policy on the two VMs: install, verify, log -> enforce, rollback.
- [Approval tasks (Vikunja ⇄ Home Assistant)](approval-tasks.md) — actionable phone notifications for Vikunja decisions; the job contract, task-declared choices, reminders, setup, and rollback.
- [Grocery planner — seed, run, and tune](grocery-planner.md) — seed Tandoor from scored real recipes, the weekly meal-plan + two-store list run, adding/swapping a recipe, changing the rules, troubleshooting.
- [wger — first deploy and onboarding](wger.md) — pilot Track A: 1Password items (incl. the JWT keypair from wger's generator), deploy, bootstrap-admin reset, PowerSync storage, Authelia header login, per-person API keys, iOS app login + offline test, backup check, rollback.
- [Exercise plan update (wger)](workout-tracking-update.md) — change an exercise, dose or cue and load it into both people's wger routines; also the 2026-11-06 retirement of the in-house tracker (host steps).
- [Home Assistant housekeeping](home-assistant-housekeeping.md) — standing rules: no `*.bak` in packages/dashboards, unused custom components out, dead Zigbee devices pruned, entity-registry orphan check, the daily-backup check before an upgrade.
- [Treadmill console HUD (MackTrack)](treadmill-hud.md) — the thud fork on the treadmill console: prerequisites (NordicFTMS, DHCP reservation), GitHub Actions build, pair/install/smoke/rollback from kdk-dkr-04 with `kdk/tm-*.sh`.
- [Treadmill → Home Assistant (retired)](treadmill-glassos-bridge.md) — retired 2026-11-08: the DIRCON→MQTT bridge, the HA treadmill package and dashboard; what was removed and where the HUD docs went.

Conventions used throughout:

- **Hosts / SSH** — `ssh kdkadmin@<host>` (key in the 1Password agent). Compose
  hosts: `kdk-dkr-01` `10.1.20.20`, `kdk-dkr-02` `10.1.30.21`, `kdk-dkr-03`
  `10.1.30.22`, `kdk-dkr-dmz-01` `192.168.191.20`, `kdk-mon-01` `10.1.20.30`,
  `kdk-log-01` `10.1.20.21` (Loki; SSH only from the admin sources).
- **Repo** — everything is GitOps from `BigH3ater/kdk-lab-dkr`. Change the repo,
  not the host. See [Komodo GitOps](../komodo-gitops.md).
- **Secrets** — 1Password (`kdk-cluster`, `kdk-ops`), `op` on the laptop only,
  values to hosts over ssh stdin ([Rotate a secret](rotate-a-secret.md)); Pushover
  creds live at `/etc/kdk/pushover.env` on every compose host.
