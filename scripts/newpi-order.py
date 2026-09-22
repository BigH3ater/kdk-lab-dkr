#!/usr/bin/env python3
"""Build the weekly New Pi (Co-op Cart / Freshop) order from Tandoor's shopping list.

Reads the household shopping list from Tandoor (Mack House), splits out Penzeys-
category items (spices -> the Penzeys list, not the grocery cart), and matches the
rest against New Pioneer Food Co-op's Freshop catalog (api.freshop.ncrcloud.com,
app_key=new_pioneer -- the same API the shop.newpi.coop storefront uses).

DRY-RUN by default: prints the match table (item -> product, size, price when the
session is store-scoped) and never touches a cart. Checkout is ALWAYS human: review
the cart at shop.newpi.coop, pick the DoorDash slot, pay.

Usage (from the workstation; SSH + op access assumed):
  scripts/newpi-order.py            # dry run: match table + Penzeys section
  scripts/newpi-order.py --cart     # ALSO add matches to the logged-in Co-op Cart
                                    # (requires op://kdk-cluster/newpi-coop-cart)

Tandoor read path: Django ORM over SSH (docker exec tandoor-web), same lane the
recipe agent uses -- REST tokens are disabled in this install.
"""
import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request

DMZ = "kdkadmin@192.168.191.20"
FRESHOP = "https://api.freshop.ncrcloud.com"
APP_KEY = "new_pioneer"
SPACE_ID = 3  # Mack House

ORM_SNIPPET = r"""
import json
from cookbook.models import ShoppingListEntry
out = []
for e in ShoppingListEntry.objects.filter(space_id=%d, checked=False).select_related("food", "unit"):
    cat = getattr(getattr(e.food, "supermarket_category", None), "name", "") or ""
    out.append({
        "food": e.food.name if e.food else "",
        "amount": float(e.amount) if e.amount else None,
        "unit": e.unit.name if e.unit else "",
        "category": cat,
    })
print(json.dumps(out))
""" % SPACE_ID


def tandoor_shopping_list():
    cmd = (
        "sudo docker exec -i tandoor-web /opt/recipes/venv/bin/python "
        "/opt/recipes/manage.py shell"
    )
    r = subprocess.run(
        ["ssh", DMZ, cmd], input=ORM_SNIPPET, capture_output=True, text=True, timeout=120
    )
    if r.returncode != 0:
        sys.exit(f"tandoor read failed: {r.stderr[-300:]}")
    line = [l for l in r.stdout.splitlines() if l.strip().startswith("[")]
    return json.loads(line[-1]) if line else []


def freshop_search(q, limit=5):
    url = f"{FRESHOP}/1/products?" + urllib.parse.urlencode(
        {"app_key": APP_KEY, "q": q, "limit": limit}
    )
    with urllib.request.urlopen(url, timeout=20) as resp:
        d = json.load(resp)
    return d.get("items", []), d.get("total", 0)


def pick_match(item_name, products):
    """Cheap heuristic: prefer products whose name contains every word of the item."""
    words = [w for w in item_name.lower().split() if len(w) > 2]
    best, best_score = None, -1
    for p in products:
        name = (p.get("name") or "").lower()
        score = sum(1 for w in words if w in name)
        if score > best_score:
            best, best_score = p, score
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cart", action="store_true", help="add matches to the Co-op Cart (P2; needs login)")
    args = ap.parse_args()

    entries = tandoor_shopping_list()
    if not entries:
        print("Shopping list is empty (need = meal plan − on-hand). Nothing to order.")
        return

    penzeys = [e for e in entries if e["category"].lower() == "penzeys"]
    grocery = [e for e in entries if e["category"].lower() != "penzeys"]

    if penzeys:
        print("== PENZEYS list (spices -- order at penzeys.com, NOT New Pi) ==")
        for e in penzeys:
            amt = f'{e["amount"]:g} {e["unit"]} ' if e["amount"] else ""
            print(f"  - {amt}{e['food']}")
        print()

    if grocery:
        print("== NEW PI (Co-op Cart) matches ==")
        unmatched = []
        for e in grocery:
            items, total = freshop_search(e["food"])
            m = pick_match(e["food"], items)
            if m:
                price = m.get("price") or "?"
                print(f"  {e['food']:<32} -> {m.get('name','?')[:48]:<48} {m.get('size','') or '':<10} ${price}  [id {m.get('id')}]")
            else:
                unmatched.append(e["food"])
                print(f"  {e['food']:<32} -> NO MATCH ({total} results)")
        if unmatched:
            print(f"\n  manual picks needed: {', '.join(unmatched)}")

    if args.cart:
        print("\n--cart: authenticated cart-add is P2 -- endpoint discovery pending a real "
              "login session (op://kdk-cluster/newpi-coop-cart). Dry-run output above is "
              "what would be added. See docs/runbooks/grocery-orders.md.")


if __name__ == "__main__":
    main()
