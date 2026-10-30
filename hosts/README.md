# hosts/ -- host-level config (applied by hand)

Komodo deploys compose stacks, not host files. Everything here is the source of truth
for what sits on each host outside Docker; it changes nothing until someone copies it
onto the host. The two VM cloud-init files carry the same files so a rebuild gets them
(`scripts/check-host-files-sync.py` fails if the copies drift).

| Path | Installs to | Hosts |
|---|---|---|
| `<host>/daemon.json` | `/etc/docker/daemon.json` | each host its own (address pool; dkr-01 keeps the nvidia runtime) |
| `_common/sshd_config.d/98-kdk-admin.conf` | `/etc/ssh/sshd_config.d/` | all five |
| `_common/sysctl.d/90-kdk-hardening.conf`, `91-kdk-nonlocal-bind.conf` | `/etc/sysctl.d/` | all five |
| `_common/apt/20auto-upgrades` | `/etc/apt/apt.conf.d/` | all five |
| `_common/apt/52kdk-unattended-upgrades` | `/etc/apt/apt.conf.d/52kdk-unattended-upgrades` | dkr-01, dmz-01 |
| `_common/apt/52kdk-unattended-upgrades-pi` | `/etc/apt/apt.conf.d/52kdk-unattended-upgrades` | mon-01, dkr-02, dkr-03 |
| `_common/authorized_keys.README.md` | (procedure: `from=` on the kdkadmin key) | all five |
| `<host>/nftables.d/kdk-storage.nft` | `/etc/nftables.d/kdk-storage.nft` | dkr-01, dmz-01 |
| `_common/systemd/kdk-storage-fence.service` | `/etc/systemd/system/` | dkr-01, dmz-01 |
| `_common/sbin/kdk-docker-user.sh` | `/usr/local/sbin/kdk-docker-user.sh` (0755) | dkr-01, dmz-01 |
| `<host>/kdk-docker-user.conf` | `/etc/default/kdk-docker-user` | dkr-01, dmz-01 |
| `_common/systemd/kdk-docker-user.{service,timer}` | `/etc/systemd/system/` | dkr-01, dmz-01 |
| `kdk-dkr-03/kdk-ups-l2.nmconnection` | NetworkManager (see `docs/hosts/kdk-dkr-03.md`) | dkr-03 |
| `kdk-hyp-01/kdk-template-backup.sh` | `/usr/local/sbin/kdk-template-backup.sh` (0755) | hyp-01 (PVE host, not a Docker host) |
| `kdk-hyp-01/kdk-template-backup.cron` | `/etc/cron.d/kdk-template-backup` | hyp-01 |

All files are `root:root 0644` unless noted. References: `docs/reference/docker-daemon.md`
(daemon.json), `docs/runbooks/host-firewall.md` (storage fence + DOCKER-USER).

## Order

Hosts least to most critical: **dkr-03 -> dkr-02 -> mon-01 -> dmz-01 -> dkr-01**.
Finish and verify one host before starting the next.

Before dmz-01 and dkr-01: security batch 2 must be live (Loki on kdk-log-01, every
Alloy pushing to `10.1.20.21`, UniFi `192.168.191.20 -> 10.1.20.21:3100`). The storage
fence cuts the old DMZ -> `172.16.30.50:3100` log path.

The compose half of security batch 7 (bind addresses, socket proxies, cAdvisor, the
`docker-forward` sidecar removal) merges only after `kdk-docker-user` is installed and
verified on dkr-01 (the sidecar is what keeps tailnet forwarding alive until then).

## Copy the files

From a checkout of this repo on the admin workstation (`H` = host name, `IP` = its address):

```sh
H=kdk-dkr-03; IP=10.1.30.22
ssh kdkadmin@$IP 'rm -rf /tmp/kdk-host && mkdir -p /tmp/kdk-host'
scp -r hosts/_common hosts/$H kdkadmin@$IP:/tmp/kdk-host/
ssh kdkadmin@$IP
cd /tmp/kdk-host
```

Keep this session open until the sshd step has been verified from a second terminal.

## Every host

**1. sysctl**

```sh
sudo install -m 644 _common/sysctl.d/90-kdk-hardening.conf _common/sysctl.d/91-kdk-nonlocal-bind.conf /etc/sysctl.d/
sudo sysctl --system | grep -E 'ptrace_scope|kptr_restrict|dmesg_restrict|sysrq|send_redirects|log_martians|nonlocal_bind'
```

**2. sshd** (the Pis already have an older copy; this overwrites it)

```sh
sudo install -m 644 _common/sshd_config.d/98-kdk-admin.conf /etc/ssh/sshd_config.d/98-kdk-admin.conf
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T | grep -Ei '^(permitrootlogin|x11forwarding|allowtcpforwarding|allowagentforwarding|allowusers|maxauthtries|logingracetime|passwordauthentication) '
# expect: no / no / no / no / kdkadmin / 3 / 20 / no
```

Then, from a second terminal: `ssh kdkadmin@$IP true`. Only then close the first session.

**3. kdkadmin key `from=`**: run the one-liner in `_common/authorized_keys.README.md`,
then test a new login from a second terminal again.

