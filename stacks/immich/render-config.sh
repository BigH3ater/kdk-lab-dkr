#!/bin/sh
# Render immich-config.json.tpl -> config/immich-config.json for the Komodo
# pre_deploy of stack `immich` (runs as root on kdk-dkr-dmz-01, cwd = stacks/immich,
# after `. /etc/komodo/op.env` and the .env inject). Also usable in CI with --check.
#
# Guards against a bad config stopping immich-server (Immich refuses to start on an
# unparseable or invalid IMMICH_CONFIG_FILE):
#   - renders into a temp file and validates it (JSON parses, OIDC invariants hold)
#     BEFORE it replaces the live file; any failure exits non-zero, Komodo aborts the
#     deploy, and the running container and the previous file stay untouched;
#   - the swap is an atomic mv inside the same directory.
# The rendered file holds the OIDC client secret: dir 0700 + file 0400, both owned by
# 1000:1000 (immich-server's uid), and gitignored (stacks/immich/config/).
# Finally appends IMMICH_CONFIG_SHA to .env for the server's kdk.config-sha label, so
# a config change (or a rotated secret) recreates immich-server on deploy.
#
# Usage: sh render-config.sh          # deploy (needs op + /etc/komodo/op.env sourced)
#        sh render-config.sh --check  # CI/local: dummy secret, validate only, no writes
set -eu

tpl=immich-config.json.tpl
dir=config
out=$dir/immich-config.json

validate() {
  python3 - "$1" <<'EOF'
import json, sys
c = json.load(open(sys.argv[1]))
o = c["oauth"]
checks = {
    "oauth.enabled": o["enabled"] is True,
    "oauth.clientSecret set": isinstance(o["clientSecret"], str) and len(o["clientSecret"]) >= 20
        and "op://" not in o["clientSecret"],
    # Authelia client `immich` uses client_secret_post (stacks/identity/authelia/configuration.yml).
    "oauth.tokenEndpointAuthMethod": o["tokenEndpointAuthMethod"] == "client_secret_post",
    # No OIDC claim may grant admin: Authelia never issues this claim name.
    "oauth.roleClaim not immich_role": o["roleClaim"] != "immich_role",
    "oauth.autoLaunch off": o["autoLaunch"] is False,
    "passwordLogin.enabled (break-glass)": c["passwordLogin"]["enabled"] is True,
    "storageTemplate.enabled": c["storageTemplate"]["enabled"] is True,
    "map off": c["map"]["enabled"] is False,
    "newVersionCheck off": c["newVersionCheck"]["enabled"] is False,
}
bad = [k for k, ok in checks.items() if not ok]
if bad:
    sys.exit("immich config invalid: " + ", ".join(bad))
EOF
}

if [ "${1:-}" = "--check" ]; then
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' EXIT
  sed 's|{{ op://[^}]* }}|dummy-secret-0123456789abcdef0123456789|' "$tpl" > "$tmp"
  validate "$tmp"
  echo "immich-config.json.tpl OK"
  exit 0
fi

install -d -m 0700 -o 1000 -g 1000 "$dir"
new=$dir/.immich-config.json.new
trap 'rm -f "$new"' EXIT
op inject -f -i "$tpl" -o "$new"
validate "$new"
chown 1000:1000 "$new"
chmod 0400 "$new"
mv -f "$new" "$out"
echo "IMMICH_CONFIG_SHA=$(sha256sum "$out" | cut -c1-12)" >> .env
