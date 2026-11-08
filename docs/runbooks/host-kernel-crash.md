---
title: Host kernel crash, watchdog reset and pending kernels
updated: 2026-11-08
---

# Host kernel crash, watchdog reset and pending kernels

What happens when a VM's kernel dies, how to read the evidence afterwards, the
sysctl baseline that makes it reboot quickly, and the weekly check that keeps an
installed-but-not-running kernel from waiting until a crash picks the reboot time.
Written from the kdk-dkr-01 crashes on 2026-10-31 and 2026-11-08 (full timeline in
the vault: `10.Homelab/Followups/dkr-01-reboot-2026-11-08.md`).

## The recovery path: QEMU ICH9 TCO watchdog

kdk-dkr-01 is a `q35` machine, which emulates an Intel ICH9 chipset including
its **TCO watchdog timer** (check another VM with `cat /sys/class/watchdog/watchdog0/identity`).
Debian loads `iTCO_wdt` for it automatically (`/dev/watchdog0`), and QEMU leaves
the timer running, so the kernel's watchdog core (`watchdogd` kthread) pings it
on its own with nobody having configured anything:

| Fact | Value / where to see it |
|---|---|
| Driver | `iTCO_wdt` (`cat /sys/class/watchdog/watchdog0/identity`) |
| Heartbeat | 30 s (`cat /sys/class/watchdog/watchdog0/timeout`; boot line `iTCO_wdt ... heartbeat=30 sec (nowayout=0)`) |
| Who pings it | the kernel (`watchdogd`), not systemd (`RuntimeWatchdogUSec=0`) and not the `watchdog` package (not installed). `state=inactive` in sysfs only means no userspace opener |
| When it fires | the kernel is wedged hard enough that `watchdogd` cannot run until the TCO expires, then QEMU performs a **system reset** of the guest (observed: 54 s and 57 s after the oops) |
| What it is not | Proxmox's `watchdog:` VM option / HA fencing (VM 300 has none), unattended-upgrades (`Automatic-Reboot "false"`) |