**4. daemon.json**: follow `docs/reference/docker-daemon.md` -> "Apply on one host"
(install, `systemctl reload docker` for live-restore, verify, `systemctl restart docker`,
recreate the host's stacks except `nut`).

**5. unattended-upgrades** (only after step 4 shows `LiveRestoreEnabled: true`; a
Docker CE upgrade restarts dockerd)

```sh
sudo install -m 644 _common/apt/20auto-upgrades /etc/apt/apt.conf.d/20auto-upgrades
# VMs (dkr-01, dmz-01):
sudo install -m 644 _common/apt/52kdk-unattended-upgrades /etc/apt/apt.conf.d/52kdk-unattended-upgrades
# Pis (mon-01, dkr-02, dkr-03):
sudo install -m 644 _common/apt/52kdk-unattended-upgrades-pi /etc/apt/apt.conf.d/52kdk-unattended-upgrades
sudo apt-config dump | grep -A8 '^Unattended-Upgrade::Origins-Pattern'
sudo unattended-upgrades --dry-run --debug 2>&1 | grep -E 'Allowed origins|Packages that will be upgraded'
```

The dry run lists the Docker (and on the Pis the Raspberry Pi Foundation) origin and the
packages it would take. The Pis then reboot themselves at 04:30 when a kernel/libc update
asks for it; the VMs keep owner-scheduled reboots (dmz-01 has had a kernel pending since
2026-09; schedule it).

**6. Komodo Periphery bind address** (C-02/H-09): in `/etc/komodo/periphery.config.toml`
set `bind_ip` to the host's VLAN address, then `sudo systemctl restart periphery`
(wait for any running Komodo deploy to finish first):

| Host | `bind_ip` |
|---|---|
| kdk-dkr-01 | `10.1.20.20` |
| kdk-dkr-dmz-01 | `192.168.191.20` |
| kdk-mon-01 | `10.1.20.30` |
| kdk-dkr-02 | `10.1.30.21` |
| kdk-dkr-03 | `10.1.30.22` |

Optional second fence: `allowed_ips = ["10.1.20.20"]` (Komodo Core's address as every
other host sees it). On dkr-01 itself Core arrives from its bridge, so there it would be
`["10.1.20.20", "10.208.0.0/16"]`. Check: Komodo UI -> Servers shows all hosts OK;
`sudo ss -lntp | grep 8120` shows the VLAN address only.

**7. kdkadmin out of the `docker` group** (plan A2: the socket is root-equivalent;
`sudo docker` keeps working, and the sessions/backup jobs already use it). The Pis
never had it; both VMs do:

```sh
sudo gpasswd -d kdkadmin docker && id -nG kdkadmin     # no "docker"
```

Takes effect on the next login.

## kdk-dkr-02 only

The `userland-proxy: false` pre-test: right after step 4, `nc -zv 127.0.0.1 1883` on the
host and Home Assistant -> Settings -> Devices & services -> MQTT shows connected. If not,
see the rollback in `docs/reference/docker-daemon.md` (drop the one key).

## kdk-mon-01 only (hosts report M-07)

avahi is not used on mon-01 (it is on dkr-02/dkr-03 for HomeKit; leave those):

```sh
sudo systemctl disable --now avahi-daemon.socket avahi-daemon.service
sudo systemctl mask avahi-daemon.socket avahi-daemon.service
sudo ss -lunp | grep -c ':5353 '   # -> 0
```

## kdk-dkr-dmz-01 and kdk-dkr-01

After the steps above, the storage fence and the DOCKER-USER unit:
`docs/runbooks/host-firewall.md` (dmz-01 first; it runs `MODE=enforce`, dkr-01 starts in
`MODE=log`). The same runbook masks `rpcbind` and turns LLMNR off (M-07) on both VMs.

Clean up: `rm -rf /tmp/kdk-host` on the host when done.

## VS Code Remote-SSH

VS Code Remote-SSH tunnels its server over a local port forward, so the sshd
baseline sets `AllowTcpForwarding local` (not `no`): local forwards only, and
`AllowUsers kdkadmin` already limits that to the admin account; remote and
gateway forwards stay off. Keep VS Code's `remote.SSH.remoteServerListenOnSocket`
at its default (false); the Unix-socket mode is refused by the Pi sshd. A `Match`
block cannot do this on Debian (drop-ins are included at the top of sshd_config).
Key auth comes from the 1Password agent via `IdentityAgent ~/.1password/agent.sock`
in `~/.ssh/config`.

## Legacy boot units (removed 2026-10-30)

Before Komodo, each Pi started its stack with a `compose-<stack>.service` systemd unit
(`docker compose up -d --remove-orphans` in `/opt/kdk-lab/<stack>`). Those units were left
enabled after the stacks moved to Komodo, and because they used the same compose project
name, the first unattended-upgrades reboot (2026-10-30 04:31, all three Pis) relaunched the
OLD compose files over the Komodo-managed containers: mon-01 lost the Alertmanager publish
and ran old images for ~1.5 h, dkr-03 got Scrypted v0.123 and homebridge back. Fixed by
`systemctl disable compose-monitoring.service` (mon-01), `compose-scrypted-homebridge.service`
(dkr-03), `compose-home-assistant.service` (dkr-02, had no compose file left and failed
harmlessly), retiring the legacy compose files and redeploying from Komodo. Rule: a host
may not carry any compose unit or compose file outside the Komodo clone; check with
`systemctl list-unit-files | grep -E '^compose'` after adopting a stack.
