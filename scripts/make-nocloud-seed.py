#!/usr/bin/env python3
"""Build a NoCloud "cidata" seed ISO for a cloud-init VM build.

A NoCloud seed is an ISO9660 image, volume label `cidata`, holding `meta-data`,
`user-data` and (optionally) `network-config`. cloud-init's NoCloud datasource
reads them by their lowercase names, so the image needs Rock Ridge (lowercase
POSIX names) and Joliet, exactly like:

    genisoimage -output seed.iso -volid cidata -joliet -rock \
        user-data meta-data network-config

This script does the same with pycdlib (pure Python), so a build host without
genisoimage/xorriso/mkisofs can still produce the seed. If one of those C tools
is present it is used instead (byte-for-byte conventional output).

Usage:
    make-nocloud-seed.py OUT.iso user-data=FILE meta-data=FILE [network-config=FILE]
"""
import os
import shutil
import subprocess
import sys
import tempfile


def parse_args(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    out = argv[1]
    files = {}
    for a in argv[2:]:
        if "=" not in a:
            sys.exit(f"bad arg {a!r} (want name=path)")
        name, path = a.split("=", 1)
        if name not in ("user-data", "meta-data", "network-config"):
            sys.exit(f"unknown seed file name {name!r}")
        if not os.path.isfile(path):
            sys.exit(f"missing {path}")
        files[name] = path
    if "user-data" not in files or "meta-data" not in files:
        sys.exit("user-data and meta-data are required")
    return out, files


def via_c_tool(out, files):
    for tool in ("genisoimage", "mkisofs", "xorriso"):
        exe = shutil.which(tool)
        if not exe:
            continue
        with tempfile.TemporaryDirectory() as td:
            for name, path in files.items():
                shutil.copyfile(path, os.path.join(td, name))
            names = list(files)
            if tool == "xorriso":
                cmd = [exe, "-as", "mkisofs", "-output", out, "-volid",
                       "cidata", "-joliet", "-rock", *names]
            else:
                cmd = [exe, "-output", out, "-volid", "cidata", "-joliet",
                       "-rock", *names]
            subprocess.run(cmd, cwd=td, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    return False


def via_pycdlib(out, files):
    try:
        import pycdlib
    except ImportError:
        sys.exit("no genisoimage/mkisofs/xorriso and pycdlib not installed "
                 "(pip install pycdlib)")
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09", vol_ident="cidata")
    iso_names = {
        "user-data": "/USERDATA.;1",
        "meta-data": "/METADATA.;1",
        "network-config": "/NETWORKC.;1",
    }
    for name, path in files.items():
        with open(path, "rb") as fh:
            data = fh.read()
        iso.add_fp(__import__("io").BytesIO(data), len(data),
                   iso_names[name], rr_name=name, joliet_path="/" + name)
    iso.write(out)
    iso.close()


def main():
    out, files = parse_args(sys.argv)
    if not via_c_tool(out, files):
        via_pycdlib(out, files)
    size = os.path.getsize(out)
    print(f"wrote {out} ({size} bytes): {', '.join(sorted(files))}")


if __name__ == "__main__":
    main()
