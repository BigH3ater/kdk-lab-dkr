#!/usr/bin/env python3
"""Keep the host baseline files in the two VM cloud-init files identical to hosts/.

hosts/ is the source of truth for host-level config (applied by hand, see
hosts/README.md). A rebuilt VM gets the same files through cloud-init
`write_files`, inside a marked block that this script owns:

    # >>> kdk host baseline ...
    ...generated write_files entries...
    # <<< kdk host baseline

Usage:
    scripts/check-host-files-sync.py           # exit 1 if a cloud-init copy drifted
    scripts/check-host-files-sync.py --write   # regenerate the marked blocks

Only the marked block is touched; everything else in the cloud-init files is
hand-written.
"""
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COMMON_VM = [
    ("/etc/ssh/sshd_config.d/98-kdk-admin.conf", "hosts/_common/sshd_config.d/98-kdk-admin.conf", None),
    ("/etc/sysctl.d/90-kdk-hardening.conf", "hosts/_common/sysctl.d/90-kdk-hardening.conf", None),
    ("/etc/sysctl.d/91-kdk-nonlocal-bind.conf", "hosts/_common/sysctl.d/91-kdk-nonlocal-bind.conf", None),
    ("/etc/apt/apt.conf.d/20auto-upgrades", "hosts/_common/apt/20auto-upgrades", None),
    ("/etc/apt/apt.conf.d/52kdk-unattended-upgrades", "hosts/_common/apt/52kdk-unattended-upgrades", None),
    ("/etc/systemd/system/kdk-storage-fence.service", "hosts/_common/systemd/kdk-storage-fence.service", None),
    ("/usr/local/sbin/kdk-docker-user.sh", "hosts/_common/sbin/kdk-docker-user.sh", "0755"),
    ("/etc/systemd/system/kdk-docker-user.service", "hosts/_common/systemd/kdk-docker-user.service", None),
    ("/etc/systemd/system/kdk-docker-user.timer", "hosts/_common/systemd/kdk-docker-user.timer", None),
]


def per_host(host):
    return [
        ("/etc/docker/daemon.json", f"hosts/{host}/daemon.json", None),
        ("/etc/nftables.d/kdk-storage.nft", f"hosts/{host}/nftables.d/kdk-storage.nft", None),
        ("/etc/default/kdk-docker-user", f"hosts/{host}/kdk-docker-user.conf", None),
    ]


# kdk-dkr-04's NFS mount units are the one storage-specific host file tracked in
# hosts/ (kdk-dkr-01 keeps its mounts hand-written in cloud-init). Tracking them
# here keeps the hosts/ source of truth and the cloud-init rebuild copy from
# drifting. runcmd in the cloud-init file enables them (systemctl enable --now).
MOUNTS_DKR04 = [
    ("/etc/systemd/system/mnt-media.mount", "hosts/kdk-dkr-04/systemd/mnt-media.mount", None),
    ("/etc/systemd/system/mnt-backup.mount", "hosts/kdk-dkr-04/systemd/mnt-backup.mount", None),
]


TARGETS = {
    "cloud-init/kdk-dkr-01-vendor-data.yaml": per_host("kdk-dkr-01") + COMMON_VM,
    "cloud-init/kdk-dkr-dmz-01-user-data.yaml": per_host("kdk-dkr-dmz-01") + COMMON_VM,
    "cloud-init/kdk-dkr-04-user-data.yaml": per_host("kdk-dkr-04") + MOUNTS_DKR04 + COMMON_VM,
}

BEGIN = "  # >>> kdk host baseline (generated from hosts/ by scripts/check-host-files-sync.py --write; edit hosts/, not this block)"
END = "  # <<< kdk host baseline"


def render(entries):
    out = [BEGIN]
    for dest, src, mode in entries:
        with open(os.path.join(ROOT, src), encoding="utf-8") as fh:
            body = fh.read()
        if not body.endswith("\n"):
            body += "\n"
        out.append(f"  - path: {dest}")
        if mode:
            out.append(f"    permissions: '{mode}'")
        out.append("    content: |")
        for line in body.splitlines():
            out.append(f"      {line}" if line else "")
    out.append(END)
    return "\n".join(out) + "\n"


def main():
    write = "--write" in sys.argv[1:]
    bad = 0
    for ci, entries in TARGETS.items():
        path = os.path.join(ROOT, ci)
        text = open(path, encoding="utf-8").read()
        block = render(entries)
        pat = re.compile(re.escape(BEGIN) + r"\n.*?" + re.escape(END) + r"\n", re.S)
        if not pat.search(text):
            print(f"{ci}: marked block missing (add the two marker lines under write_files:)")
            bad += 1
            continue
        new = pat.sub(lambda _m: block, text)
        # The generated YAML must parse and carry exactly the repo bytes.
        doc = yaml.safe_load(new)
        files = {f["path"]: f for f in doc.get("write_files", [])}
        for dest, src, mode in entries:
            want = open(os.path.join(ROOT, src), encoding="utf-8").read()
            got = files.get(dest, {}).get("content")
            if got != want:
                print(f"{ci}: {dest} does not round-trip from {src}")
                bad += 1
            if mode and str(files.get(dest, {}).get("permissions")) != mode:
                print(f"{ci}: {dest} permissions != {mode}")
                bad += 1
        if new != text:
            if write:
                open(path, "w", encoding="utf-8").write(new)
                print(f"{ci}: regenerated")
            else:
                print(f"{ci}: out of sync with hosts/ (run scripts/check-host-files-sync.py --write)")
                bad += 1
        else:
            print(f"{ci}: in sync ({len(entries)} files)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
