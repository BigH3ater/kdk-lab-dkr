#!/bin/sh
# reMarkable Paper Pro -- make Tailscale survive a reboot.
#
# Problem: tailscaled was started by hand (nohup) so it did NOT come back after a
# reboot/deep-sleep, silently breaking all remote sync. This installs a systemd
# unit so tailscaled is managed and restarts automatically.
#
# Persistence model:
#   * Binaries + node state live under /home/root -> survive FIRMWARE UPDATES.
#   * The systemd unit lives in /etc/systemd/system -> survives REBOOTS but is
#     WIPED by a firmware update (rootfs A/B swap). So RE-RUN THIS SCRIPT after
#     every reMarkable firmware update. It is idempotent.
#
# The node's prefs (accept-routes, ssh, tag:remarkable, hostname) are already
# persisted in tailscaled.state, so tailscaled reconnects on start -- no
# `tailscale up` / re-auth needed.
#
# Run on the tablet as root:  sh /home/root/install-tailscale-persistence.sh
set -e

TS_DIR="$(ls -d /home/root/tailscale_*_arm64 2>/dev/null | sort -V | tail -1)"
[ -n "$TS_DIR" ] && [ -x "$TS_DIR/tailscaled" ] || { echo "ERROR: tailscaled not found under /home/root/tailscale_*_arm64"; exit 1; }
STATE=/home/root/tailscaled.state
[ -f "$STATE" ] || { echo "ERROR: no node state at $STATE (register the node first)"; exit 1; }

echo "Using $TS_DIR"

cat > /etc/systemd/system/tailscaled.service <<EOF
[Unit]
Description=Tailscale node agent (reMarkable, userspace)
Documentation=https://tailscale.com/
After=network.target
Wants=network.target

[Service]
ExecStart=$TS_DIR/tailscaled --tun=userspace-networking --state=$STATE --socket=/run/tailscale/tailscaled.sock
ExecStopPost=$TS_DIR/tailscaled --cleanup
Restart=always
RestartSec=5
RuntimeDirectory=tailscale
RuntimeDirectoryMode=0755
Type=notify

[Install]
WantedBy=multi-user.target
EOF

# Stop any hand-started tailscaled so it doesn't fight the unit for the socket.
pkill -f 'tailscaled --tun=userspace-networking' 2>/dev/null || true
sleep 1

systemctl daemon-reload
systemctl enable --now tailscaled.service
sleep 3

echo "--- unit status ---"
systemctl --no-pager -l status tailscaled.service | head -n 6 || true
echo "--- tailscale status ---"
"$TS_DIR/tailscale" --socket=/run/tailscale/tailscaled.sock status 2>&1 | head -n 5 || true
echo "Done. tailscaled is now managed by systemd and will start on boot."
