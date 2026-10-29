#!/bin/bash
# kdk-docker-user.sh -- rebuild Docker's DOCKER-USER chain from this host's policy.
#
# Installed as /usr/local/sbin/kdk-docker-user.sh (root:root 0755). Policy file:
# /etc/default/kdk-docker-user (root:root 0644), copied from
# hosts/<host>/kdk-docker-user.conf. Run by kdk-docker-user.service (after
# docker.service) and re-asserted by kdk-docker-user.timer every 5 minutes.
# Source of truth: hosts/_common/sbin/ in BigH3ater/kdk-lab-dkr; runbook:
# docs/runbooks/host-firewall.md.
#
# Scope: ONLY the DOCKER-USER chain of the iptables-nft `filter` table -- the
# chain Docker reserves for operator rules and jumps to first from FORWARD.
# Docker's own chains, the nft table `inet kdk-storage`, and tailscaled's
# iptables-legacy rules are never touched. The chain is replaced in one
# iptables-restore transaction, so there is no moment with half a policy.
#
# Chain layout (top to bottom):
#   1. TAILSCALE=1 only: -i/-o tailscale0 ACCEPT (the two rules the retired
#      docker-forward sidecar asserted; the subnet router needs them).
#   2. RELATED,ESTABLISHED -> RETURN (replies, incl. published-port replies).
#   3. guard_host_port: containers may not reach this host's own published
#      port (matched on the pre-DNAT destination).
#   4. bridge -> bridge -> RETURN. Covers DNATed hairpin to a published port
#      on this host. RETURN, not ACCEPT: Docker's own isolation chains still
#      decide, so two compose networks stay as separated as before.
#   5. DNS to the host's upstream resolvers (Docker's embedded DNS forwards
#      from inside each container's namespace, so these queries are FORWARD).
#   6. allow_dst / allow_ctr exceptions -> RETURN.
#   7. Anything from a bridge to RFC1918 -> LOG (rate-limited) + DROP
#      (MODE=enforce) or LOG only (MODE=log, nothing is dropped).
#   8. RETURN.
# Traffic that never leaves its bridge (seerr -> jellyfin on proxy-dmz) matches
# rule 4 even with br_netfilter, and internet egress matches nothing in 7.
#
# Usage: kdk-docker-user.sh [--dry-run]   (--dry-run prints the restore payload)
set -euo pipefail

CONF=${KDK_DOCKER_USER_CONF:-/etc/default/kdk-docker-user}
IPT=${IPT:-iptables-nft}
IPT_RESTORE=${IPT_RESTORE:-iptables-nft-restore}
STATE=${KDK_DOCKER_USER_STATE:-/run/kdk-docker-user.sha256}
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

log() { echo "kdk-docker-user: $*"; }
die() { echo "kdk-docker-user: ERROR: $*" >&2; exit 1; }

# ---- defaults, overridden by $CONF ------------------------------------------
MODE=enforce                                   # enforce | log
TAILSCALE=0                                    # 1 = subnet-router host
BRIDGES="br+ docker0"                          # iptables interface patterns
PRIVATE_NETS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"
LOG_LIMIT="6/minute"

GUARDS=()     # "proto port host-ip"
ALLOW_DST=()  # "dst proto ports"
ALLOW_CTR=()  # "container dst proto ports"

valid_proto() { case "$1" in tcp|udp) return 0 ;; *) die "bad protocol '$1'" ;; esac; }
valid_ports() { [[ "$1" =~ ^[0-9]+(,[0-9]+)*$ ]] || die "bad port list '$1'"; }
valid_addr()  { [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}(/[0-9]{1,2})?$ ]] || die "bad IPv4 address '$1'"; }
valid_name()  { [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || die "bad container name '$1'"; }

# Policy verbs used by the conf file.
# guard_host_port <tcp|udp> <port> <host-ip>: containers must not reach
#   <host-ip>:<port> (a port this host publishes, e.g. cAdvisor 9280).
guard_host_port() { valid_proto "$1"; valid_ports "$2"; valid_addr "$3"; GUARDS+=("$1 $2 $3"); }
# allow_dst <ip[/len]> <tcp|udp> <port[,port...]>: any container may open it.
allow_dst() { valid_addr "$1"; valid_proto "$2"; valid_ports "$3"; ALLOW_DST+=("$1 $2 $3"); }
# allow_ctr <container> <ip[/len]> <tcp|udp> <port[,port...]>: only that
#   container (all of its current addresses) may open it.
allow_ctr() { valid_name "$1"; valid_addr "$2"; valid_proto "$3"; valid_ports "$4"; ALLOW_CTR+=("$1 $2 $3 $4"); }

[ -r "$CONF" ] || die "policy file $CONF missing"
# shellcheck source=/dev/null
. "$CONF"
case "$MODE" in enforce|log) ;; *) die "MODE must be enforce or log, not '$MODE'" ;; esac

