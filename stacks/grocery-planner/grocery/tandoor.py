"""Thin Tandoor 2.7 API client. Reuses the dashboard mealplan sidecar's
django-allauth headless login (session + CSRF; DRF token auth is disabled on
this instance).

Verification (2026-10-31): the READ routes/fields were exercised live against
the instance (meal-plan, shopping-list-entry, food, keyword, recipe,
supermarket-category, recipe-book) and the recipe-from-source route probed. The
WRITE shapes (recipe-from-source, recipe create, meal-plan create with
addshopping, shopping-list-entry, food/recipe PATCH, recipe-book-entry) were
checked against the live `GET /openapi/` request schemas but NOT executed here
(no live writes in this build); the owner's first `seed --apply` and first
weekly run exercise them.

Network code lives here so the pure modules (scoring/generator/shoplist) stay
import-clean and unit-testable offline.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("grocery.tandoor")


def resolve_active_space(spaces, user_spaces):
    """(active_space_id, active_space_name) from /api/space/ + /api/user-space/.
    Pure, so the startup check is unit tested offline."""
    by_id = {s["id"]: s.get("name") for s in (spaces or [])}
    aid = None
    for r in (user_spaces or []):
        if r.get("active"):
            sp = r.get("space")
            aid = sp.get("id") if isinstance(sp, dict) else sp
            break
    return aid, by_id.get(aid)


def resolve_active_household(user_spaces):
    """(household_id, household_name) from the active /api/user-space/ row's
    `household` object. Tandoor 2.x shares meal plans + shopping within a
    household. Pure, so the startup check is unit tested offline."""
    for r in (user_spaces or []):
        if r.get("active"):
            hh = r.get("household")
            if isinstance(hh, dict):
                return hh.get("id"), hh.get("name")
            return hh, None
    return None, None


def _name_or_id_matches(active_name, active_id, want_name, want_id):
    if want_name:
        return active_name == want_name
    if want_id is not None:
        return active_id == want_id
    return True


# Back-compat alias (the space check used this name).
space_matches = _name_or_id_matches
household_matches = _name_or_id_matches


class Tandoor:
    def __init__(self, url, user, password, space="Mack House", space_id=None,
                 household=None, household_id=None, timeout=30):
        self.url = url.rstrip("/")
        self.user = user
        self.password = password
        self.space = space
        self.space_id = space_id
        self.household = household
        self.household_id = household_id
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "kdk-grocery-planner"

    # ---- auth -------------------------------------------------------------
    def login(self):
        auth = f"{self.url}/_allauth/browser/v1"
        r = self.s.get(f"{auth}/config", timeout=self.timeout)
        r.raise_for_status()
        r = self.s.post(f"{auth}/auth/login",
                        json={"username": self.user, "password": self.password},
                        headers={"X-CSRFToken": self.s.cookies.get("csrftoken", ""),
                                 "Referer": f"{self.url}/"},
                        timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"Tandoor login failed: {r.status_code}")
        chk = self.s.get(f"{self.url}/api/user-preference/", timeout=self.timeout)
        if chk.status_code != 200:
            raise RuntimeError(f"Tandoor login check failed: {chk.status_code}")
        log.info("logged into Tandoor as %s", self.user)
        self.verify_space()
        self.verify_household()
        return self

    def verify_household(self):
        """Fail loudly unless the account's active household is the configured
        household -- that is the unit Tandoor 2.x shares meal plans + shopping
        lists within, so jmack/rach only see the plan if they share it. Skipped
        if no household is configured. Membership is a space-admin (owner) step."""
        if not (self.household or self.household_id is not None):
            return None, None
        hid, name = resolve_active_household(self.get_all("/api/user-space/"))
        if not household_matches(name, hid, self.household, self.household_id):
            raise RuntimeError(
                f"dashboard account's active household is {name!r} (id {hid}); "
                f"expected {self.household!r} (id {self.household_id}) -- refusing "
                "to write (set membership in Tandoor Space settings -> Households)")
        log.info("Tandoor active household verified: %s (id %s)", name, hid)
        return hid, name

    def verify_space(self):
        """Fail loudly unless the account's ACTIVE Tandoor space is the configured
        household space -- every write (recipes, books, meal plans, shopping
        entries, food categories) must land there. Tandoor scopes all writes to
        the active space, so this guards against a misconfigured account."""
        aid, name = resolve_active_space(self.get_all("/api/space/"),
                                         self.get_all("/api/user-space/"))
        if not space_matches(name, aid, self.space, self.space_id):
            raise RuntimeError(
                f"Tandoor active space is {name!r} (id {aid}); expected "
                f"{self.space!r} (id {self.space_id}) -- refusing to write")
        log.info("Tandoor active space verified: %s (id %s)", name, aid)
        return aid, name

    def _csrf_headers(self):
        return {"X-CSRFToken": self.s.cookies.get("csrftoken", ""),
                "Referer": f"{self.url}/"}

    # ---- low-level --------------------------------------------------------
    def get(self, path, **params):
        r = self.s.get(f"{self.url}{path}", params=params or None, timeout=self.timeout)
        if r.status_code in (401, 403):
            self.login()
            r = self.s.get(f"{self.url}{path}", params=params or None, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if r.text else None

    def get_all(self, path, **params):
        """Follow DRF pagination (`results` + `next`) to a full list."""
        out = []
        data = self.get(path, **params)
        while True:
            if isinstance(data, dict) and "results" in data:
                out.extend(data["results"])
                nxt = data.get("next")
                if not nxt:
                    break
                r = self.s.get(nxt, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
            else:
                out.extend(data or [])
                break
        return out

    def post(self, path, body):
        r = self.s.post(f"{self.url}{path}", json=body, headers=self._csrf_headers(),
                        timeout=self.timeout)
        if r.status_code in (401, 403):
            self.login()
            r = self.s.post(f"{self.url}{path}", json=body, headers=self._csrf_headers(),
                            timeout=self.timeout)
        r.raise_for_status()
        return r.json() if r.text else None

    def patch(self, path, body):
        r = self.s.patch(f"{self.url}{path}", json=body, headers=self._csrf_headers(),
                         timeout=self.timeout)
        r.raise_for_status()
        return r.json() if r.text else None

    # ---- domain helpers ---------------------------------------------------
    def keywords(self):
        return {k["name"]: k for k in self.get_all("/api/keyword/")}

    def ensure_keyword(self, name):
        existing = self.keywords()
        if name in existing:
            return existing[name]
        return self.post("/api/keyword/", {"name": name})

    def supermarkets(self):
        return {s["name"]: s for s in self.get_all("/api/supermarket/")}

    def supermarket_categories(self):
        return {c["name"]: c for c in self.get_all("/api/supermarket-category/")}

    def ensure_supermarket_category(self, name):
        existing = self.supermarket_categories()
        if name in existing:
            return existing[name]
        return self.post("/api/supermarket-category/", {"name": name})

    def meal_types(self):
        return {m["name"]: m for m in self.get_all("/api/meal-type/")}

    def recipe_books(self):
        return {b["name"]: b for b in self.get_all("/api/recipe-book/")}

    def ensure_recipe_book(self, name, description=""):
        existing = self.recipe_books()
        if name in existing:
            return existing[name]
        return self.post("/api/recipe-book/", {"name": name, "description": description})

    def meal_plan(self, from_date, to_date):
        return self.get_all("/api/meal-plan/",
                            from_date=from_date, to_date=to_date)

    def shopping_list_entries(self):
        return self.get_all("/api/shopping-list-entry/")

    # ---- URL import (recipe-from-source + save) ---------------------------
    def import_from_url(self, url):
        """Two-step, matching the Tandoor web importer:
        1) POST /api/recipe-from-source/ {url} -> parsed recipe JSON (not saved),
        2) POST /api/recipe/ <that recipe> -> created recipe (returns id).
        Returns (recipe_id, parsed) or raises. Duplicate detection: the source
        response includes `duplicates`; the caller may skip if non-empty."""
        src = self.post("/api/recipe-from-source/", {"url": url})
        if not src or src.get("error"):
            raise RuntimeError(f"import parse failed for {url}: {src and src.get('msg')}")
        recipe = src.get("recipe")
        if not recipe:
            raise RuntimeError(f"no recipe parsed from {url}")
        created = self.post("/api/recipe/", recipe)
        return created.get("id"), src
