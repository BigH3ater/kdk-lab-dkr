#!/usr/bin/env python3
"""Finish what a Wizarr "Create LDAP user" invite leaves half-done.

Wizarr makes an lldap user (uid=<name>,ou=people,dc=kmkdp,dc=com) and a local
Jellyfin account on an accepted invite, but adds NO group and leaves the Jellyfin
account on the DEFAULT auth provider. So the invitee cannot log in via LDAP/SSO
(Jellyfin's LDAP plugin only accepts members of cn=jellyfin-users) and is unknown
to Seerr. For every Wizarr `user` row with is_ldap_user=1 this reconciles, idempotently:

  (a) lldap    -> ensure membership in jellyfin-users (and nothing else)
  (b) Jellyfin -> set AuthenticationProviderId to the LDAP plugin if not already
  (c) Seerr    -> import the Jellyfin user if Seerr has no user with that id

Nothing else: no other groups, no permission changes. Stdlib only. One line per
action. DRY_RUN=1 reports without changing anything. Pushover summary only when
something changed or errored. Exit 1 on any error.
"""
import json
import os
import shutil
import sqlite3
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
WIZARR_DB = os.environ.get("WIZARR_DB", "/wizarr/database.db")
SEERR_SETTINGS = os.environ.get("SEERR_SETTINGS", "/seerr-settings.json")
PUSHOVER_ENV = os.environ.get("PUSHOVER_ENV", "/kdk/pushover.env")

LLDAP_URL = os.environ.get("LLDAP_URL", "http://lldap:17170").rstrip("/")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://jellyfin:8096").rstrip("/")
SEERR_URL = os.environ.get("SEERR_URL", "http://seerr:5055").rstrip("/")
JELLYFIN_GROUP = os.environ.get("JELLYFIN_GROUP", "jellyfin-users")
LDAP_PROVIDER_ID = os.environ.get(
    "LDAP_PROVIDER_ID",
    "Jellyfin.Plugin.LDAP_Auth.LdapAuthenticationProviderPlugin",
)
LLDAP_ADMIN_PASSWORD = os.environ.get("LLDAP_ADMIN_PASSWORD", "")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")

CTX = ssl.create_default_context()

actions = []   # one line per change made (or WOULD make)
errors = []


def log(*a):
    print(*a, flush=True)


def err(msg):
    errors.append(msg)
    print("ERROR", msg, file=sys.stderr, flush=True)


def _req(url, method="GET", headers=None, body=None, timeout=60):
    data = None
    if body is not None:
        data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Accept", "application/json")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)


# ---------- inputs ----------
def wizarr_ldap_users():
    """(username, email) for every Wizarr user row with is_ldap_user set.

    The Wizarr DB is WAL mode and its directory is mounted read-only. A plain
    `?mode=ro` connection fails there ("attempt to write a readonly database")
    because SQLite must write the wal-index/-shm. So copy database.db + -wal +
    -shm into a writable tmp dir and open the copy read-write: SQLite replays the
    WAL (only valid, matching frames) and we read the latest committed rows. The
    copy is a point-in-time snapshot; a torn copy is safe (idempotent, next run
    catches up) because SQLite validates WAL frame checksums/salt.
    """
    tmp = tempfile.mkdtemp(prefix="wizarr-db-")
    try:
        base = os.path.basename(WIZARR_DB)
        src_dir = os.path.dirname(WIZARR_DB) or "."
        for suffix in ("", "-wal", "-shm"):
            src = os.path.join(src_dir, base + suffix)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp, base + suffix))
        con = sqlite3.connect(os.path.join(tmp, base))
        try:
            rows = con.execute(
                "SELECT DISTINCT username, email FROM user WHERE is_ldap_user = 1"
            ).fetchall()
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [(u, e) for (u, e) in rows if u]


def seerr_api_key():
    with open(SEERR_SETTINGS) as f:
        return (json.load(f).get("main") or {}).get("apiKey") or ""


