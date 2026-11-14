---
title: Exercise videos — Jellyfin library, playlists, wger links
updated: 2026-11-14
---

# Exercise videos in Jellyfin

Every video in the exercise plan (`stacks/home-assistant/tools/wger_plan.json`, one
YouTube address per exercise) is kept as **one file** in a flat Jellyfin library
**Exercise**: 46 files for 47 exercises. `treadmill-walk` (Treadmill Cardio) has
`"video": null` (owner decision 2026-11-14: not needed). It stays in the plan and in jmack's
routines, so the walk is still logged in wger, but it has no file, no playlist entry, no
mapping entry and no `Video:` line. Only an explicit `null` means "no video"; a missing or
empty `video` is a plan error. Each person (**jmack**, **rach**) gets one playlist per training day, with the
videos in workout order, private to that person. Days with the same list share a playlist:

| jmack | rach |
|---|---|
| Jacob · 1 Mon Lower A, Jacob · 2 Wed Upper A, Jacob · 3 Fri Lower B, Jacob · 4 Sat Upper B | Rachel · 1 Mon Strength A, Rachel · 2 Tue/Fri Cardio, Rachel · 3 Wed/Sat/Sun Daily parts, Rachel · 4 Thu Strength B |

The number after the person is the playlist's place in that person's week (a shared playlist
takes the place of its first day). Jellyfin lists playlists by name, so the number keeps
them in week order; with 10 or more for one person the tool zero-pads it (`01`, `02`, …).
Until 2026-11-14 the names had no number (`Jacob · Mon Lower A`); see
[Migration from the unnumbered names](#migration-from-the-unnumbered-names-2026-11-14).

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
| `tools/exercise_videos.py download` | yt-dlp, one-shot container on **kdk-dkr-01** (the only host with the media share read-write), into `/mnt/media/exercise/<plan key> [<youtube id>].mp4`, as uid:gid 1000:1000 like the media stack. Output is H.264 + AAC mp4, the largest YouTube has up to 1080 on the **short** side (see [Resolution](#resolution)), with the plan's exercise name as the embedded title. `--refetch <key>` replaces a file in place, `--probe` reports each file's resolution |
| `tools/yt-dlp/Dockerfile` | not built; its `FROM` line pins the yt-dlp image (tag + digest), which Renovate updates |
| `tools/exercise_videos.py jellyfin` | checks that jmack and rach can see the library (names any other account that can, without stopping), scans the Exercise library only, creates/updates the playlists (owned by the person, not public, no shares), writes `tools/wger_videos.json` |
| `tools/wger_videos.json` | committed: plan key → Jellyfin item id (+ the YouTube id it was made for) and the playlists the tool manages. `gen_wger.py` builds the `Video:` line from it |
| `stacks/jellyfin/compose.yaml` | mounts `/mnt/media/exercise` read-only at `/data/media/exercise`, and `/mnt/media/exercise-days` at `/data/media/exercise-days` |
| `/mnt/media/exercise-days/<day>/` | since 2026-11-14: one folder of hardlinks per playlist, one Jellyfin library each, for the Apple TV. See [Day libraries](#day-libraries-for-the-apple-tv-option-b-2026-11-14) |

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
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20            # dry run: "46 missing"
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20 --apply    # about 46 one-shot containers
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20            # "46 present, 0 missing"
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
- At the end every video still missing is listed under `ERROR n of 46 video(s) not
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
ssh kdkadmin@192.168.191.20 'sudo docker exec jellyfin ls /data/media/exercise | wc -l'   # 46 (.incoming not listed)
```

### Resolution

The format rule: H.264 video + AAC audio merged into mp4 (the Apple TV client direct-plays
that), the largest stream YouTube has **up to 1080 on the short side**: 1920x1080 for a
landscape video, 1080x1920 for a portrait one. In yt-dlp terms:
`-f "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1][acodec^=mp4a]" -S res:1080`. The
cap is the sort field `res`, which yt-dlp computes as the smaller dimension (README,
*Sorting Formats*); a `[height<=1080]` filter would drop every portrait stream taller than
1080. Until 2026-11-14 the tool used that filter, so the portrait videos goblet-squat and
suitcase-carry came down at 480x854.

Some videos only exist at 720 on the short side as H.264 (2026-11-14: plank, farmer-carry,
hammer-curl, rear-delt-raise, lateral-raise, plank-shoulder-tap at 1280x720, and the
portrait goblet-squat at 720x1280). They stay; `--probe` lists them as `LOW`, which is
expected for those seven.

**Check the files** (read-only: one container, no network, the share mounted read-only,
ffprobe from the pinned yt-dlp image):

```bash
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20 --probe   # "ok  1920x1080 ..." / "LOW 480x854 ... <- <url>"
```

**Check what YouTube has for a `LOW` one** (laptop, any recent yt-dlp; nothing is
downloaded). It prints the stream the tool would take:

```bash
yt-dlp -f "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1][acodec^=mp4a]" -S res:1080 \
  --print "%(width)sx%(height)s %(vcodec)s" "https://www.youtube.com/watch?v=<id>"
```

If that is larger than the file, refetch it. If it is the same, YouTube has nothing better:
keep it, or replace the video in the plan (*After a plan change*).

### Replace a file in place (`--refetch`)

```bash
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20 --refetch goblet-squat --refetch suitcase-carry           # dry run: "would replace ..."
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20 --refetch goblet-squat --refetch suitcase-carry --apply
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20 --probe
```

The new copy downloads into `.incoming/` (same retries as a normal download). Only when
yt-dlp has finished and the file is there and not empty does the tool `mv` it over
`<key> [<id>].mp4`. The path does not change, so the Jellyfin item id, the playlists and the
wger links stay. A failed refetch leaves the old file in place and exits 1. Then run
`jellyfin --apply` (step 5): the scan picks up the changed file, and `wger_videos.json`
stays unchanged.

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
python3 $T/exercise_videos.py jellyfin                    # dry run: access check (+ NOTE line), 8 "would: create playlist" (+ 8 "would: delete" once, see Migration)
python3 $T/exercise_videos.py jellyfin --apply            # scan (waits up to 10 min), playlists, wger_videos.json
python3 $T/exercise_videos.py jellyfin --apply            # second run: only the scan, "wger_videos.json unchanged"
unset JELLYFIN_API_KEY
git diff --stat $T/wger_videos.json                       # 46 items (no treadmill-walk), 8 playlists
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
- never updates or renames one in place. A recorded or same-named playlist whose order is
  wrong is deleted with `DELETE /Items/{id}` and created again. A playlist with the old
  (unnumbered) name is replaced the other way round: the new-named one is created and
  verified first, then the old one is deleted. Either way it gets a new id; nothing links
  to playlist ids except `wger_videos.json`;
- does not set a Sort Name (`ForcedSortName`). `POST /Playlists` has no field for it
  (the 12.x OpenAPI `CreatePlaylistDto` is Name, Ids, UserId, MediaType, Users, IsPublic),
  and `POST /Items/{id}` would mean writing back a whole item record with an API key,
  untested here. The number in the name is enough for the name sort;
- verifies each playlist after the write, as the owner and as every other account:
  - `GET /Playlists/{id}/Items?userId=<owner>` gives the plan's videos in workout order;
  - for each other account (the other plan person and admins included), the playlist is
    not in `GET /Items?userId=<them>&IncludeItemTypes=Playlist`, and
    `GET /Items/{id}?userId=<them>` is a 404.

  If any other account can see a playlist, the run stops with exit 2 and names it.

It never deletes a playlist it did not make. Besides `--prune` (only playlists it recorded
whose day left the plan), it deletes only a playlist in that person's list with an exact
generated name (current or old scheme), and only when no other account can see it, so it is
that person's own and not a share. A playlist someone made by hand (`Jacob favourites`,
`Jacob · Mon Lower A (copy)`) is never touched. A recorded playlist the person renamed by
hand is left alone with a `NOTE` line and dropped from `wger_videos.json`.

### Migration from the unnumbered names (2026-11-14)

The first `jellyfin --apply` after this change replaces the eight old playlists. It finds
them by the ids in `wger_videos.json`, or (with an empty mapping) by their exact old name in
that person's list. For each plan playlist, in order:

1. `POST /Playlists` creates `Jacob · 1 Mon Lower A` (etc.);
2. the usual checks: `GET /Playlists/{new id}/Items?userId=<owner>` and, for every other
   account, `GET /Items?userId=<them>&IncludeItemTypes=Playlist` and
   `GET /Items/{new id}?userId=<them>`;
3. the same visibility checks on the old playlist (it must be the owner's only), then
   `DELETE /Items/{old id}`.

The dry run lists all of it: 8 `would: create playlist '<new name>'` and 8
`would: delete playlist '<old name>' (<id>) of <login>: replaced by '<new name>'` lines.
A failed delete prints `ERROR could not delete playlist ...`, marks that `did:` line
`-- FAILED`, carries on with the rest, records only the new (verified) playlists and exits 1.
The next run finds the leftover by its exact old name and deletes it again (or delete it
by hand in Jellyfin as its owner). After a clean run, the next one writes nothing but the
scan.

**A run that stops midway** (a failed check, an HTTP error) still writes
`wger_videos.json`. The file gets all item ids, since by then every plan file is an item,
and only the playlists verified so far. `gen_wger.py` can run on it, and the next
`jellyfin --apply` resumes: it finds the playlists already made by id or name and creates
the rest.

## 6. Check it

- jmack's Jellyfin home shows **Exercise** and four playlists of 9 videos each, in workout
  order (the first lift first, core last; the treadmill walk has no video). Rachel sees her
  four.
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

## Day libraries for the Apple TV (option B, 2026-11-14)

**Why.** The Apple TV client, Neptune 1.4.3, cannot use the playlists or a folder (tested
2026-11-14):

- **Playlists do not play.** Neptune requests PlaybackInfo for the playlist item itself,
  and the server answers "has no media sources".
- **Folders are not shown.** Neptune flattens a library into one grid sorted by name. A
  subfolder of hardlinks inside Exercise showed as a folder in the web client, but Neptune
  showed its ten copies as duplicates in the flat grid.

What Neptune does handle is a library shown as one flat grid sorted by name, and playing a
single video from it. So the owner chose one library per person and day (option B). The
Exercise library, the 8 playlists and the wger links stay exactly as they are.

**What the tool builds.**

- **Day folders** on the share (`download`, kdk-dkr-01): `/mnt/media/exercise-days/<name>/`,
  one per playlist and grouped the same way, so Rachel's identical days share one. Each holds
  **hardlinks** `<NN> <exercise name>.mp4` to the Exercise files. They are on the same NFS
  filesystem (`findmnt`: one `/mnt/media` mount, verified 2026-11-14), so they take no extra
  space. NN is the position in the day's workout order among the exercises that have a
  video (the treadmill walk has none, so jmack's days have 9).
- **Day libraries** (`jellyfin`): one Home videos library per folder, with the same name and
  the folder `/data/media/exercise-days/<name>`. The tool sets the library options:
  - every metadata downloader and image fetcher off;
  - *Prefer embedded titles* **off**, so the item name is the file name
    (`01 Dumbbell Goblet Squat`) and the name sort equals the workout order. The embedded
    title is the plain exercise name and would lose the order;
  - no trickplay, chapter images or real-time monitoring.

| Library (= folder) | Videos |
|---|---|
| Jacob · 1 Mon Lower A, Jacob · 2 Wed Upper A, Jacob · 3 Fri Lower B, Jacob · 4 Sat Upper B | 9 each |
| Rachel · 1 Mon Strength A, Rachel · 2 Tue-Fri Cardio, Rachel · 3 Wed-Sat-Sun Daily parts, Rachel · 4 Thu Strength B | 7, 5, 4, 7 |

Names are made file-system safe: `/` becomes `-`, and `\ : * ? " < > | [ ]` are dropped
(`Yoga exercise: Cow-cat` → `05 Yoga exercise Cow-cat.mp4`).

**Access** stays as it is (owner decision): jmack, rach and the admin have *all libraries*,
so they see all 8 day libraries; the SSO and LDAP plugin settings are not touched. Each run
prints `NOTE library '<name>' is visible to: ...`. If a plan person cannot see their own day
library, it prints `WARN <login> cannot see library ...`.

**What the tool may touch.**

- **On the share:** only inside `/mnt/media/exercise-days`, only folder names of the
  generated shape (`<Person> · <n> ...`) and file names `<NN> <name>.mp4`. Symlinks are
  never followed. Anything else there is listed as `OTHER` and never touched.
- **In Jellyfin:** a library is deleted only with `--prune`, and only when all of these
  hold: its name left the plan, `wger_videos.json` records it with origin `api` (the tool
  created it), and the server's library of that exact name still has the recorded id.
  Exercise, Movies, Shows and Collections are never deleted. A library made by hand
  (origin `hand`) is never deleted.

### Pre-merge check (the compose mount)

Merging the PR deploys jellyfin with the new read-only bind mount (`deploy = true`, webhook).
Docker cannot create a missing bind source on the read-only DMZ mount, so the folder must
exist first. The owner created the empty `/mnt/media/exercise-days` on 2026-11-14 and
checked that kdk-dkr-dmz-01 sees it. Check it again just before the merge:

```bash
ssh kdkadmin@10.1.20.20 'ls -ldn /mnt/media/exercise-days'        # drwxrwxr-x ... 1000 1000
ssh kdkadmin@192.168.191.20 'ls -ld /mnt/media/exercise-days'     # must exist BEFORE the merge
```

### Steps after the merge

**Laptop, repo root (SSH agent; `op` signed in; LAN or tailnet)**

```bash
T=stacks/home-assistant/tools
# 1. the mount is in the container
ssh kdkadmin@192.168.191.20 'sudo docker exec jellyfin ls -ld /data/media/exercise-days'
# 2. day folders (dry run, then apply, then a no-op run)
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20            # "would link ...", "59 new (planned)"
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20 --apply    # 8 folders, 59 hardlinks
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20            # "links 59 ok, 0 new, 0 re-linked, 0 removed"
ssh kdkadmin@10.1.20.20 'stat -c "%h %i %n" "/mnt/media/exercise/goblet-squat [D1IEI-lfLxU].mp4" "/mnt/media/exercise-days/Jacob · 1 Mon Lower A/01 Dumbbell Goblet Squat.mp4"'   # same inode, link count 2
ssh kdkadmin@192.168.191.20 'sudo docker exec jellyfin ls "/data/media/exercise-days/Jacob · 1 Mon Lower A"'
# 3. libraries: dry run, apply, second run
export JELLYFIN_API_KEY="$(op read op://kdk-cluster/jellyfin-api-exercise-videos/credential)"
python3 $T/exercise_videos.py jellyfin            # 8 "would: create library ...", 8 "would: scan library ..."
python3 $T/exercise_videos.py jellyfin --apply    # creates, scans, "ok library '<name>': n item(s) in workout order" x8
python3 $T/exercise_videos.py jellyfin --apply    # only scans; "wger_videos.json unchanged"
unset JELLYFIN_API_KEY
git diff $T/wger_videos.json                      # new "libraries": 8 entries, origin api (or hand)
```

Commit `wger_videos.json` in a small PR. Then check on the Apple TV, as jmack and as rach:
each day library shows its videos in workout order (`01 …` first), and a video plays.

**If the server refuses the create** (`could not create library ...: HTTP 401/403`), the run
prints `HAND STEPS` and exits 1. For each library, in **Dashboard → Libraries → Add Media
Library**:

| Field | Value |
|---|---|
| Content type | Home videos and photos |
| Display name | exactly the printed name |
| Folders | `/data/media/exercise-days/<name>` (one folder) |
| Metadata downloaders / image fetchers | all unticked |
| Prefer embedded titles over filenames | ☐ **off** |
| Real-time monitoring, trickplay, chapter images | ☐ |

Then run `jellyfin --apply` again. It finds each library by exact name, type and folder,
records it with origin `hand`, re-applies drifted options and verifies the order.

**After a re-fetch or a plan change.** `download --refetch` moves a new file over the old
name, which breaks the old hardlink. The same run then re-links it (the inode differs), and
so do `download --apply` and `--apply --prune`. The day libraries keep their item ids (same
path). Run `jellyfin --apply` afterwards so the libraries are scanned.

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

Use this when an exercise was added, a video replaced or removed (`"video": null`), or a
routine changed:

1. Edit `wger_plan.json`, test, PR, merge ([exercise plan update](workout-tracking-update.md)).
2. Run `download` (step 3). It fetches the new files and lists the old ones as `EXTRA`.
   The same run brings the day folders up to date: new and re-linked links, and removed
   links for exercises a day no longer has. A whole folder whose day left the plan is
   listed as `EXTRA day folder`.
3. Run `jellyfin --apply` (step 5), then commit `wger_videos.json`. This creates and
   verifies any new day library.
4. Run `gen_wger.py --apply` and then `--check`.
5. Once everything works, delete the leftovers, `jellyfin` first:
   - `jellyfin --apply --prune` deletes the playlists the tool made whose day left the
     plan, and the day libraries it created (origin `api`) whose name left the plan.
   - `download --apply --prune` removes files that match the `<key> [<id>].mp4` pattern
     and are no longer in the plan (any other file in the folder is reported and never
     touched). It also empties and removes day folders that left the plan; a folder that
     still holds a foreign file keeps it, and the run exits 1.

`gen_wger.py` refuses `--apply` (exit 2, nothing written) and fails `--check` while any
plan exercise lacks an item id for its current YouTube video. A dry run warns and leaves
those descriptions alone. It never writes a YouTube or empty `Video:` line.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `retry ... HTTP Error 403: Forbidden`, then `FAILED <file> after 4 attempts` | YouTube refuses the video data now and then; the same video usually works a few minutes later. The rest of the run still downloads. Re-run step 3 (`--apply`) later; it fetches only the listed files. If the same video fails across several runs, open it in a browser: it may be private, removed or region-locked. Replace it in `wger_plan.json` (see *After a plan change*). |
| A video plays at low resolution (480p or less), or `--probe` prints `LOW` | Run the format check in [Resolution](#resolution) for its YouTube id. Larger available: `download --refetch <key> --apply`, then `jellyfin --apply`. Same size: YouTube has nothing better; keep it (the seven 720-only videos listed there) or replace the video in the plan. If Jellyfin still shows the old size after the scan, open the item → ⋯ → **Refresh metadata** (*Replace all metadata*). |
| `--refetch`: `FAILED <file>: .incoming/<file> already exists` | An earlier refetch finished but was not moved. Check that file (`ls -la /mnt/media/exercise/.incoming` on kdk-dkr-01), delete it or move it over the old one by hand, then run again. |
| `ERROR n of 46 video(s) not created`, a file marked `yt-dlp exited 0 but the file is not there` | yt-dlp wrote it under a different name or not at all. Check the run's output for that file and `ls -la /mnt/media/exercise/.incoming` on kdk-dkr-01. |
| `jellyfin`: `ERROR GET /Playlists/<id>: HTTP 400 Error processing request.` | An older tool version. With an API key that call has no user and always fails; the current tool never calls it. Update the checkout and run again. The first playlist it made is reused, not duplicated. |
| `jellyfin`: `playlist '...' (<id>) is visible to <user> (...)`, exit 2 | The playlist is public or shared. As the owner in Jellyfin, open the playlist, remove the shares and make it private. Or delete it; the next run creates it again. `wger_videos.json` has already been written with the item ids and the playlists that passed. |
| `jellyfin`: `ERROR could not delete playlist '<old name>' (<id>) ...`, exit 1 | The new playlist was made and recorded; only the old one is left. Run `jellyfin --apply` again (it retries the delete by exact name), or delete it by hand as its owner in Jellyfin. |
| `jellyfin`: `WARN playlist '<old name>' (<id>) is visible to <user>, so it may not be <login>'s own; not deleted`, exit 1 | A playlist with an old generated name that another account can see (shared, public, or someone else's shared with this person). Check it in Jellyfin; delete it by hand if it is the old tool playlist. |
| `jellyfin`: `ACCESS jmack cannot see the library`, exit 2, nothing written | That plan person has neither *all libraries* nor Exercise ticked. Fix it as in step 4, then run again. A `NOTE other accounts can see the library` line is expected and does not stop the run. |
| `jellyfin`: `library '<day>': n item(s) in name order, not the day's m file(s) in workout order` | Count differs: the folder on the share is incomplete (run `download --apply`) or the scan is not done (run again). `(the right files, wrong order ...)`: Jellyfin named the items by their embedded title. Check the library's *Prefer embedded titles* (off), then **Scan → Replace all metadata** on that library and run again. |
| `jellyfin`: `could not create library ...` + `HAND STEPS` | The API key may not create libraries. Make them by hand as printed ([Day libraries](#day-libraries-for-the-apple-tv-option-b-2026-11-14)), then run again. |
| `download`: `SKIP <day>/<NN> ...: source ... is not on the share`, exit 1 | That video failed to download; the link follows on the run that fetches it. |
| `download`: `WARN day folder '<day>' is not a plain directory` / `OTHER ...` | Something else sits at that path (a symlink, a file, a folder that is not a day name). The tool never touches it; move it out of `/mnt/media/exercise-days` by hand. |
| `WARN .incoming/ holds finished video(s), left alone` | A run stopped after the file was done but before yt-dlp moved it. Move it up: `ssh kdkadmin@10.1.20.20 'mv "/mnt/media/exercise/.incoming/<file>" /mnt/media/exercise/'`, then run step 3 again; it removes the empty folder. |

## Rollback

**Day libraries only** (keeps Exercise, the playlists and the wger links):

1. In the dashboard, delete the 8 day libraries (Dashboard → Libraries → ⋯ → Remove). This
   removes the library, never the files.
2. Revert the day-libraries PR. Merging the revert redeploys jellyfin without the
   `exercise-days` mount (DeployStack if the webhook did not fire). Then drop the
   `libraries` block from `wger_videos.json` (or run `jellyfin --apply` from the reverted
   tool, which ignores it).
3. After that deploy: `ssh kdkadmin@10.1.20.20 'rm -r /mnt/media/exercise-days/*'`. These are
   hardlinks only, so the Exercise files stay. Keep the empty folder, or remove it after the
   deploy.

**Everything:**

1. Revert the merged PR (the compose mount, the tools, and `gen_wger.py` back to YouTube
   links). Merging the revert redeploys jellyfin (DeployStack if the webhook did not fire);
   then run `gen_wger.py --apply`. Remove the directory (step 3) only after that deploy.
2. In the dashboard, delete the Exercise library and the eight playlists.
3. Optional: `ssh kdkadmin@10.1.20.20 'rm -r /mnt/media/exercise'`.
4. Delete the API key in the dashboard and archive `kdk-cluster/jellyfin-api-exercise-videos`.
