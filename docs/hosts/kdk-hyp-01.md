---
title: kdk-hyp-01
host: kdk-hyp-01
fqdn: none
ip: 10.1.1.100
role: hypervisor
tier: infra
status: live
provisioner: baremetal
hypervisor: none
vmid: none
console: Proxmox UI → Datacenter → kdk-hyp-01; iDRAC/physical
standards: out-of-scope (not a Komodo compose host)
secrets: []
updated: 2026-10-30
tags: [homelab/host, role/hypervisor, status/live, tier/infra]
---

# kdk-hyp-01 — Proxmox hypervisor

Dell PowerEdge R720 running Proxmox VE; the hypervisor for the lab's Linux VMs.
Not a Komodo compose host (no periphery) — it is referenced by the stacks and
hosts that run on top of it.

**Related:** [[kdk-dkr-01]] · [[kdk-log-01]] · [[network]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Proxmox VE hypervisor for the lab's Linux VMs |
| Host | Dell PowerEdge R720 (bare metal) |
| Address | `10.1.1.100` (VLAN 10, management); storage NIC `vmbr1` at `172.16.30.3` (NFS net) |
| Guests | VM 300 → [[kdk-dkr-01]], VM 320 → [[kdk-log-01]], VM 220 → [[kdk-dee-01]]; template VM `9000` (Debian 13 cloud-init base) |
| Backups | nightly Proxmox → PBS (`kdk-pbs-01`) vzdump; see [[backups]] |
| Fan/BMC | R720 BMC steered over IPMI by the `fanctl-hyp` controller on [[kdk-mon-01]] |
| Blast radius | Every VM it hosts goes down |

## Notes

This page is a reference stub for the facts the repo already knows. The
hypervisor is administered through the Proxmox UI and iDRAC, not through this
repo; VM lifecycle for the Komodo hosts is scripted (e.g.
`scripts/create-vm-kdk-log-01.sh`) and the template is VM `9000`.

## ACME renewal (Proxmox)

Proxmox manages the WebUI certificate for `pve.kmkdp.com` itself (Proxmox's
built-in ACME client, `proxmox-acme` / `acme.sh`), **not** Traefik. It is
independent of the container hosts' cert tooling.

| Field | Value |
|---|---|
| ACME account | `default` (Let's Encrypt production) |
| Domain | `acmedomain0: domain=pve.kmkdp.com,plugin=cloudflare` |
| Challenge | DNS-01 via Cloudflare |
| Plugin | `cloudflare` — `api cf`, data holds a single `CF_Token=…` (API token) |
| Zone pin | none — no `CF_Zone_ID`/`CF_Account_ID`, so `dns_cf` must *list* the zone |
| Config | `/etc/pve/priv/acme/plugins.cfg` (secret; cluster fs, root-only) |
| Renewal | nightly `acmerenew` task (runs after `aptupdate`, ~03–04 CDT) |

Because no `CF_Zone_ID` is pinned, the Cloudflare plugin resolves the zone at
run time with `GET /client/v4/zones?name=kmkdp.com`. That call needs the token
to be **valid** and to carry **Zone → Zone → Read** in addition to
**Zone → DNS → Edit**; a token that is revoked, or that has DNS:Edit only,
makes the zone lookup return nothing and `acme.sh` aborts with `invalid domain`.

### Failure mode: `invalid domain` / `Error add txt`

```
The validation for pve.kmkdp.com is pending!
[...] invalid domain
[...] Error add txt for domain:_acme-challenge.pve.kmkdp.com
TASK ERROR: command '... proxmox-acme setup cf pve.kmkdp.com' failed: exit code 1
```

This is the Cloudflare DNS plugin reporting that its zone lookup for
`kmkdp.com` returned nothing it could use. Diagnose, don't guess:

1. **Confirm the plugin token's Cloudflare status** (read-only, never print the
   token): verify it and list the zone with the token — on the host, extract
   `CF_Token` from `pvenode acme plugin config cloudflare` into a variable and
   `curl` `/user/tokens/verify` and `/zones?name=kmkdp.com`. `success:false` +
   error **1000 "Invalid API Token"** (or 9109 on the zones call) ⇒ the stored
   token is **revoked/expired/wrong** — replace it. `success:true` but the
   zones call returns **0 results** ⇒ the token is valid but lacks
   **Zone:Read** — add that scope (or pin `CF_Zone_ID`).
2. **The zone is on Cloudflare** — `dig +short NS kmkdp.com @1.1.1.1` returns
   `maya`/`coleman.ns.cloudflare.com`. `pve.kmkdp.com` needs no public A record
   for DNS-01 and intentionally has none; it is not in the `dns-reconcile` /
   AdGuard rewrite set.
3. **Check remaining validity** before it bites: `sudo pvenode cert info`
   (look at `pveproxy-ssl.pem` `notafter`). The LE cert is a 90-day cert.

### Fix (owner)

The lab's shared ACME token lives in 1Password at
`kdk-cluster/cloudflare-dns-acme-hosts`. Give the Proxmox plugin a token that
is **valid** and scoped **Zone:DNS:Edit + Zone:Read** on `kmkdp.com` (adding
Zone:Read to the shared token is additive and does not affect the other
consumers — Traefik on the container hosts, the Pi, `roborock-cert` lego).
Then, on `kdk-hyp-01`, write the token to a root-only file and update the
plugin:

```
umask 077
read -rs CF_NEW                                   # paste token; not echoed
printf 'CF_Token=%s\n' "$CF_NEW" > /root/cf-acme.data
sudo pvenode acme plugin set cloudflare --api cf --data /root/cf-acme.data
shred -u /root/cf-acme.data; unset CF_NEW
sudo pvenode acme cert order --force              # re-issue now
sudo pvenode cert info | grep -A1 pveproxy-ssl    # confirm new notafter
```

(If you prefer to keep a DNS:Edit-only token, instead pin the zone by adding a
`CF_Zone_ID=<kmkdp.com zone id>` line to the data file so `dns_cf` skips the
zone listing.)
