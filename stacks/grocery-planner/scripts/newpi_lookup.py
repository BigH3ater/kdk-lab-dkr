#!/usr/bin/env python3
"""Find New Pi (market.newpi.coop) product ids to put in recipes/foods.yaml.

New Pi runs a Local Food Marketplace (LFM) single-page storefront. Two things
about it shape this tool:

  * Product ids are GLOBAL and share-safe. https://market.newpi.coop/product/<id>
    resolves to the item after a one-time store pick, so that is the URL to paste
    into a rule's `newpi_url`.
  * Names and prices are rendered client-side after a store is selected; there is
    no documented, cookieless name->id search URL. What IS public and stable is
    the per-store sitemap, which enumerates every product id by category:
        https://market.newpi.coop/stores/<store>/sitemaps/store-products-<store>-<category>.xml

So this helper has two modes:

  list   -- download a store+category sitemap and print its product ids/URLs
            (verified to work; no store session needed). Good for pulling, say,
            every Produce id, then eyeballing them in the app.
                python3 scripts/newpi_lookup.py list --category produce
                python3 scripts/newpi_lookup.py list --category meat_poultry --store 5370

  search -- best-effort name search: open a store session, then try the LFM
            product-search endpoint and print id / name / size for matches.
            The exact endpoint is not published, so if the site has changed this
            prints what it got and how to find the id by hand instead.
                python3 scripts/newpi_lookup.py search spinach "chicken breast"

Stores (New Pioneer Co-op locations): 5369, 5370, 5371 (default 5369). robots.txt
allows all paths; this tool uses a normal User-Agent and sleeps between requests.
It never logs in and prints no prices. Add a link to foods.yaml ONLY when you are
sure of the product -- the repo is public and a wrong link is worse than none.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests

BASE = "https://market.newpi.coop"
UA = "kdk-lab-newpi-lookup/1.0 (+https://github.com/BigH3ater/kdk-lab-dkr; grocery planner)"
STORES = ("5369", "5370", "5371")
# LFM category slugs seen in the store sitemap index (not exhaustive).
CATEGORIES = ("produce", "meat_poultry", "meat", "dairy_eggs", "dairy", "cheese",
              "bakery", "frozen", "grocery", "pantry", "herbs", "fresh_food", "deli")
PRODUCT_RE = re.compile(r"/product/([0-9A-Za-z]+)\b")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def _get(s: requests.Session, url: str, **kw):
    r = s.get(url, timeout=20, **kw)
    time.sleep(0.5)  # be polite: low request rate
    return r


def list_category(store: str, category: str) -> list[str]:
    """Product ids in a store+category, from the public sitemap (verified path)."""
    url = f"{BASE}/stores/{store}/sitemaps/store-products-{store}-{category}.xml"
    s = _session()
    r = _get(s, url)
    if r.status_code != 200 or "xml" not in r.headers.get("content-type", ""):
        print(f"  no sitemap for category {category!r} at store {store} "
              f"(HTTP {r.status_code}). Try another --category from: "
              f"{', '.join(CATEGORIES)}", file=sys.stderr)
        return []
    ids: list[str] = []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        print(f"  could not parse sitemap XML: {e}", file=sys.stderr)
        return []
    for loc in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        m = PRODUCT_RE.search(loc.text or "")
        if m:
            ids.append(m.group(1))
    return ids


# LFM product-search endpoints are not documented; these are tried in order and
# the first JSON response wins. Update here if the Network tab shows a new path.
SEARCH_ENDPOINTS = (
    "{base}/stores/{store}/products.json?search={q}",
    "{base}/api/stores/{store}/products?search={q}",
    "{base}/api/v1/stores/{store}/products?q={q}",
    "{base}/stores/{store}/catalog/search?q={q}",
)


def _walk_products(obj):
    """Yield dicts that look like products (have an id + a name-ish field)."""
    if isinstance(obj, dict):
        keys = {k.lower() for k in obj}
        if "id" in keys and keys & {"name", "title", "product_name", "displayname"}:
            yield obj
        for v in obj.values():
            yield from _walk_products(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_products(v)


def search(store: str, terms: list[str], limit: int) -> int:
    s = _session()
    # Open a store session so the storefront serves store-scoped data.
    _get(s, f"{BASE}/stores/{store}")
    found_any = False
    for term in terms:
        print(f"\n== {term} ==")
        hits = []
        for tmpl in SEARCH_ENDPOINTS:
            url = tmpl.format(base=BASE, store=store, q=requests.utils.quote(term))
            try:
                r = _get(s, url)
            except requests.RequestException:
                continue
            if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
                continue
            try:
                data = r.json()
            except ValueError:
                continue
            for p in _walk_products(data):
                pid = str(p.get("id") or p.get("Id") or "")
                name = (p.get("name") or p.get("title") or p.get("product_name")
                        or p.get("displayName") or "").strip()
                size = (p.get("size") or p.get("unit_size") or p.get("package")
                        or p.get("sellBy") or "").strip()
                if pid and name:
                    hits.append((pid, name, size))
            if hits:
                break
        if not hits:
            print("  no result from the search API (the SPA endpoint may have moved).")
            print("  Find the id by hand: open https://market.newpi.coop, pick your")
            print(f"  co-op, search {term!r}, open the product, and copy the")
            print("  /product/<id> part of the URL into the rule's newpi_url.")
            continue
        found_any = True
        for pid, name, size in hits[:limit]:
            extra = f"  [{size}]" if size else ""
            print(f"  {pid}\t{name}{extra}\t{BASE}/product/{pid}")
    return 0 if found_any else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default="5369", choices=STORES,
                        help="New Pi store id (default 5369)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", parents=[common],
                        help="list product ids in a store+category (sitemap)")
    pl.add_argument("--category", required=True,
                    help=f"category slug, e.g. {', '.join(CATEGORIES)}")
    ps = sub.add_parser("search", parents=[common],
                        help="best-effort name search via the storefront API")
    ps.add_argument("terms", nargs="+", help="food names to search for")
    ps.add_argument("--limit", type=int, default=10, help="max hits per term")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        ids = list_category(a.store, a.category)
        for pid in ids:
            print(f"{pid}\t{BASE}/product/{pid}")
        print(f"\n{len(ids)} products in {a.category!r} at store {a.store}.", file=sys.stderr)
        return 0
    return search(a.store, a.terms, a.limit)


if __name__ == "__main__":
    sys.exit(main())
