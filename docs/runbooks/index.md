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
- [Troubleshoot Tdarr transcodes](tdarr-troubleshooting.md) — transcode/health errors, the DEE dependency, node flapping.
- [Jellyfin transcoding (ramdisk + Arc QSV)](jellyfin-transcoding.md) — the RAM-disk transcode scratch and the Arc A380 QSV/HDR settings.
- [Storage network & NFS](storage-network.md) — the `172.16.30.0/24` storage plane, the Tailscale-CGNAT collision that broke NFS, renumber + hung-mount procedures.

Conventions used throughout:

- **Hosts / SSH** — `ssh kdkadmin@<host>` (key in the 1Password agent). Compose
  hosts: `kdk-dkr-01` `10.1.20.20`, `kdk-dkr-02` `10.1.30.21`, `kdk-dkr-03`
  `10.1.30.22`, `kdk-dkr-dmz-01` `192.168.191.20`, `kdk-mon-01` `10.1.20.30`.
- **Repo** — everything is GitOps from `BigH3ater/kdk-lab-dkr`. Change the repo,
  not the host. See [Komodo GitOps](../komodo-gitops.md).
- **Secrets** — 1Password (`kdk-cluster`, `kdk-ops`); Pushover creds live at
  `/etc/kdk/pushover.env` on every compose host.
