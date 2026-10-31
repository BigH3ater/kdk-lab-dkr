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
| NFS | server for `172.16.30.0/24` (jumbo MTU 9000); per-host backup datasets `tankz3/backup/dkr/<host>`; clients `172.16.30.50` (dkr-01), `.51` (dmz-01), `.52` (dkr-04: `media` + `backup` only, added 2026-10-30) |
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

State as of **2026-10-30 22:10Z** (TrueNAS-25.10.6), after the owner-approved switch:

| Piece | Value |
|---|---|
| Web UI certificate | id 5 `truenas_kmkdp_acme`, Let's Encrypt, `CN/SAN=truenas.kmkdp.com`, valid 2026-10-30 → 2027-01-28, `renew_days=30` (TrueNAS renews it automatically 30 days before expiry) |
| CSR it was issued from | id 4 `truenas_kmkdp_csr` (RSA 2048). Keep it: the ACME cert was created from it |
| DNS authenticator | id 1 `kmkdp.com`, type **cloudflare**, **scoped API token only** (`api_token` set; `cloudflare_email`/`api_key` cleared). Token = the shared ACME token `op://kdk-cluster/cloudflare-dns-acme-hosts/credential` (Zone:DNS:Edit on kmkdp.com), the same one Traefik, roborock-cert and Proxmox use |
| ACME account | id 1, Let's Encrypt `acme-v02` production |
| Factory cert | id 1 `truenas_default` (self-signed, `CN=localhost`) stays on the box but serves nothing |

`truenas.kmkdp.com` → `10.1.3.5` (AdGuard rewrite). `nas.kmkdp.com` is **not** this box:
it falls through the wildcard to the internal Traefik at `10.1.20.20`.

History: until 2026-10-30 the UI served the factory self-signed cert and the
authenticator held the account-wide **Cloudflare Global API Key**. The only ACME cert
was `auth_kmkdp_ACME` (`auth.kmkdp.com`, a leftover from the old Authentik app); it was
deleted, and the Global API Key was rotated by the owner the same evening.

### Verify

```sh
echo | openssl s_client -connect 10.1.3.5:443 -servername truenas.kmkdp.com 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates
ssh kdkadmin@10.1.3.5 'sudo midclt call certificate.query' \
  | python3 -c 'import sys,json; [print(c["id"],c["name"],c["common"],c["until"]) for c in json.load(sys.stdin)]'
```

### Rotating the shared ACME token

When `cloudflare-dns-acme-hosts` is rolled, update TrueNAS the same way it was set up:
the token travels from 1Password over ssh stdin into the TrueNAS API client, so it
never appears on a command line on the NAS:

```sh
op read "op://kdk-cluster/cloudflare-dns-acme-hosts/credential" | ssh kdkadmin@10.1.3.5 'sudo python3 -c "
import sys
from truenas_api_client import Client
t=sys.stdin.read().strip()
with Client() as c:
    c.call(\"acme.dns.authenticator.update\",1,{\"attributes\":{\"authenticator\":\"cloudflare\",\"api_token\":t,\"cloudflare_email\":None,\"api_key\":None}})
    print(\"updated\")
"'
```

Also update Proxmox (docs/hosts/kdk-hyp-01.md "ACME renewal") and redeploy the
stacks that render the same token (proxy, dmz-proxy, roborock-cert).
