#!/usr/bin/env bash
# Install the Kodiak color templates onto the reMarkable Paper Pro (LAB-SIDE).
#
# The Paper Pro stores templates as JSON *vector* files in
# /usr/share/remarkable/templates/ with a manifest templates.json there. That
# dir is on the RO rootfs (remountable rw, no dm-verity): changes SURVIVE A
# REBOOT but are WIPED BY A FIRMWARE UPDATE -> re-run this after any FW update.
#
# This runs from a computer (the device has no python/jq): it pulls templates.json,
# merges our entries here, then pushes the .template files + merged manifest and
# restarts xochitl. Requires SSH to the tablet on home Wi-Fi.
#
# Usage: RM_HOST=10.1.30.245 ./install-templates.sh
#   Auth: set up SSH however you like (key, or sshpass/SSH_ASKPASS with the
#   root password from op://kdk-ops/remarkable-device). SSH/SCP is invoked as
#   plain `ssh`/`scp` so your agent/askpass config applies.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TPLDIR="$HERE/templates"
RM_HOST="${RM_HOST:-10.1.30.245}"
RM_USER="${RM_USER:-root}"
DEST="/usr/share/remarkable/templates"
SSH=(ssh -o StrictHostKeyChecking=accept-new "$RM_USER@$RM_HOST")
SCP=(scp -o StrictHostKeyChecking=accept-new)

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

echo "[1/5] pull current manifest"
"${SCP[@]}" "$RM_USER@$RM_HOST:$DEST/templates.json" "$tmp/templates.json"

echo "[2/5] merge Kodiak entries (idempotent, keyed on name+landscape)"
python3 - "$tmp/templates.json" "$TPLDIR/manifest-entries.json" "$tmp/merged.json" <<'PY'
import json,sys
cur=json.load(open(sys.argv[1])); add=json.load(open(sys.argv[2]))
ts=cur["templates"]
key=lambda t:(t["name"], bool(t.get("landscape",False)))
have={key(t) for t in ts}
for e in add:
    if key(e) not in have: ts.append(e); have.add(key(e))
json.dump(cur, open(sys.argv[3],"w"), ensure_ascii=False, indent=0)
print("  manifest now has", len(ts), "templates")
PY

echo "[3/5] remount / rw and copy files"
"${SSH[@]}" "mount -o remount,rw /"
for f in "$TPLDIR"/*.template; do "${SCP[@]}" "$f" "$RM_USER@$RM_HOST:$DEST/"; done
"${SCP[@]}" "$tmp/merged.json" "$RM_USER@$RM_HOST:$DEST/templates.json"

echo "[4/5] restart xochitl to register templates"
"${SSH[@]}" "systemctl restart xochitl"

echo "[5/5] remount / ro"
"${SSH[@]}" "mount -o remount,ro / 2>/dev/null || true"
echo "Done. Kodiak templates installed. (Re-run after a firmware update.)"
