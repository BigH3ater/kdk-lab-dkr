#!/bin/sh
# Scan every image used by a running container on this host, once per image ID.
# Image list and layers come from the local Docker daemon through the GET-only
# socket proxy (DOCKER_HOST). One JSON report per image lands in /out as
# <n>.json with <n>.meta ("<image id> <name>"), a failed scan leaves <n>.err.
# /out/done marks a finished pass; report.py reads it after this container exits.
set -u
OUT=/out
API=${DOCKER_HOST#tcp://}
mkdir -p "$TMPDIR"
rm -rf "${OUT:?}"/* 2>/dev/null

api() { wget -qO- "http://$API$1"; }

i=0
until api /_ping >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 30 ]; then echo "level=error msg=\"socket proxy unreachable\""; exit 1; fi
  sleep 2
done

ids=$(api /containers/json | grep -o '"ImageID":"sha256:[0-9a-f]*"' | cut -d'"' -f4 | sort -u)
n=0
for id in $ids; do
  n=$((n + 1))
  meta=$(api "/images/$id/json")
  # Prefer the tag (traefik:v3.7); a dangling image (tag moved by a newer pull)
  # only has a digest reference; failing both, scan by ID.
  name=$(echo "$meta" | grep -o '"RepoTags":\["[^"]*"' | cut -d'"' -f4)
  [ -n "$name" ] || name=$(echo "$meta" | grep -o '"RepoDigests":\["[^"]*"' | cut -d'"' -f4)
  [ -n "$name" ] || name=$id
  echo "$id $name" > "$OUT/$n.meta"
  echo "level=info msg=scanning n=$n image=$name"
  if ! trivy image --image-src docker --scanners vuln --severity HIGH,CRITICAL \
      --format json --quiet --timeout 20m --skip-version-check --disable-telemetry \
      --output "$OUT/$n.json" "$name" 2> "$OUT/$n.log"; then
    echo "level=error msg=\"scan failed\" image=$name"
    tail -n 3 "$OUT/$n.log"
    : > "$OUT/$n.err"
  fi
done
echo "$n" > "$OUT/done"
echo "level=info msg=\"scan finished\" images=$n"
exit 0