# ---------- lldap ----------
def lldap_token():
    _, body = _req(
        f"{LLDAP_URL}/auth/simple/login",
        "POST",
        body={"username": "admin", "password": LLDAP_ADMIN_PASSWORD},
    )
    tok = (body or {}).get("token")
    if not tok:
        raise RuntimeError("lldap login returned no token")
    return tok


def lldap_gql(token, query, variables=None):
    _, body = _req(
        f"{LLDAP_URL}/api/graphql",
        "POST",
        headers={"Authorization": f"Bearer {token}"},
        body={"query": query, "variables": variables or {}},
    )
    if body and body.get("errors"):
        raise RuntimeError(f"graphql: {body['errors']}")
    return (body or {}).get("data") or {}


def lldap_group_id(token, name):
    data = lldap_gql(token, "{ groups { id displayName } }")
    for g in data.get("groups", []):
        if g["displayName"] == name:
            return g["id"]
    return None


def lldap_user_group_ids(token, uid):
    data = lldap_gql(
        token,
        "query($u:String!){ user(userId:$u){ groups { id } } }",
        {"u": uid},
    )
    u = data.get("user")
    return {g["id"] for g in u["groups"]} if u else None


def lldap_add_to_group(token, uid, gid):
    lldap_gql(
        token,
        "mutation($u:String!,$g:Int!){ addUserToGroup(userId:$u, groupId:$g){ ok } }",
        {"u": uid, "g": gid},
    )


# ---------- jellyfin ----------
def jf_headers():
    # Jellyfin 12 requires the Authorization: MediaBrowser scheme; the legacy
    # X-Emby-Token header path is gone (same reason Seerr 3.5.0 switched).
    return {"Authorization": f'MediaBrowser Token="{JELLYFIN_API_KEY}"'}


def jf_users():
    _, body = _req(f"{JELLYFIN_URL}/Users", headers=jf_headers())
    return body or []


def jf_set_ldap_provider(user):
    policy = dict(user.get("Policy") or {})
    policy["AuthenticationProviderId"] = LDAP_PROVIDER_ID
    _req(
        f"{JELLYFIN_URL}/Users/{user['Id']}/Policy",
        "POST",
        headers=jf_headers(),
        body=policy,
    )


# ---------- seerr ----------
def _norm(guid):
    return (guid or "").replace("-", "").lower()


def seerr_existing_jellyfin_ids(api_key):
    ids, skip, take = set(), 0, 100
    while True:
        _, body = _req(
            f"{SEERR_URL}/api/v1/user?take={take}&skip={skip}",
            headers={"X-Api-Key": api_key},
        )
        results = (body or {}).get("results") or []
        for u in results:
            if u.get("jellyfinUserId"):
                ids.add(_norm(u["jellyfinUserId"]))
        if len(results) < take:
            break
        skip += take
    return ids


def seerr_import(api_key, jf_id):
    _req(
        f"{SEERR_URL}/api/v1/user/import-from-jellyfin",
        "POST",
        headers={"X-Api-Key": api_key},
        body={"jellyfinUserIds": [jf_id]},
    )


