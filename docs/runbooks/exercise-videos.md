---
title: Exercise videos — Jellyfin library, playlists, wger links
updated: 2026-11-14
---

# Exercise videos in Jellyfin

Every video in the exercise plan (`stacks/home-assistant/tools/wger_plan.json`, one
YouTube address per exercise) is kept as **one file** in a flat Jellyfin library
**Exercise**. Each person (**jmack**, **rach**) gets one playlist per training day, with the
videos in workout order, private to that person. Days with the same list share a playlist:

| jmack | rach |
|---|---|
| Jacob · Mon Lower A, Jacob · Wed Upper A, Jacob · Fri Lower B, Jacob · Sat Upper B | Rachel · Mon Strength A, Rachel · Tue/Fri Cardio, Rachel · Wed/Sat/Sun Daily parts, Rachel · Thu Strength B |

**Who sees what (owner decision 2026-11-14).** The library stays visible to every Jellyfin
account for now. The SSO and LDAP plugin folder settings are not changed. Only the
playlists are private: each one is owned by its person, not public, with no shares. The
owner may later hide the library per user; see
[Hide the library from other accounts](#hide-the-library-from-other-accounts-optional-later).

The `Video:` line in each wger exercise description points at the video's Jellyfin page,
`https://jellyfin.kmkdp.com/web/#/details?id=<item id>`. The same hostname works on the
LAN, on the tailnet and through the Cloudflare tunnel. The YouTube address stays in the
plan file as the download source.

Nothing here is scheduled and no service is added. After a plan change, run the steps in
[After a plan change](#after-a-plan-change) by hand.

| Piece | What it does |
|---|---|
| `tools/exercise_videos.py download` | yt-dlp, one-shot container on **kdk-dkr-01** (the only host with the media share read-write), into `/mnt/media/exercise/<plan key> [<youtube id>].mp4`, as uid:gid 1000:1000 like the media stack. Output is H.264 + AAC mp4, at most 1080p, with the plan's exercise name as the embedded title |
| `tools/yt-dlp/Dockerfile` | not built; its `FROM` line pins the yt-dlp image (tag + digest), which Renovate updates |
| `tools/exercise_videos.py jellyfin` | checks that jmack and rach can see the library (names any other account that can, without stopping), scans the Exercise library only, creates/updates the playlists (owned by the person, not public, no shares), writes `tools/wger_videos.json` |
| `tools/wger_videos.json` | committed: plan key → Jellyfin item id (+ the YouTube id it was made for) and the playlists the tool manages. `gen_wger.py` builds the `Video:` line from it |
| `stacks/jellyfin/compose.yaml` | mounts `/mnt/media/exercise` read-only at `/data/media/exercise` |

**File names never change.** Jellyfin derives an item id from the file path, so the wger
links stay valid only while the path stays the same. Names come from the plan key and the
YouTube id, never the video title. A replaced video has a new YouTube id, so it becomes a
new file and gets a new id. `gen_wger.py` refuses to write while the mapping still holds
the old video's id.

**Tdarr and Trailarr leave the folder alone, with no config change.** Verified 2026-11-14:
Tdarr's two libraries are `/data/media/movies` and `/data/media/tv`. Trailarr only follows
Radarr/Sonarr titles, and its path mappings cover only `tv/` and `movies/`. If either ever
gets a library on `/data/media` itself, add `exercise` to its ignore list.

## First install: order

**Merging the PR that adds the mount deploys it.** The jellyfin stack in
`komodo/resources.toml` has `deploy = true` and `webhook_enabled = true`, so the merge
recreates the container with the new bind mount right away. The DMZ mount of the share is
read-only, so docker cannot create a missing bind source there, and the deploy fails if
`/mnt/media/exercise` does not exist yet. The directory therefore has to exist **before**
the merge (step 0). The steps:

0. Before the merge: the directory exists on the share.
1. Merge (auto-deploy), then check that the container sees `/data/media/exercise`.
2. Jellyfin API key → 1Password.
3. Download the videos.
4. Create the library; jmack and rach can see it.
5. Scan, playlists, mapping.
6. Check it, including the third-account check.

Hiding the library from other accounts is not part of the install; it is an
[optional later step](#hide-the-library-from-other-accounts-optional-later).

## 0. Before merging: the directory exists

The share is read-write on kdk-dkr-01 only. Create the directory there as kdkadmin
(uid 1000, the share's owner and the media stack's PUID). Then check it from the DMZ host,
which is where the bind mount needs it.

**Laptop (SSH agent)**

```bash
ssh kdkadmin@10.1.20.20 'mkdir -p /mnt/media/exercise && ls -ldn /mnt/media/exercise'   # drwxr-xr-x ... 1000 1000
ssh kdkadmin@192.168.191.20 'ls -ld /mnt/media/exercise'                                # must exist BEFORE the merge
```

Do not merge while the second command answers `No such file or directory`.

## 1. Merge (= deploy), check the mount

Merging deploys the stack. This restarts Jellyfin and drops active streams for about a
minute, so merge in a quiet moment. If the webhook did not fire, run **DeployStack
`jellyfin`** in Komodo; RunSync alone does not redeploy a compose change.

**Laptop (SSH agent)**

```bash
ssh kdkadmin@192.168.191.20 'sudo docker inspect jellyfin --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}" | grep exercise'   # /mnt/media/exercise -> /data/media/exercise
ssh kdkadmin@192.168.191.20 'sudo docker exec jellyfin ls -ld /data/media/exercise'
```

## 2. Jellyfin API key → 1Password

The tool needs a **dedicated key** for this tool, following the per-consumer pattern
(see `wizarr-sync`). Do not reuse `kdk-cluster/jellyfin`.

1. In **Jellyfin → Dashboard → API Keys → +**, set App name `exercise-videos`, then copy
   the key.
2. Store it without echoing it, as an **API Credential** item with the standard
   `credential` field (the owner's convention). The key travels in JSON on stdin, so it
   never appears in argv:

**Laptop (`op` signed in, key on the clipboard)**

```bash
wl-paste -n | python3 -c 'import json,sys; print(json.dumps({"title":"jellyfin-api-exercise-videos","category":"API_CREDENTIAL","fields":[{"id":"credential","label":"credential","type":"CONCEALED","value":sys.stdin.read().strip()}]}))' \
  | op item create --vault kdk-cluster -
wl-copy --clear
K="$(op read op://kdk-cluster/jellyfin-api-exercise-videos/credential)"; echo "${#K} bytes"; unset K   # 32 bytes
```

To rotate: create a new key, run `op item edit` on the `credential` field, then delete the old key in
the dashboard. The tool only reads the key when it runs, so nothing else changes.

## 3. Download the videos (kdk-dkr-01)

Run this from the laptop. The tool reads the plan locally and runs every command on
kdk-dkr-01 over SSH (agent socket only). `sudo -n docker` needs kdkadmin's NOPASSWD.

**Laptop, repo root (SSH agent)**

```bash
T=stacks/home-assistant/tools
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20            # dry run: "47 missing"
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20 --apply    # about 47 one-shot containers
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20            # "47 present, 0 missing"
ssh kdkadmin@10.1.20.20 'ls -ln /mnt/media/exercise | head -3; du -sh /mnt/media/exercise'   # 1000 1000
```

The directory already exists from step 0 (`--apply` would create it as kdkadmin otherwise).
The download skips files that already exist, so a re-run after a failure only fetches
what is missing.

YouTube sometimes answers `HTTP Error 403: Forbidden` for a video that works on the next
try. The tool handles that itself:

- yt-dlp gets `--extractor-retries 5` and backs off between its own retries
  (`--retry-sleep http:exp=1:30`, `fragment:exp=1:30`, `extractor:5`).
- A video whose container still fails is run again, up to 4 attempts, waiting 30 s,
  60 s, then 90 s (lines `retry <file> (n/4 failed: ...)`).
- A video that fails all 4 attempts is logged as `FAILED` and the run goes on with the
  rest. There is a 5 s pause between videos.
- At the end every video still missing is listed under `ERROR n of 47 video(s) not
  created`, and the command exits 1. Run the same command again later; it fetches only
  those.

`.incoming/` holds yt-dlp's partial files. After every run (also a failed one) the tool
deletes `/mnt/media/exercise/.incoming` and nothing else. It skips that only if the
folder is a symlink or holds a finished `<key> [<id>].mp4`; then it prints a `WARN` and
you move that file up by hand. A dry run only reports it. Jellyfin skips dot folders, and
the `other file(s)` count does not include `.incoming`. Do not run two downloads at the
same time: one run's cleanup would delete the other's partial files.

Then check that Jellyfin sees the files:

```bash
ssh kdkadmin@192.168.191.20 'sudo docker exec jellyfin ls /data/media/exercise | wc -l'   # 47 (.incoming not listed)
```

## 4. Create the library (Jellyfin dashboard, as an admin)

Go to **Dashboard → Libraries → Libraries → Add Media Library**:

| Field | Value |
|---|---|
| Content type | **Home videos and photos** |
| Display name | `Exercise` (exactly: the tool finds the library by this name) |
| Folders | `/data/media/exercise` (one folder only) |
| Metadata downloaders / image fetchers | **all unticked** |
| Prefer embedded titles over filenames | ✅ (the files carry the plan's exercise name as title) |
| Save trickplay images next to media | ☐ (the mount is read-only) |
| Chapter image extraction | ☐ |

Then check that **jmack** and **rach** can see it: **Dashboard → Users → <user> → Access**
has either *Enable access to all libraries* on or Exercise ticked. The `jellyfin` step (5)
stops before any write when one of them cannot see the library. Other accounts seeing it is
fine (owner decision 2026-11-14): the run prints one `NOTE other accounts can see the
library ...` line naming them and goes on.

## 5. Scan, playlists, mapping

**Laptop, repo root (`op` signed in; LAN or tailnet: the Cloudflare WAF blocks the admin
API paths from the internet)**

```bash
T=stacks/home-assistant/tools
export JELLYFIN_API_KEY="$(op read op://kdk-cluster/jellyfin-api-exercise-videos/credential)"
printf '%s bytes\n' ${#JELLYFIN_API_KEY}                   # 32; never echo the value
python3 $T/exercise_videos.py jellyfin                    # dry run: access check (+ NOTE line), 8 "would: create playlist"
python3 $T/exercise_videos.py jellyfin --apply            # scan (waits up to 10 min), playlists, wger_videos.json
python3 $T/exercise_videos.py jellyfin --apply            # second run: only the scan, "wger_videos.json unchanged"
unset JELLYFIN_API_KEY
git diff --stat $T/wger_videos.json                       # 47 items, 8 playlists
```

Commit `wger_videos.json` in a small PR. After it merges, run `gen_wger.py --apply`
([wger runbook step 9](wger.md#9-load-the-plan-built-in-exercises--weekday-routines)) so
every exercise description gets its Jellyfin `Video:` line.

The tool resolves the users by username (`jmack`, `rach`: the plan's people logins) on
every run, so no user id is stored. It finds a playlist by the id it recorded, or else by
its exact name in that person's list.

**API key limits (Jellyfin 12.4).** An API key has no user behind it. Every
`/Playlists/{id}` call that works on "the calling user" answers `400 Error processing
request.`: read, users, update, remove item and move item. So the tool:

- creates a playlist with `POST /Playlists` and the owner in the body (`UserId`,
  `IsPublic: false`, no `Users`);
- never updates one in place. A recorded or same-named playlist whose name or order is
  wrong is deleted with `DELETE /Items/{id}` and created again. That gives it a new id;
  nothing links to playlist ids except `wger_videos.json`;
- verifies each playlist after the write, as the owner and as every other account:
  - `GET /Playlists/{id}/Items?userId=<owner>` gives the plan's videos in workout order;
  - for each other account (the other plan person and admins included), the playlist is
    not in `GET /Items?userId=<them>&IncludeItemTypes=Playlist`, and
    `GET /Items/{id}?userId=<them>` is a 404.

  If any other account can see a playlist, the run stops with exit 2 and names it.

It never deletes a playlist it did not make: only one it recorded (with `--prune`) or one
it is re-creating because it carries the plan's exact name.

**A run that stops midway** (a failed check, an HTTP error) still writes
`wger_videos.json`. The file gets all item ids, since by then every plan file is an item,
and only the playlists verified so far. `gen_wger.py` can run on it, and the next
`jellyfin --apply` resumes: it finds the playlists already made by id or name and creates
the rest.

## 6. Check it

- jmack's Jellyfin home shows **Exercise** and four playlists, each in workout order (the
  walk first, core last). Rachel sees her four.
- Tap a `Video:` address in wger on the phone, once on Wi-Fi and once on cellular (through
  the tunnel). It opens the video in the browser after the Jellyfin login.
- **Third account (required):** open a private window, sign in as an account that is
  neither jmack nor rach (a Wizarr invitee or a test account), first with the password
  form and then once with **Sign in with SSO**. Do it on the LAN and through the tunnel
  (phone on cellular). Then check:
  1. Home shows the Exercise library (expected while it is not hidden).
  2. *Playlists* lists none of the Jacob/Rachel playlists, and searching for `Jacob` or
     `Rachel` finds no playlist.
  3. Paste a playlist address of jmack's (`/web/#/details?id=<playlist id>` from
     `wger_videos.json`). The page shows an error or nothing, not the playlist.

## Hide the library from other accounts (optional, later)

Not done now (owner decision 2026-11-14): every account can see Exercise and the SSO and
LDAP plugin settings stay as they are. If the owner later wants the library hidden, three
things grant library access, and all three must change:

1. **Each user's policy.** Go to **Dashboard → Users → <user> → Access**. For each
   account except jmack and rach (kdkadmin and Wizarr invitees included), untick
   **Enable access to all libraries** and tick only that user's libraries (Movies, Shows,
   …), leaving Exercise off. For jmack and rach, tick Exercise as well. An administrator
   is not exempt: with *all libraries* on, the admin sees Exercise too.
2. **The SSO Authentication plugin** (*Enable Authorization by Plugin* is on, see
   [jellyfin-sso](jellyfin-sso.md)). It rewrites a user's folders on **every** SSO login.
   With *Enable All Folders* ✅ (verified 2026-11-14), each SSO login grants every library
   again, so a per-user untick alone does not hold for anyone who signs in with SSO. To
   hide it, change the plugin form:
   - **Enable All Folders**: ☐. Under **Enabled Folders**, tick every library except
     Exercise.
   - **Enable Role-Based Folder Access**: ✅. Add a mapping from the LLDAP group
     `exercise-users` to the folder Exercise. First create the group in
     `users.kmkdp.com` and add jmack and rach to it.
3. **The LDAP-Auth plugin** creates users from LDAP with *Enable All Folders* ✅ (verified
   2026-11-14), so every new LDAP or Wizarr account starts with Exercise. Untick it and
   tick every library except Exercise. Check Wizarr's invitation library choice too
   (Wizarr → Settings → the Jellyfin server's libraries).

Afterwards, sign in once as jmack **through SSO** and check that Exercise is still on that
user's Access tab. That proves the role mapping works; the plugin's role-folder behaviour
has not been tested here. Then repeat the third-account check (step 6): the library is now
gone from Home, a search for `plank` finds no exercise video, a `Video:` address shows an
error, and after the SSO login the account's Access tab still has Exercise unticked. The
`jellyfin` run's `NOTE other accounts` line should then be gone.

## After a plan change

Use this when an exercise was added, a video replaced, or a routine changed:

1. Edit `wger_plan.json`, test, PR, merge ([exercise plan update](workout-tracking-update.md)).
2. Run `download` (step 3). It fetches the new files and lists the old ones as `EXTRA`.
3. Run `jellyfin --apply` (step 5), then commit `wger_videos.json`.
4. Run `gen_wger.py --apply` and then `--check`.
5. Once everything works, delete the leftovers: `download --apply --prune` removes files
   that match the `<key> [<id>].mp4` pattern and are no longer in the plan (any other file
   in the folder is reported and never touched). `jellyfin --apply --prune` deletes
   playlists the tool made whose day left the plan.

`gen_wger.py` refuses `--apply` (exit 2, nothing written) and fails `--check` while any
plan exercise lacks an item id for its current YouTube video. A dry run warns and leaves
those descriptions alone. It never writes a YouTube or empty `Video:` line.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `retry ... HTTP Error 403: Forbidden`, then `FAILED <file> after 4 attempts` | YouTube refuses the video data now and then; the same video usually works a few minutes later. The rest of the run still downloads. Re-run step 3 (`--apply`) later; it fetches only the listed files. If the same video fails across several runs, open it in a browser: it may be private, removed or region-locked. Replace it in `wger_plan.json` (see *After a plan change*). |
| `ERROR n of 47 video(s) not created`, a file marked `yt-dlp exited 0 but the file is not there` | yt-dlp wrote it under a different name or not at all. Check the run's output for that file and `ls -la /mnt/media/exercise/.incoming` on kdk-dkr-01. |
| `jellyfin`: `ERROR GET /Playlists/<id>: HTTP 400 Error processing request.` | An older tool version. With an API key that call has no user and always fails; the current tool never calls it. Update the checkout and run again. The first playlist it made is reused, not duplicated. |
| `jellyfin`: `playlist '...' (<id>) is visible to <user> (...)`, exit 2 | The playlist is public or shared. As the owner in Jellyfin, open the playlist, remove the shares and make it private. Or delete it; the next run creates it again. `wger_videos.json` has already been written with the item ids and the playlists that passed. |
| `jellyfin`: `ACCESS jmack cannot see the library`, exit 2, nothing written | That plan person has neither *all libraries* nor Exercise ticked. Fix it as in step 4, then run again. A `NOTE other accounts can see the library` line is expected and does not stop the run. |
| `WARN .incoming/ holds finished video(s), left alone` | A run stopped after the file was done but before yt-dlp moved it. Move it up: `ssh kdkadmin@10.1.20.20 'mv "/mnt/media/exercise/.incoming/<file>" /mnt/media/exercise/'`, then run step 3 again; it removes the empty folder. |

## Rollback

1. Revert the merged PR (the compose mount, the tools, and `gen_wger.py` back to YouTube
   links). Merging the revert redeploys jellyfin (DeployStack if the webhook did not fire);
   then run `gen_wger.py --apply`. Remove the directory (step 3) only after that deploy.
2. In the dashboard, delete the Exercise library and the eight playlists.
3. Optional: `ssh kdkadmin@10.1.20.20 'rm -r /mnt/media/exercise'`.
4. Delete the API key in the dashboard and archive `kdk-cluster/jellyfin-api-exercise-videos`.
