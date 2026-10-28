#!/usr/bin/env python3
"""Fail when an image used in stacks/ has no advisory-watch catalog entry.

Every image repository referenced by a `stacks/*/compose.yaml` image: line or a
local Dockerfile FROM must have an entry in stacks/advisory-watch/catalog.json
with at least one source (github, osv, nvd_cpe, feed) or `local: true` plus a
`base` that is itself in the catalog. Image parsing is shared with the job
(stacks/advisory-watch/advisory_watch.py) so CI and the weekly run agree.

Usage: python3 scripts/advisory-catalog-check.py [--repo DIR]
Exit 0 = covered, 1 = gaps (listed). Unused entries and `confirm: true`
entries are reported as warnings only. Stdlib only.
"""
from __future__ import annotations

import argparse, importlib.util, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_job(repo: str):
    path = os.path.join(repo, "stacks", "advisory-watch", "advisory_watch.py")
    spec = importlib.util.spec_from_file_location("advisory_watch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=ROOT)
    a = ap.parse_args()
    job = load_job(a.repo)
    catalog = json.load(open(os.path.join(a.repo, "stacks", "advisory-watch", "catalog.json")))["images"]
    used: dict[str, set[str]] = {}
    for (repo, _tag), stacks in job.tree_images(a.repo).items():
        used.setdefault(repo, set()).update(stacks)

    errors = []
    for repo in sorted(used):
        ent = catalog.get(repo)
        where = ",".join(sorted(used[repo]))
        if ent is None:
            errors.append(f"{repo} (stacks: {where}): no catalog entry")
            continue
        sources = [k for k in ("github", "osv", "nvd_cpe", "feed") if ent.get(k)]
        if ent.get("local"):
            if ent.get("base") not in catalog:
                errors.append(f"{repo}: local image needs `base` = a catalog key (got {ent.get('base')!r})")
        elif not sources:
            errors.append(f"{repo}: entry has no source (github/osv/nvd_cpe/feed) and is not local")
        if ent.get("github") and not re.fullmatch(r"[\w.-]+/[\w.-]+", ent["github"]):
            errors.append(f"{repo}: github must be owner/repo (got {ent['github']!r})")
        osv = ent.get("osv")
        if osv and not (isinstance(osv, dict) and osv.get("ecosystem") and osv.get("package")):
            errors.append(f"{repo}: osv must be {{ecosystem, package}}")

    unused = sorted(set(catalog) - set(used))
    confirm = sorted(k for k, v in catalog.items() if v.get("confirm"))
    print(f"images in stacks/: {len(used)}  catalog entries: {len(catalog)}  "
          f"to confirm: {len(confirm)}  unused: {len(unused)}")
    for k in unused:
        print(f"warning: catalog entry not used by any stack: {k}")
    for k in confirm:
        print(f"note: mapping marked confirm: {k}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        print("Add the missing entries to stacks/advisory-watch/catalog.json (see its README).")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
