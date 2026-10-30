---
title: kdk-nas-01
host: kdk-nas-01
fqdn: none
ip: 10.1.3.5
role: storage
tier: infra
status: live
provisioner: baremetal
hypervisor: none
vmid: none
console: TrueNAS UI; iDRAC/physical
standards: out-of-scope (not a Komodo compose host)
secrets: []
updated: 2026-10-30
tags: [homelab/host, role/storage, status/live, tier/infra]
---

# kdk-nas-01 — TrueNAS storage

Dell PowerEdge R730xd running TrueNAS; the lab's primary storage and NFS server.
Not a Komodo compose host — it is referenced by the stacks that mount its
datasets.

**Related:** [[network]] · [[backups]] · [[kdk-dkr-dmz-01]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Bulk storage, NFS server, SMB shares, TrueNAS VM host |
| Host | Dell PowerEdge R730xd (bare metal) |
| Address | `10.1.3.5` (VLAN 1); storage NIC `br0` alias at `172.16.30.2` (NFS net) |
| NFS | server for `172.16.30.0/24` (jumbo MTU 9000); per-host backup datasets `tankz3/backup/dkr/<host>`; clients `172.16.30.50` (dkr-01), `.51` (dmz-01) |
| SMB | bound to `10.1.3.5` (not offered on the storage bridge) — e.g. `\\10.1.3.5\dee-exchange` for [[kdk-dee-01]] |
| Guests | TrueNAS VMs: [[kdk-dkr-dmz-01]] and `kdk-pbs-01` |
| Fan/BMC | R730xd BMC steered over IPMI by the `fanctl-nas` controller on [[kdk-mon-01]]; drive-temp watchdog `/root/kdk-temp-watch.sh` |
| Blast radius | NFS/SMB offline lab-wide; backup targets unreachable |

## Notes

This page is a reference stub for the facts the repo already knows. The NFS and
SMB server configuration lives in TrueNAS (`nfs.config`, `sharing.nfs`,
`sharing.smb`), not in this repo. See [[network]] for the storage-network layout
and [[backups]] for the backup datasets.

## ACME / web certificate

Verified read-only 2026-10-30 (`midclt`, served-cert probe). TrueNAS-25.10.6.

**The web UI runs on the factory self-signed cert, *not* ACME.** `system.general.config`
→ `ui_certificate` is cert id 1 `truenas_default` (iXsystems self-signed,
`CN=localhost`, `SAN=DNS:localhost`), served on `https://truenas.kmkdp.com`
(`truenas.kmkdp.com` → `10.1.3.5`; `nas.kmkdp.com` is *not* this box — it falls
through the wildcard to the internal Traefik at `10.1.20.20`). TrueNAS
auto-regenerates `truenas_default` itself (last regenerated 2026-10-08, valid to
2027-11-09), so the UI shows a name-mismatch / untrusted warning but never
expires out from under you. Browsers trust it only if you accept the exception.

**ACME is configured on the box, and it works** — but it does not drive the UI
cert:

| Piece | Value |
|---|---|
| DNS authenticator (`acme.dns.authenticator.query`) | id 1 `kmkdp.com`, type **cloudflare**, using **Global API Key + email** (`cloudflare_email`, `api_key` set; `api_token` null) |
| ACME account (`acme.registration.query`) | id 1, Let's Encrypt `acme-v02`, status **valid** |
| ACME cert present (`certificate.query`) | id 3 `auth_kmkdp_ACME` — `CN=auth.kmkdp.com`, LE, `renew_days=10`, authenticator map `{DNS:auth.kmkdp.com: 1}`, valid **2026-10-24 → 2027-01-22** |

The only ACME cert is for `auth.kmkdp.com` (not the NAS's own name, and Authelia
now runs on the DMZ, not here — this cert is a historical leftover from the old
Authentik app, cf. the Dec-2025 `/mnt/tankz3/apps/authentik/certs` errors in
`middlewared.log`). It last renewed successfully **2026-10-24** and next renews
~2027-01-12; no recent renewal failures are in the logs.

**Cloudflare credential status (verified read-only 2026-10-30):** the authenticator
uses the Cloudflare **Global API Key**, not a scoped API token, so the 2026-10-27
Proxmox token revocation and 2026-10-29 token cleanup did **not** touch it.
`GET /user` → `success:true`; `GET /zones?name=kmkdp.com` → `success:true`,
one active zone. The credential is live and renewals are not failing — **no
outage fix is needed** (unlike pve.kmkdp.com, which was on a revoked scoped token).

### Recommendations (optional, owner's call — not applied)

1. **Least-privilege (match the Proxmox pattern):** migrate authenticator id 1
   off the account-wide Global API Key onto the shared scoped token
   `op://kdk-cluster/cloudflare-dns-acme-hosts/credential` (Zone:DNS:Edit on
   kmkdp.com). Deliver the token over ssh stdin, never pasted by hand:

   ```sh
   op read "op://kdk-cluster/cloudflare-dns-acme-hosts/credential" \
     | ssh kdkadmin@10.1.3.5 'T=$(cat); sudo midclt call acme.dns.authenticator.update 1 \
         "{\"attributes\": {\"authenticator\": \"cloudflare\", \"api_token\": \"$T\", \"cloudflare_email\": null, \"api_key\": null}}"'
   ```

   (Schema for 25.10.6: cloudflare attributes are `authenticator` (const), and one
   of `api_token`, or `cloudflare_email`+`api_key`.) After switching, confirm the
   old Global API Key can be retired from Cloudflare.

2. **Trusted UI cert (only if wanted):** the UI is self-signed by design. To make
   `https://truenas.kmkdp.com` present a Let's Encrypt cert, mint a fresh ACME
   cert for that name and point the UI at it (nothing today does this):

   ```sh
   sudo midclt call certificate.create '{"name": "truenas_kmkdp_ACME", "create_type": "CERTIFICATE_CREATE_ACME", "acme_directory_uri": "https://acme-v02.api.letsencrypt.org/directory", "dns_mapping": {"DNS:truenas.kmkdp.com": 1}, "csr_id": <id-of-a-CSR-for-truenas.kmkdp.com>, "renew_days": 10}' --job
   sudo midclt call system.general.update '{"ui_certificate": <new-cert-id>}'
   sudo midclt call system.general.ui_restart
   ```

   (`certificate.create` with `CERTIFICATE_CREATE_ACME` needs an existing CSR id;
   create the CSR first via `certificate.create` with
   `create_type=CERTIFICATE_CREATE_CSR` for `CN/SAN=truenas.kmkdp.com`.) Verify
   with `openssl s_client -connect 10.1.3.5:443 -servername truenas.kmkdp.com`.

3. **Stale cert:** `auth_kmkdp_ACME` (id 3) appears orphaned — Authelia lives on
   the DMZ now. Consider removing it so the box stops renewing a cert nothing here
   uses.
