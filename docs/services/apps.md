---
title: Apps stack
---
# Apps stack

Small internal services on kdk-dkr-01.

| Service | Notes |
|---|---|
| ntp | cturra/ntp, host :123/udp — `ntp.kmkdp.com` |
| adguardhome-sync | dns-01 → dns-02/03 every 15 min (replaces the k3s CronJob) |
| rmfakecloud | `remarkable.kmkdp.com`; data migrated; readwise sync legs retired (custom images) |
| proton-bridge | SMTP relay on 10.1.20.20:25; keychain migrated |
| roborock | :555/:8881; config + TLS from the old k8s Secrets at /opt/kdk-lab/apps/roborock |

Secrets: kdk-dns-adguard-home-admin, rmfakecloud-app (kdk-cluster).
