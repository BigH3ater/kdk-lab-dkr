#!/usr/bin/env bash
# Copy a running k3s workload's config volume to a compose host directory.
# Usage: migrate-config.sh <namespace> <deploy> <container-path> <host> <dest-dir>
# Scales the deployment to 0 first is the CALLER's job if quiesce is needed;
# this streams via kubectl exec tar -> ssh tar.
set -euo pipefail
NS=$1; DEPLOY=$2; SRC=$3; HOST=$4; DEST=$5
POD=$(kubectl get pod -n "$NS" -l app="$DEPLOY" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null \
  || kubectl get pod -n "$NS" -o jsonpath='{.items[?(@.metadata.labels.app\.kubernetes\.io/name=="'"$DEPLOY"'")].metadata.name}' | awk '{print $1}')
[ -n "$POD" ] || { echo "no pod found for $DEPLOY in $NS"; exit 1; }
ssh "kdkadmin@$HOST" "sudo mkdir -p '$DEST'"
kubectl exec -n "$NS" "$POD" -- tar cf - -C "$SRC" . \
  | ssh "kdkadmin@$HOST" "sudo tar xf - -C '$DEST'"
ssh "kdkadmin@$HOST" "sudo chown -R 1000:1000 '$DEST' && sudo du -sh '$DEST'"
