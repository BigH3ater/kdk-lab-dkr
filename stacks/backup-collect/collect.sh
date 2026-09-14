#!/bin/sh
# Pull /opt/kdk-lab from the three hosts NOT on the storage NFS net into the
# shared backup tree. Best-effort per host: a failed pull leaves that host's
# .last-backup marker stale, which backup-verify turns into a Pushover alert --
# one host failing never blocks the rest or the offsite/verify stages.
# Remote reads use `sudo rsync` (app data is root-owned; kdkadmin has NOPASSWD
# sudo). SSH key is the op-rendered /key (see the stack's pre_deploy).
for spec in kdk-dkr-02:10.1.30.21 kdk-dkr-03:10.1.30.22 kdk-mon-01:10.1.20.30; do
  name="${spec%%:*}"; ip="${spec##*:}"
  echo "== collecting $name ($ip) =="
  if rsync -a --delete --timeout=300 \
       -e "ssh -i /key -o IdentitiesOnly=yes -o IdentityAgent=none -o StrictHostKeyChecking=no -o BatchMode=yes" \
       --rsync-path="sudo rsync" \
       --exclude 'stacks/' \
       "kdkadmin@$ip:/opt/kdk-lab/" "/dest/$name/"; then
    touch "/dest/$name/.last-backup"
    echo "   $name OK"
  else
    echo "   $name FAILED (rc=$?) -- marker left stale for backup-verify"
  fi
done
exit 0