This is the **deliberate** last-resort recovery path: keep it. It recovered
kdk-dkr-01 in about 90 s on both crash dates; without it the VM stays wedged
until someone presses Reset in Proxmox. With the panic baseline the kernel reboots
itself 10 s after an oops (see [the panic baseline](#panic-baseline)), so the TCO
only acts when the kernel wedges without oopsing first.

### What a TCO reset looks like, and how to tell it from a power event

| Evidence | TCO reset (guest reset) | Host power / hypervisor event |
|---|---|---|
| Previous boot's journal (`journalctl -b -1 -n 50`) | ends **mid-crash** (oops / lockup lines), no `systemd-shutdown` lines | ends mid-normal-logging (power) or with a clean shutdown sequence (qmshutdown) |
| kdk-hyp-01 `journalctl -k` at that minute | `vfio-pci 0000:42:00.0: resetting` (dkr-01's GPU) and nothing else | the host itself rebooted (`journalctl --list-boots` on hyp-01), or NUT/`qmshutdown` lines |
| Proxmox task log (`/var/log/pve/tasks/index`) | **no** `qmreset`/`qmstop`/`qmreboot` for the VM | a task, or a host boot |
| QEMU process (`ps -o lstart -p $(cat /var/run/qemu-server/300.pid)` on hyp-01) | start time **before** the reboot: same process, in-place reset | start time **after**: the VM was started fresh |
| Other VMs on hyp-01 / `node_boot_time_seconds` | only this VM changed | every VM (and hyp-01) rebooted |
| Loki `{host="<vm>", app="kernel"}` | oops / lockup lines up to ~a minute before the reset | nothing unusual |

## Crash signatures seen so far

| # | Signature (first lines) | Kernel | Dates | Status |
|---|---|---|---|---|
| 1 | `BUG: kernel NULL pointer dereference, address: 0000000000000008` → `Oops [#1]` on CPU n, comm **`kswapd0`**, `RIP: list_lru_add+0x83` ← `workingset_update_node` ← `__filemap_remove_folio` ← `shrink_folio_list` ← `evict_folios` (MGLRU) ← `balance_pgdat`; then `kswapd0[131] exited with irqs disabled` | Debian 6.12.113 | dkr-01 2026-10-31 06:37Z, 2026-11-08 08:39:30Z | presumed fixed in 6.12.115 (`mm, madvise: fix potential workingset node list_lru leaks`), medium confidence; recurrence on .115 = Debian bug + trixie-backports kernel |
| 2 | follow-on wedge after #1: `rcu: INFO: rcu_preempt self-detected stall on CPU`, NMI backtraces ending in `_raw_spin_lock` / `kvm_wait` (often in NFS read completion: `nfs4_read_done`, `nfs_file_read`), then `watchdog: BUG: soft lockup - CPU#k stuck for 22s!` growing to ~52 s, then silence and a TCO reset ~55 s after the oops | same | same | a **victim**, not a cause: kswapd died holding a spinlock with IRQs off. NFS shows up only because its completion path wanted the same lock. With `panic_on_oops=1` this phase no longer happens |

A soft lockup **without** an oops before it is a different problem (hypervisor
steal, a real driver loop). Check `node_cpu_seconds_total{mode="steal"}` and hyp-01
load first.

## Triage after an unexpected reboot

`HostRebooted` (Prometheus) or `KernelOopsOrLockup` (Loki) paged, or uptime is
suspiciously short:

1. **Was it a crash?** On the host: `journalctl --list-boots | tail -3`, then
   `journalctl -b -1 -n 60 --no-pager`. A journal that ends mid-oops/lockup = crash.
2. **Find the first oops line and its RIP.** Everything after the first one is
   fallout:
   ```sh
   sudo journalctl -b -1 -k --no-pager | grep -nE 'BUG: kernel|Oops|kernel BUG at|general protection|RIP:|soft lockup|rcu_preempt|hung_task|Kernel panic' | head -20
   sudo journalctl -b -1 -k --no-pager | grep -A40 -m1 -E 'BUG: kernel|Oops|kernel BUG at|general protection'
   ```
   Note: comm (`kswapd0`, a container process...), `RIP: <function>+0x..`, the
   call trace, the `Tainted:` flags, and `uname -r` of that boot (`journalctl -b -1 -k | head -1`).
3. **Local journal incomplete?** journald often stops flushing seconds into the
   wedge. Loki usually has more (rsyslog kept forwarding for ~55 s on 11-08):
   Grafana → Explore → Loki `{job="syslog", host="kdk-dkr-01", app="kernel"}`
   around the reset, or the `KernelOopsOrLockup` alert's labels.
4. **What reset it?** Use the [TCO vs power table](#what-a-tco-reset-looks-like-and-how-to-tell-it-from-a-power-event).
   With the panic baseline installed, expect `Kernel panic - not syncing: Fatal exception`
   as the last line and a reboot 10 s later instead of a TCO reset.
5. **Match a signature** above. New one: write a vault Followups note with the
   first-oops block, kernel version and load at the time (Prometheus/cAdvisor).
6. **Kernel version:** run the [pending-kernel check](#weekly-pending-kernel-check).
   If the crash kernel had a newer one waiting, the reboot already moved to it
   (`uname -r`); confirm the GRUB default is the newest (`GRUB_DEFAULT=0` and
   `ls -t /boot/vmlinuz-*`).

## Panic baseline

`hosts/_common/sysctl.d/92-kdk-panic.conf` (VMs: kdk-dkr-01, kdk-dkr-dmz-01,
kdk-dkr-04, kdk-log-01; also in the three VM cloud-init files):

| sysctl | Value | Why |
|---|---|---|
| `kernel.panic_on_oops` | `1` | an oops panics instead of leaving a half-dead kernel that wedges |
| `kernel.panic` | `10` | reboot 10 s after a panic (lets the panic text reach the console and rsyslog) |
| `kernel.softlockup_panic` | `0` (unchanged) | a descheduled vCPU (hyp-01 busy, vzdump) trips soft lockups on a healthy guest; the real wedge is covered by `panic_on_oops` + TCO, and Loki pages on the lines |
| `kernel.hung_task_panic` | `0` (unchanged) | NFS mounts are `hard`: a kdk-nas-01 reboot would reboot every VM |
| `kernel.panic_on_rcu_stall`, `panic_on_warn` | `0` (unchanged) | false-positive risk as above; the NVIDIA module emits WARNs |

Install (per VM, from a checkout, see `hosts/README.md`):

```sh
sudo install -m 644 _common/sysctl.d/92-kdk-panic.conf /etc/sysctl.d/92-kdk-panic.conf
sudo sysctl --system >/dev/null
sysctl kernel.panic_on_oops kernel.panic kernel.softlockup_panic
# expect: kernel.panic_on_oops = 1, kernel.panic = 10, kernel.softlockup_panic = 0
```

Rollback: `sudo rm /etc/sysctl.d/92-kdk-panic.conf && sudo sysctl -w kernel.panic_on_oops=0 kernel.panic=0`.

The Pis are not in scope yet (bare metal with their own bcm2835 watchdog and the
Raspberry Pi kernel; decide separately). [The weekly check](#weekly-pending-kernel-check) flags a VM without
the baseline as `BASELINE`.

## Weekly: pending-kernel check

unattended-upgrades installs new kernels on every host, but the VMs never reboot
on their own (`Automatic-Reboot "false"`; dkr-01 is the tailnet router). On
2026-11-08 kdk-dkr-01 had been running 6.12.113 for five days with 6.12.115
installed; the crash chose the reboot. Part of the weekly maintenance run
(homelab-weekly-update skill, Step 5 "Not covered by Renovate"), from the laptop:

```sh
scripts/kernel-pending-check.sh            # all Docker hosts + log-01 + mon-01
scripts/kernel-pending-check.sh kdk-hyp-01=10.1.1.100   # one host / extra hosts
```

Read-only, one ssh per host, no sudo. Columns: host, running kernel, newest
installed kernel of the same flavour, the day it was installed, days since,
uptime, `panic_on_oops/panic`, status. Exit 0 = all OK, 1 = a `PENDING` or
`BASELINE` row, 2 = a host was unreachable.

| Status | Do |
|---|---|
| `PENDING` | put the reboot on the owner's list: a quiet window, one host at a time (dkr-01 last; it carries Komodo, Connect and the tailnet router). After the reboot re-run the check: `OK` |
| `BASELINE` | install `92-kdk-panic.conf` ([Panic baseline](#panic-baseline)) |
| `SSH-FAIL` | note it; never work around it with another credential |

Output on 2026-11-08 (after dkr-01's crash reboot, before the baseline was installed):

```text
HOST            RUNNING                       NEWEST                        INSTALLED  DAYS UPTIME OOPS/PNC STATUS
kdk-dkr-01      6.12.115+deb13.1-amd64        6.12.115+deb13.1-amd64        2026-11-03    5     0d 0/0      BASELINE
kdk-dkr-04      6.12.113+deb13-cloud-amd64    6.12.115+deb13.1-cloud-amd64  2026-11-04    4     8d 0/0      PENDING
kdk-dkr-dmz-01  6.12.107+deb13-amd64          6.12.115+deb13.1-amd64        2026-11-03    5    52d 0/0      PENDING
kdk-log-01      6.12.95+deb13-cloud-amd64     6.12.115+deb13.1-cloud-amd64  2026-11-03    5    11d 0/0      PENDING
kdk-mon-01      6.18.60+rpt-rpi-2712          6.18.60+rpt-rpi-2712          2026-11-06    2     1d 0/0      OK
kdk-dkr-02      6.18.60+rpt-rpi-2712          6.18.60+rpt-rpi-2712          2026-11-06    2     1d 0/0      OK
kdk-dkr-03      6.18.60+rpt-rpi-2712          6.18.60+rpt-rpi-2712          2026-11-06    2     1d 0/0      OK
```

## Crash evidence persistence (not implemented yet)

On 2026-10-31 the full oops survived only in Loki (the local journal had 3 lines).
Options for keeping the next one locally:

| Option | Works here? | Cost / risk |
|---|---|---|
| `efi_pstore` | **no**: the VMs boot SeaBIOS (`/sys/firmware/efi` absent), so there are no EFI variables to write to | would need an OVMF rebuild of the VM |
| `ramoops` (pstore in reserved RAM) | fragile: needs a `memmap=`-reserved region plus `ramoops.mem_address`, and relies on QEMU keeping guest RAM across a system reset | kernel cmdline change + reboot; easy to get silently wrong |
| `kdump-tools` | yes | `crashkernel=512M` reserved (dkr-01 has 47 GiB), a reboot to activate, and it only triggers on a **panic** (needs `panic_on_oops=1`, now in place). The TCO keeps counting while the capture kernel writes the dump, so a big vmcore can be cut off; set `MAKEDUMP_ARGS="-c -d 31"` and rely on the `dmesg.*` file it writes |
| Serial console logged on kdk-hyp-01 | yes, most complete | dkr-01 already boots with `console=ttyS0,115200`. The hypervisor records everything the guest kernel prints, up to the final panic line, outside the guest, unaffected by the TCO, no guest RAM. Needs a hand change on hyp-01 |
| `netconsole` to kdk-log-01 | partly | kernel-level UDP, survives a dead rsyslog but not a dead virtio-net / network stack; raw lines, not syslog format |

**Recommendation: serial console logging on kdk-hyp-01**, kdump-tools only if a
crash ever needs a vmcore. Hand steps for the owner (hypervisor side, not in any PR yet):

1. On hyp-01, check what VM 300 has: `qm config 300 | grep -E '^serial|^vga'`.
2. Keep `serial0` free for `qm terminal 300`; add a second port only for logging:
   `qm set 300 --serial1 socket` (takes effect at the next VM cold start: `qm shutdown 300 && qm start 300`, in a quiet window).
3. In the guest, add `console=ttyS1,115200` after the existing `console=ttyS0,115200`
   in `GRUB_CMDLINE_LINUX` (`/etc/default/grub`), `sudo update-grub`; same reboot.
4. On hyp-01, a logger unit that reconnects after every VM restart:
   `socat -u UNIX-CONNECT:/var/run/qemu-server/300.serial1 OPEN:/var/log/kdk-serial/300.log,creat,append`
   in a `Restart=always`, `RestartSec=5` systemd service, plus a logrotate rule (weekly, 8 rotations).
5. Verify: `sudo sh -c 'echo kdk-serial-test > /dev/kmsg'` on dkr-01, then
   `tail -1 /var/log/kdk-serial/300.log` on hyp-01 shows it.

Once that works, carry it into `hosts/kdk-hyp-01/` (unit + logrotate) and the dkr-01
cloud-init (`console=ttyS1`) by PR.
