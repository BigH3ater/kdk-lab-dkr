# kdkadmin key: source restriction (`from=`)

`kdkadmin` keeps `NOPASSWD: ALL` (owner decision, security plan A2), so the one
`kdkadmin-ssh-key` is a lab-wide root credential. The mitigation is to make the key
useless from anywhere but the admin sources, with an OpenSSH `from=` option on the
key line in `~kdkadmin/.ssh/authorized_keys` on every host.

## Allowed sources

| Source | Why |
|---|---|
| `10.1.30.0/24` | users VLAN (admin workstations; no single-host DHCP pin by owner decision) |
| `10.1.20.20` | kdk-dkr-01: `stacks/backup-collect` pulls `/opt/kdk-lab` from mon-01, dkr-02 and dkr-03 over SSH with this key (its container traffic is masqueraded to 10.1.20.20). Without it the nightly collect fails and `backup-verify` pages. |
| `100.64.0.0/10` | tailnet clients (the subnet router does not SNAT, so lab hosts see the 100.x address) |
| `192.168.3.0/24` | break-glass WireGuard VPN (whole /24 by standing rule) |

Not included: `192.168.2.3`, which logged in to three hosts in the week of the
hosts audit (report L-05). Identify it first; if it is expected, add it to the list
on every host; if not, rotate `op://kdk-ops/kdkadmin-ssh-key`.

## Apply (per host)

Keep one SSH session open while you do this, and test a new login from a second
terminal before closing it. Console access is the fallback (Proxmox / TrueNAS VM
console for the VMs, keyboard for the Pis).

```sh
# Prefix the kdkadmin key with from="..."; idempotent: only a line that still STARTS
# with the key type is changed, so a second run (or an already-restricted line) is a no-op.
sudo sed -i -E 's#^(ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIISxVOFP6r1EKbRDgtXctnqNII6EEypDpBax3LyOBxDb( .*)?)$#from="10.1.30.0/24,10.1.20.20,100.64.0.0/10,192.168.3.0/24" \1#' /home/kdkadmin/.ssh/authorized_keys

# Check: the key line now starts with from="...", nothing else changed
sudo grep -c '^from="10.1.30.0/24,10.1.20.20,100.64.0.0/10,192.168.3.0/24" ssh-ed25519 ' /home/kdkadmin/.ssh/authorized_keys   # -> 1
```

`sshd` reads `authorized_keys` on every login: no reload needed. Verify from the
workstation (`ssh kdkadmin@<host> true`). For mon-01, dkr-02 and dkr-03 the check is
the next nightly `backup-collect` run: `backup-verify` stays quiet and each host's
`/mnt/backup/dkr/<host>/.last-backup` is fresh.

## Rebuilds

`cloud-init/kdk-dkr-dmz-01-user-data.yaml` writes the key with the same `from=`
prefix. kdk-dkr-01's key comes from the Proxmox template (`ciuser`/`sshkeys` on VM
300), not from the repo: re-run the one-liner after a rebuild. The Pis are imaged by
hand: run it after imaging.

## Rollback

```sh
sudo sed -i -E 's#^from="10\.1\.30\.0/24,10\.1\.20\.20,100\.64\.0\.0/10,192\.168\.3\.0/24" ##' /home/kdkadmin/.ssh/authorized_keys
```