# ---- helpers -----------------------------------------------------------------
port_match() {  # <proto> <ports> -> iptables match
  if [[ "$2" == *,* ]]; then echo "-p $1 -m multiport --dports $2"; else echo "-p $1 --dport $2"; fi
}

ctr_ips() {     # all IPv4 addresses of a running container, space separated
  docker inspect -f '{{range .NetworkSettings.Networks}}{{if .IPAddress}}{{.IPAddress}} {{end}}{{end}}' "$1" 2>/dev/null || true
}

dns_servers() { # the host's upstream resolvers (what Docker's embedded DNS uses)
  local f
  for f in /run/systemd/resolve/resolv.conf /etc/resolv.conf; do
    [ -r "$f" ] || continue
    local out
    out=$(awk '/^nameserver[ \t]/ && $2 ~ /^[0-9.]+$/ && $2 !~ /^127\./ {print $2}' "$f")
    if [ -n "$out" ]; then echo "$out"; return 0; fi
  done
}

RULES=()
add() { RULES+=("-A DOCKER-USER $*"); }
deny() {        # <match...>: LOG, then DROP in enforce mode
  local prefix="kdk-du-drop: "
  [ "$MODE" = log ] && prefix="kdk-du-would-drop: "
  add "$* -m limit --limit $LOG_LIMIT --limit-burst 10 -j LOG --log-prefix \"$prefix\" --log-level 4"
  if [ "$MODE" = enforce ]; then add "$* -j DROP"; fi
}

# ---- wait for Docker's jump (proves we are in the backend Docker uses) ------
for _ in $(seq 1 30); do
  if $IPT -w -S FORWARD 2>/dev/null | grep -q -- '-j DOCKER-USER'; then break; fi
  sleep 1
done
$IPT -w -S FORWARD 2>/dev/null | grep -q -- '-j DOCKER-USER' \
  || die "no '-j DOCKER-USER' in $IPT FORWARD: Docker not running, or not on the iptables-nft backend; refusing to write a chain nothing jumps to"

# ---- build ---------------------------------------------------------------------
if [ "$TAILSCALE" = 1 ]; then
  add "-i tailscale0 -j ACCEPT"
  add "-o tailscale0 -j ACCEPT"
fi
add "-m conntrack --ctstate RELATED,ESTABLISHED -j RETURN"

for br in $BRIDGES; do
  for g in "${GUARDS[@]+"${GUARDS[@]}"}"; do
    read -r proto port hip <<<"$g"
    deny "-i $br -p $proto -m conntrack --ctstate NEW --ctorigdst $hip --ctorigdstport $port"
  done
done

for in_br in $BRIDGES; do
  for out_br in $BRIDGES; do
    add "-i $in_br -o $out_br -j RETURN"
  done
done

for ns in $(dns_servers); do
  for br in $BRIDGES; do
    add "-i $br -d $ns -p udp --dport 53 -j RETURN"
    add "-i $br -d $ns -p tcp --dport 53 -j RETURN"
  done
done

for a in "${ALLOW_DST[@]+"${ALLOW_DST[@]}"}"; do
  read -r dst proto ports <<<"$a"
  for br in $BRIDGES; do
    add "-i $br -d $dst $(port_match "$proto" "$ports") -j RETURN"
  done
done

for a in "${ALLOW_CTR[@]+"${ALLOW_CTR[@]}"}"; do
  read -r name dst proto ports <<<"$a"
  ips=$(ctr_ips "$name")
  if [ -z "$ips" ]; then
    log "container '$name' not running: its exception to $dst $proto/$ports is left out (fail closed)"
    continue
  fi
  for ip in $ips; do
    add "-s $ip/32 -d $dst $(port_match "$proto" "$ports") -j RETURN"
  done
done

for br in $BRIDGES; do
  for net in $PRIVATE_NETS; do
    deny "-i $br -d $net"
  done
done
add "-j RETURN"

PAYLOAD=$(
  echo "*filter"
  echo ":DOCKER-USER - [0:0]"
  echo "-F DOCKER-USER"
  printf '%s\n' "${RULES[@]}"
  echo "COMMIT"
)

if [ "$DRY_RUN" = 1 ]; then
  printf '%s\n' "$PAYLOAD"
  exit 0
fi

# ---- apply atomically, then check ---------------------------------------------
printf '%s\n' "$PAYLOAD" | $IPT_RESTORE -w --noflush \
  || die "$IPT_RESTORE rejected the policy; DOCKER-USER left as it was"

have=$($IPT -w -S DOCKER-USER | grep -c '^-A DOCKER-USER ' || true)
[ "$have" = "${#RULES[@]}" ] \
  || die "DOCKER-USER has $have rules after apply, expected ${#RULES[@]}"

sum=$(printf '%s\n' "$PAYLOAD" | sha256sum | cut -d' ' -f1)
if [ "$(cat "$STATE" 2>/dev/null || true)" != "$sum" ]; then
  echo "$sum" > "$STATE"
  log "policy applied: MODE=$MODE, ${#RULES[@]} rules, sha256 ${sum:0:12}"
fi