# ---------- main ----------
def main():
    if not LLDAP_ADMIN_PASSWORD or not JELLYFIN_API_KEY:
        err("missing LLDAP_ADMIN_PASSWORD or JELLYFIN_API_KEY")
        finish()

    users = wizarr_ldap_users()
    log(f"wizarr: {len(users)} LDAP user(s) to reconcile (dry_run={DRY_RUN})")
    if not users:
        finish()

    # lldap setup (fatal if the group or login fails -- nothing can proceed).
    try:
        token = lldap_token()
        gid = lldap_group_id(token, JELLYFIN_GROUP)
    except Exception as e:  # noqa: BLE001
        err(f"lldap connect/login: {e}")
        finish()
    if gid is None:
        err(f"lldap group {JELLYFIN_GROUP!r} not found")
        finish()

    # Jellyfin user index by lowercased name.
    try:
        jf_index = {(u.get("Name") or "").lower(): u for u in jf_users()}
    except Exception as e:  # noqa: BLE001
        err(f"jellyfin user list: {e}")
        jf_index = None

    # Seerr existing ids (one call set; import is per-user below).
    try:
        key = seerr_api_key()
        if not key:
            raise RuntimeError("no main.apiKey in seerr settings")
        seerr_ids = seerr_existing_jellyfin_ids(key)
    except Exception as e:  # noqa: BLE001
        err(f"seerr user list: {e}")
        key, seerr_ids = None, None

    for username, _email in users:
        uid = username
        # (a) lldap group membership
        try:
            have = lldap_user_group_ids(token, uid)
            if have is None:
                err(f"lldap: user {uid!r} not found")
            elif gid not in have:
                if DRY_RUN:
                    actions.append(f"WOULD add {uid} to {JELLYFIN_GROUP} (lldap)")
                else:
                    lldap_add_to_group(token, uid, gid)
                    actions.append(f"lldap: added {uid} to {JELLYFIN_GROUP}")
        except Exception as e:  # noqa: BLE001
            err(f"lldap {uid}: {e}")

        # (b) Jellyfin auth provider
        jf_user = None
        if jf_index is not None:
            jf_user = jf_index.get(uid.lower())
            if jf_user is None:
                err(f"jellyfin: no account named {uid!r}")
            else:
                current = (jf_user.get("Policy") or {}).get("AuthenticationProviderId")
                if current != LDAP_PROVIDER_ID:
                    try:
                        if DRY_RUN:
                            actions.append(f"WOULD set LDAP provider on jellyfin {uid}")
                        else:
                            jf_set_ldap_provider(jf_user)
                            actions.append(f"jellyfin: set LDAP provider on {uid}")
                    except Exception as e:  # noqa: BLE001
                        err(f"jellyfin policy {uid}: {e}")

        # (c) Seerr import (needs the Jellyfin id)
        if seerr_ids is not None and jf_user is not None:
            jf_id = jf_user["Id"]
            if _norm(jf_id) not in seerr_ids:
                try:
                    if DRY_RUN:
                        actions.append(f"WOULD import {uid} into seerr ({jf_id})")
                    else:
                        seerr_import(key, jf_id)
                        seerr_ids.add(_norm(jf_id))
                        actions.append(f"seerr: imported {uid} ({jf_id})")
                except Exception as e:  # noqa: BLE001
                    err(f"seerr import {uid}: {e}")

    finish()


def pushover(title, message, priority=0):
    creds = {}
    try:
        for line in open(PUSHOVER_ENV):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip().strip('"')
    except OSError:
        log("pushover.env unreadable; skipping notification")
        return
    tok, usr = creds.get("PUSHOVER_TOKEN"), creds.get("PUSHOVER_USER")
    if not tok or not usr:
        return
    data = urllib.parse.urlencode(
        {"token": tok, "user": usr, "title": title,
         "message": message[:1000], "priority": priority}
    ).encode()
    try:
        urllib.request.urlopen(
            "https://api.pushover.net/1/messages.json", data, timeout=25, context=CTX
        ).read()
    except Exception as e:  # noqa: BLE001
        log(f"pushover failed: {e}")


def finish():
    for a in actions:
        log(a)
    summary = (f"{'DRY RUN: ' if DRY_RUN else ''}"
               f"{len(actions)} change(s), {len(errors)} error(s)")
    log(summary)
    if errors:
        pushover("kdk-lab wizarr-sync FAILED",
                 summary + "\n" + "\n".join(errors[:8]), priority=1)
    elif actions and not DRY_RUN:
        pushover("kdk-lab wizarr-sync", summary + "\n" + "\n".join(actions[:12]))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
