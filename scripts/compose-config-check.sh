#!/usr/bin/env bash
# Validate every stacks/*/compose.yaml with `docker compose config -q` so broken
# YAML / compose schema never reaches main (CI job `compose-config` in
# .github/workflows/compose-baseline.yml; also runnable locally).
#
# Works on a COPY of stacks/ in a temp dir, so a host clone's real (op-injected)
# .env files are never read or overwritten. In the copy:
#   - each .env.tpl becomes a placeholder .env (every KEY=... -> KEY=placeholder,
#     so op:// refs are never resolved);
#   - any other missing env_file inside the stack is created empty (noted);
#   - a missing env_file OUTSIDE the stack (host-managed, e.g.
#     /opt/kdk-lab/home-assistant/secrets.env) is created empty with sudo when
#     run with --ci; locally that stack is skipped and listed with the reason.
#
# Usage: scripts/compose-config-check.sh [--ci] [stack ...]
set -uo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
ci=0
if [ "${1:-}" = "--ci" ]; then ci=1; shift; fi
# Komodo renders per-stack `environment` (komodo/resources.toml) into .env at
# deploy time. Variables that a compose file requires with ${VAR:?...} need a
# placeholder here or `config` fails before it validates anything.
export BIND_IP="${BIND_IP:-127.0.0.1}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp -r "$repo/stacks" "$work/"

if [ $# -gt 0 ]; then stacks=("$@"); else
  stacks=(); for d in "$work"/stacks/*/; do stacks+=("$(basename "$d")"); done
fi

ok=0; failed=(); skipped=(); notes=()
for stack in "${stacks[@]}"; do
  dir="$work/stacks/$stack"
  [ -f "$dir/compose.yaml" ] || { skipped+=("$stack: no compose.yaml"); continue; }
  if [ -f "$dir/.env.tpl" ]; then
    sed -E '/^[[:space:]]*(#|$)/d; s/=.*/=placeholder/' "$dir/.env.tpl" > "$dir/.env"
  fi
  status=fail
  for _ in 1 2 3 4 5 6; do
    out=$(cd "$dir" && docker compose -f compose.yaml config -q 2>&1) && { status=ok; break; }
    missing=$(printf '%s\n' "$out" | sed -nE 's/^env file ([^ ]+) not found.*/\1/p' | head -n1)
    [ -n "$missing" ] || break
    if [[ "$missing" == "$dir"/* ]]; then
      mkdir -p "$(dirname "$missing")" && : > "$missing"
      notes+=("$stack: created empty ${missing#"$dir"/} (env_file without a .env.tpl)")
    elif [ $ci -eq 1 ]; then
      sudo mkdir -p "$(dirname "$missing")" && sudo touch "$missing"
      notes+=("$stack: created empty host env_file $missing")
    else
      status=skip
      skipped+=("$stack: host-managed env_file $missing missing here (the Action creates it with --ci)")
      break
    fi
  done
  case $status in
    ok) ok=$((ok + 1)) ;;
    fail) failed+=("$stack"); printf 'FAIL %s\n%s\n' "$stack" "$out" ;;
  esac
done

for n in "${notes[@]}"; do echo "note: $n"; done
for s in "${skipped[@]}"; do echo "skipped: $s"; done
echo "compose config: ${ok} ok, ${#failed[@]} failed, ${#skipped[@]} skipped"
[ ${#failed[@]} -eq 0 ]
