#!/usr/bin/env bash
# Weekly check: is any host running an older kernel than the newest one installed?
#
# kdk-dkr-01 ran 6.12.113 for five days with 6.12.115 installed (unattended-upgrades
# installs kernels, the VMs never reboot on their own), and a crash chose the
# reboot time (docs/runbooks/host-kernel-crash.md). This makes a pending kernel
# visible in the weekly maintenance run so the owner schedules the reboot.
#
# Read-only: one ssh per host, no sudo, nothing written. Per host it prints the
# running kernel, the newest installed kernel of the same flavour (amd64,
# rpi-2712, pve, ...), the day that kernel was installed (dpkg.log, else the
# package's dpkg list file), days since then, uptime, the panic baseline
# (kernel.panic_on_oops/kernel.panic from hosts/_common/sysctl.d/92-kdk-panic.conf,
# checked on VMs only) and a status:
#   OK        running == newest installed (and a VM has the panic baseline)
#   PENDING   a newer kernel is installed: reboot in a quiet window
#   BASELINE  kernel current, but a VM lacks panic_on_oops=1 / panic=10
#   SSH-FAIL  host unreachable, nothing checked
#
# Usage: scripts/kernel-pending-check.sh [name=ip ...]     (default: the hosts below)
#        KDK_SSH_OPTS="-o IdentityAgent=/path/agent.sock" scripts/kernel-pending-check.sh
# Exit: 0 all OK, 1 any PENDING or BASELINE, 2 any SSH-FAIL.
set -u

DEFAULT_HOSTS=(
  kdk-dkr-01=10.1.20.20
  kdk-dkr-04=10.1.20.22
  kdk-dkr-dmz-01=192.168.191.20
  kdk-log-01=10.1.20.21
  kdk-mon-01=10.1.20.30
  kdk-dkr-02=10.1.30.21
  kdk-dkr-03=10.1.30.22
)
if [ $# -gt 0 ]; then HOSTS=("$@"); else HOSTS=("${DEFAULT_HOSTS[@]}"); fi

# Runs on the host through `bash -s`. Prints one line:
#   running|newest|install_epoch|now_epoch|uptime_s|virt|panic_on_oops|panic
# The flavour is the release minus its version part:
#   6.12.115+deb13.1-amd64 -> amd64, 6.18.60+rpt-rpi-2712 -> rpi-2712, 6.8.12-4-pve -> pve
# shellcheck disable=SC2016
PROBE='
set -u
flavor() { sed -E "s/^[0-9][0-9.]*(\+[A-Za-z0-9.~]+)?(-[0-9]+)?-//" <<<"$1"; }
run=$(uname -r); f=$(flavor "$run")
newest=$(for k in /boot/vmlinuz-*; do r=${k#/boot/vmlinuz-}; [ "$(flavor "$r")" = "$f" ] && echo "$r"; done | sort -V | tail -1)
[ -n "$newest" ] || newest=$run
pkg=$(dpkg-query -S "/boot/vmlinuz-$newest" 2>/dev/null | head -1 | cut -d: -f1)
ts=""
if [ -n "$pkg" ]; then
  ts=$(zgrep -hF " status installed $pkg:" /var/log/dpkg.log* 2>/dev/null | sort | tail -1 | cut -c1-19)
fi
if [ -n "$ts" ]; then ep=$(date -d "$ts" +%s)
elif [ -n "$pkg" ] && [ -e "/var/lib/dpkg/info/$pkg.list" ]; then ep=$(stat -c %Y "/var/lib/dpkg/info/$pkg.list")
else ep=$(stat -c %Y "/boot/vmlinuz-$newest" 2>/dev/null || echo 0); fi
virt=$(systemd-detect-virt 2>/dev/null) || virt=none
echo "$run|$newest|$ep|$(date +%s)|$(cut -d. -f1 /proc/uptime)|$virt|$(cat /proc/sys/kernel/panic_on_oops)|$(cat /proc/sys/kernel/panic)"
'

# KDK_SSH_OPTS is split on spaces on purpose (several -o options).
# shellcheck disable=SC2206
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 ${KDK_SSH_OPTS:-})

FMT='%-15s %-29s %-29s %-10s %4s %6s %-8s %s\n'
rc=0
# shellcheck disable=SC2059
printf "$FMT" HOST RUNNING NEWEST INSTALLED DAYS UPTIME OOPS/PNC STATUS
for hp in "${HOSTS[@]}"; do
  name=${hp%%=*}; ip=${hp#*=}
  if ! out=$(ssh "${SSH_OPTS[@]}" "kdkadmin@$ip" 'bash -s' <<<"$PROBE" 2>/dev/null) || [ -z "$out" ]; then
    # shellcheck disable=SC2059
    printf "$FMT" "$name" - - - - - - SSH-FAIL
    rc=2
    continue
  fi
  IFS='|' read -r run newest ep now up virt oops pnc <<<"$out"
  status=OK
  if [ "$run" != "$newest" ]; then
    status=PENDING
  elif [ "$virt" != none ] && { [ "$oops" != 1 ] || [ "$pnc" != 10 ]; }; then
    status=BASELINE
  fi
  if [ "$status" != OK ] && [ "$rc" -eq 0 ]; then rc=1; fi
  # shellcheck disable=SC2059
  printf "$FMT" "$name" "$run" "$newest" "$(date -d "@$ep" +%F)" "$(( (now - ep) / 86400 ))" \
    "$(( up / 86400 ))d" "$oops/$pnc" "$status"
done
exit "$rc"
