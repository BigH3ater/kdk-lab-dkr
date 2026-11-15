---
title: Exercise videos — Jellyfin day libraries, playlists, wger links
updated: 2026-11-15
---

# Exercise videos in Jellyfin

Every video in the exercise plan (`stacks/home-assistant/tools/wger_plan.json`, one
YouTube address per exercise) is downloaded once, as **one file** in
`/mnt/media/exercise` on the media share: 46 files for 47 exercises. `treadmill-walk`
(Treadmill Cardio) has `"video": null` (owner decision 2026-11-14: not needed). It stays in
the plan and in jmack's routines, so the walk is still logged in wger, but it has no file,
no day link, no playlist entry, no mapping entry and no `Video:` line. Only an explicit
`null` means "no video"; a missing or empty `video` is a plan error.

Jellyfin shows the videos as **one library per person and training day** (the *day
libraries*). Each one is a folder of hardlinks to the downloads, numbered in workout
order. Since 2026-11-14 they are the **only** Jellyfin libraries for exercise videos. The
old flat library **Exercise** on `/data/media/exercise` is removed (see
[Removing the old Exercise library](#removing-the-old-exercise-library-2026-11-14)).
Jellyfin no longer mounts the download folder at all.

| jmack | rach |
|---|---|
| Jacob · 1 Mon Lower A, Jacob · 2 Wed Upper A, Jacob · 3 Fri Lower B, Jacob · 4 Sat Upper B (9 videos each) | Rachel · 1 Mon Strength A, Rachel · 2 Tue-Fri Cardio, Rachel · 3 Wed-Sat-Sun Daily parts, Rachel · 4 Thu Strength B (7, 5, 4, 7) |

Each person also has one **private playlist** per day with the same videos, from that
person's own day library, in workout order (`Jacob · 1 Mon Lower A`,
`Rachel · 2 Tue/Fri Cardio`, …). Days with the same list share one library and one
playlist. The number after the person is the place in that person's week, so Jellyfin's
name sort shows week order; with 10 or more the tool zero-pads it (`01`, `02`, …).

**The wger link.** The `Video:` line in each wger exercise description points at one
Jellyfin item: `https://jellyfin.kmkdp.com/web/#/details?id=<item id>`. A wger
description is shared by both people, so there is **one link per exercise**. It is the
exercise's item in the **first** day library that has it: people in plan order (jmack,
then rach), then weekday order. The same hostname works on the LAN, on the tailnet and
through the Cloudflare tunnel. The YouTube address stays in the plan file as the download
source.

**Known limit.** An exercise both people do (today only the dumbbell Romanian deadlift,
`db-rdl`) links to jmack's day library. The link works for Rachel only while she can see
that library. Today she cannot (she has only her own day libraries ticked), so each
`jellyfin` run prints `WARN rach does db-rdl but cannot see library 'Jacob · 1 Mon Lower A' ...`.
Her playlist and her own day library still have the video. To fix the link for her, tick
that library under her Library access.

Nothing here is scheduled and no service is added. After a plan change, run the steps in
[After a plan change](#after-a-plan-change) by hand.

| Piece | What it does |
|---|---|
| `tools/exercise_videos.py download` | yt-dlp, one-shot container on **kdk-dkr-01** (the only host with the media share read-write), into `/mnt/media/exercise/<plan key> [<youtube id>].mp4`, as uid:gid 1000:1000 like the media stack. H.264 + AAC mp4, the largest YouTube has up to 1080 on the **short** side (see [Resolution](#resolution)), the plan's exercise name as the embedded title. Then it keeps the day folders (hardlinks) in `/mnt/media/exercise-days`. `--refetch <key>` replaces a file in place, `--probe` reports each file's resolution |
| `tools/yt-dlp/Dockerfile` | not built; its `FROM` line pins the yt-dlp image (tag + digest), which Renovate updates |
| `tools/exercise_videos.py jellyfin` | creates, scans and checks the 8 day libraries, takes each exercise's item id from them, creates/updates the playlists (owned by the person, not public, no shares), writes `tools/wger_videos.json`. `--remove-main-library` deletes the old Exercise library once |
| `tools/wger_videos.json` | committed, written only by the tool: plan key → item id, the day library it is in, the YouTube id it was made for; the playlists and day libraries the tool manages. `gen_wger.py` builds the `Video:` line from it |
| `stacks/jellyfin/compose.yaml` | mounts `/mnt/media/exercise-days` read-only at `/data/media/exercise-days`. Not `/mnt/media/exercise` |

**Names never change by themselves.** Jellyfin derives an item id from the file path.
The day-link path is `<library>/<NN> <exercise name>.mp4`, so the wger link stays valid as
long as the day, the position and the exercise name stay the same. The download names come
from the plan key and the YouTube id, never the video title. A replaced video (new YouTube
id) is a new download, but its day link keeps its name: `download` re-links it, the item id
stays, and the next `jellyfin --apply` records the new YouTube id. A renamed exercise or a
changed workout order gives a new link name and so a new item id. `jellyfin --apply`
records it and `gen_wger.py --apply` updates the description. `gen_wger.py` refuses to
write while the mapping still holds an id made for another YouTube video.

**Tdarr and Trailarr leave both folders alone, with no config change.** Verified
2026-11-14: Tdarr's two libraries are `/data/media/movies` and `/data/media/tv`. Trailarr
only follows Radarr/Sonarr titles, and its path mappings cover only `tv/` and `movies/`. If
either ever gets a library on `/data/media` itself, add `exercise` and `exercise-days` to
its ignore list.

## Fresh install: order

Use this only to rebuild from nothing. The live setup is already done.

**Merging a PR that adds a mount deploys it.** The jellyfin stack in
`komodo/resources.toml` has `deploy = true` and `webhook_enabled = true`, so a merge
recreates the container right away. The DMZ mount of the share is read-only, so docker
cannot create a missing bind source there, and the deploy fails if
`/mnt/media/exercise-days` does not exist. The steps:

0. Before the merge: the folders exist on the share.
1. Merge (auto-deploy), then check that the container sees `/data/media/exercise-days`.
2. Jellyfin API key → 1Password.
3. Download the videos and make the day folders.
4. Day libraries, item ids, playlists, mapping (`jellyfin --apply`).
5. Load the wger descriptions (`gen_wger.py --apply`).
6. Check it, including the third-account check.

## 0. Before merging: the folders exist

The share is read-write on kdk-dkr-01 only. Create the folders there as kdkadmin (uid
1000, the share's owner and the media stack's PUID). Then check them from the DMZ host,
which is where the bind mount needs them.

**Laptop (SSH agent)**

```bash
ssh kdkadmin@10.1.20.20 'mkdir -p /mnt/media/exercise /mnt/media/exercise-days && ls -ldn /mnt/media/exercise /mnt/media/exercise-days'   # 1000 1000
ssh kdkadmin@192.168.191.20 'ls -ld /mnt/media/exercise-days'                                   # must exist BEFORE the merge
```

Do not merge while the second command answers `No such file or directory`.

## 1. Merge (= deploy), check the mount

Merging deploys the stack. This restarts Jellyfin and drops active streams for about a
minute, so merge in a quiet moment. If the webhook did not fire, run **DeployStack
`jellyfin`** in Komodo; RunSync alone does not redeploy a compose change.

**Laptop (SSH agent)**

```bash
ssh kdkadmin@192.168.191.20 'sudo docker inspect jellyfin --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}" | grep exercise'   # only /mnt/media/exercise-days -> /data/media/exercise-days
ssh kdkadmin@192.168.191.20 'sudo docker exec jellyfin ls -ld /data/media/exercise-days'
```

## 2. Jellyfin API key → 1Password

The tool needs a **dedicated key**, following the per-consumer pattern (see
`wizarr-sync`). Do not reuse `kdk-cluster/jellyfin`.

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

To rotate: create a new key, run `op item edit` on the `credential` field, then delete the
old key in the dashboard. The tool only reads the key when it runs, so nothing else
changes.

## 3. Download the videos and make the day folders (kdk-dkr-01)

Run this from the laptop. The tool reads the plan locally and runs every command on
kdk-dkr-01 over SSH (agent socket only). `sudo -n docker` needs kdkadmin's NOPASSWD.

**Laptop, repo root (SSH agent)**

```bash
T=stacks/home-assistant/tools
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20            # dry run: "46 missing", "would link ..."
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20 --apply    # about 46 one-shot containers, then 59 hardlinks
python3 $T/exercise_videos.py download --host kdkadmin@10.1.20.20            # "46 present, 0 missing"; "links 59 ok, 0 new, 0 re-linked, 0 removed"
ssh kdkadmin@10.1.20.20 'stat -c "%h %i %n" "/mnt/media/exercise/goblet-squat [D1IEI-lfLxU].mp4" "/mnt/media/exercise-days/Jacob · 1 Mon Lower A/01 Dumbbell Goblet Squat.mp4"'   # same inode, link count 2
ssh kdkadmin@192.168.191.20 'sudo docker exec jellyfin ls "/data/media/exercise-days/Jacob · 1 Mon Lower A"'
```

The download skips files that already exist, so a re-run after a failure only fetches what
is missing.

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
you move that file up by hand. A dry run only reports it. The `other file(s)` count does
not include `.incoming`. Do not run two downloads at the same time: one run's cleanup
would delete the other's partial files.

### Day folders

One folder per playlist (grouped the same way, so Rachel's identical days share one), in
`/mnt/media/exercise-days/<name>/`. Each holds **hardlinks** `<NN> <exercise name>.mp4` to
the downloads. They are on the same NFS filesystem (`findmnt`: one `/mnt/media` mount,
verified 2026-11-14), so they take no extra space. NN is the position in the day's workout
order among the exercises that have a video (the treadmill walk has none, so jmack's days
have 9). Names are made file-system safe: `/` becomes `-`, and `\ : * ? " < > | [ ]` are
dropped (`Yoga exercise: Cow-cat` → `05 Yoga exercise Cow-cat.mp4`).

Every `download` run checks the links by inode: a missing link is made, a link to an old
file (a `--refetch` moves a new file over the old name, which breaks the hardlink) is made
again, and a link the day no longer has is removed. The tool only works inside
`/mnt/media/exercise-days`, only on folder names of the generated shape
(`<Person> · <n> ...`) and file names `<NN> <name>.mp4`. Symlinks are never followed.
Anything else there is listed as `OTHER` and never touched.

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
`<key> [<id>].mp4`. The same run then re-links the day folders (the inode changed). The day
link paths stay, so the Jellyfin item ids, the playlists and the wger links stay. A failed
refetch leaves the old file in place and exits 1. Then run `jellyfin --apply` (step 4) so
the day libraries are scanned; `wger_videos.json` stays unchanged.

## 4. Day libraries, item ids, playlists, mapping

**Laptop, repo root (`op` signed in; LAN or tailnet: the Cloudflare WAF blocks the admin
API paths from the internet)**

```bash
T=stacks/home-assistant/tools
export JELLYFIN_API_KEY="$(op read op://kdk-cluster/jellyfin-api-exercise-videos/credential)"
printf '%s bytes\n' ${#JELLYFIN_API_KEY}                   # 32; never echo the value
python3 $T/exercise_videos.py jellyfin                    # dry run: 8 "would: create library", 8 "would: create playlist"
python3 $T/exercise_videos.py jellyfin --apply            # creates + scans the libraries (waits up to 10 min), playlists, wger_videos.json
python3 $T/exercise_videos.py jellyfin --apply            # second run: only the 8 scans, "wger_videos.json unchanged"
unset JELLYFIN_API_KEY
git diff --stat $T/wger_videos.json                       # 46 items (no treadmill-walk), 8 playlists, 8 libraries
```

Commit `wger_videos.json` in a small PR. Then run `gen_wger.py --apply`
([wger runbook step 9](wger.md#9-load-the-plan-built-in-exercises--weekday-routines)) so
every exercise description gets its Jellyfin `Video:` line.

What one run does, in order:

1. **Day libraries.** One Home videos library per day folder, with the same name and the
   folder `/data/media/exercise-days/<name>`. Created with
   `POST /Library/VirtualFolders`. The tool sets the library options:
   - every metadata downloader and image fetcher off;
   - *Prefer embedded titles* **off**, so the item name is the file name
     (`01 Dumbbell Goblet Squat`) and the name sort equals the workout order (the embedded
     title is the plain exercise name and would lose the order);
   - no trickplay, chapter images or real-time monitoring.

   Drifted options are set again. Each library is scanned and checked: its items, sorted
   by name, must be exactly the day's files in workout order (`ok library '<name>': n
   item(s) in workout order`).
2. **Item ids.** Each exercise's id is its item in the first day library that has it
   (people in plan order, then weekday order). If that library does not list the item,
   the exercise is `MISSING`. There is no fallback to a later library, so the link never
   moves between runs. With `--apply`, a `MISSING` exercise stops the run before the
   playlists: `wger_videos.json` keeps its old item ids and records the day libraries.
3. **Playlists.** Each playlist holds the items of that person's own day library, in
   workout order.
4. **`wger_videos.json`**, then the note or the removal of the old Exercise library
   (below).

**Access** stays as it is (owner decision): jmack and the admin have *all libraries*;
rach has her four day libraries ticked. The SSO and LDAP plugin settings are not touched.
Each run prints `NOTE library '<name>' is visible to: ...`. If a plan person cannot see
their own day library, it prints `WARN <login> cannot see library ...`. If a person does
an exercise whose link is in a library they cannot see, it prints
`WARN <login> does <key> but cannot see library ...` (see *Known limit*).

**If the server refuses to create a library** (`could not create library ...: HTTP
401/403`), the run prints `HAND STEPS` and stops. For each library, in **Dashboard →
Libraries → Add Media Library**:

| Field | Value |
|---|---|
| Content type | Home videos and photos |
| Display name | exactly the printed name |
| Folders | `/data/media/exercise-days/<name>` (one folder) |
| Metadata downloaders / image fetchers | all unticked |
| Prefer embedded titles over filenames | ☐ **off** |
| Real-time monitoring, trickplay, chapter images | ☐ |

Then run `jellyfin --apply` again. It finds each library by exact name, type and folder,
records it with origin `hand`, sets drifted options and checks the order.

The tool resolves the users by username (`jmack`, `rach`: the plan's people logins) on
every run, so no user id is stored. It finds a playlist by the id it recorded, or else by
its exact name in that person's list.

**API key limits (Jellyfin 12.4).** An API key has no user behind it. Every
`/Playlists/{id}` call that works on "the calling user" answers `400 Error processing
request.`: read, users, update, remove item and move item. So the tool:

- creates a playlist with `POST /Playlists` and the owner in the body (`UserId`,
  `IsPublic: false`, no `Users`);
- never updates or renames one in place. A recorded or same-named playlist whose items or
  order are wrong is deleted with `DELETE /Items/{id}` and created again. A playlist with
  the old (unnumbered) name is replaced the other way round: the new-named one is created
  and checked first, then the old one is deleted. Either way it gets a new id; nothing
  links to playlist ids except `wger_videos.json`;
- does not set a Sort Name (`ForcedSortName`). `POST /Playlists` has no field for it, and
  the number in the name is enough for the name sort;
- checks each playlist after the write, as the owner and as every other account:
  - `GET /Playlists/{id}/Items?userId=<owner>` gives the day library's items in workout
    order;
  - for each other account (the other plan person and admins included), the playlist is
    not in `GET /Items?userId=<them>&IncludeItemTypes=Playlist`, and
    `GET /Items/{id}?userId=<them>` is a 404.

  If any other account can see a playlist, the run stops with exit 2 and names it.

**What the tool may delete in Jellyfin.**

- A playlist only if it made it: with `--prune`, a recorded playlist whose day left the
  plan; otherwise only a playlist in that person's list with an exact generated name
  (current or old scheme), and only when no other account can see it. A hand-made one
  (`Jacob favourites`, `Jacob · Mon Lower A (copy)`) is never touched. A recorded playlist
  the person renamed by hand is left alone with a `NOTE` and dropped from the record.
- A day library only with `--prune`, and only when its name left the plan,
  `wger_videos.json` records it with origin `api` (the tool created it), and the server's
  library of that exact name still has the recorded id. A hand-made one (origin `hand`) is
  never deleted. `--prune` never deletes Exercise, Movies, Shows or Collections.
- The old library `Exercise` only with `--remove-main-library` (next section).

**A run that stops midway** (a failed check, an HTTP error) after the item ids are known
still writes `wger_videos.json`: all item ids, the day libraries, and only the playlists
checked so far. `gen_wger.py` can run on it, and the next `jellyfin --apply` resumes.

### Migration from the unnumbered playlist names (2026-11-14)

Done on the live server. The first `jellyfin --apply` after that change found the eight
old playlists (`Jacob · Mon Lower A`, …) by recorded id or exact old name, created and
checked each new one, then deleted the old one. A failed delete prints `ERROR could not
delete playlist ...`, marks that `did:` line `-- FAILED`, and exits 1; the next run finds
the leftover by its exact old name and tries again.

## Removing the old Exercise library (2026-11-14)

Until this change the wger links and the playlists used the items of a flat library
**Exercise** (id `3c29806b1097f5e2b21f4cec073309f1`) on `/data/media/exercise`. The owner
wants it gone. The PR that makes the day libraries the only source does three things:

- `exercise_videos.py jellyfin` takes every item id from the day libraries and never
  scans or needs Exercise. Without a flag it only prints
  `NOTE the old library 'Exercise' (...) still exists; nothing links to it any more`.
- `--remove-main-library` deletes it, but only when it is the one library named exactly
  `Exercise`, its single folder is `/data/media/exercise`, and every plan exercise with a
  video got a day-library item in the same run. Otherwise it prints
  `ERROR not removing library 'Exercise': ...` and deletes nothing. A dry run prints
  `would: delete library 'Exercise' (...)`.
- `stacks/jellyfin/compose.yaml` no longer mounts `/mnt/media/exercise`.

**The files stay.** `DELETE /Library/VirtualFolders?name=Exercise` (Jellyfin's
`LibraryManager.RemoveVirtualFolder`) deletes only the library's own folder in Jellyfin's
config, `/config/data/root/default/Exercise`. Read on the live host 2026-11-14, that folder
holds `exercise.mblink` (a text file with the path `/data/media/exercise`),
`homevideos.collection`, `options.xml` and `folder.png` (the library's cover image). The
media folder is never opened. The downloads in `/mnt/media/exercise` stay, and the day
links that point at them stay. Besides, the share is mounted read-only on the DMZ host,
and after this merge Jellyfin does not mount the folder at all.

**Merging restarts Jellyfin** (about a minute; active streams drop). Until the steps below
are run:

- the Exercise library still shows, but empty (its folder is no longer mounted);
- the wger `Video:` links still point at Exercise items, so they open nothing;
- the playlists still hold Exercise items, so they show empty.

The day libraries keep working the whole time. Run the steps soon after the merge.

### Steps after the merge

Run them in this order. Stop at the first step that does not show what is expected.

**Laptop, repo root (SSH agent; `op` signed in; LAN or tailnet)**

```bash
T=stacks/home-assistant/tools
# 0. the old mount is gone, the day folders are still there
ssh kdkadmin@192.168.191.20 'sudo docker inspect jellyfin --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}" | grep exercise'   # only /mnt/media/exercise-days -> /data/media/exercise-days

export JELLYFIN_API_KEY="$(op read op://kdk-cluster/jellyfin-api-exercise-videos/credential)"
printf '%s bytes\n' ${#JELLYFIN_API_KEY}                                    # 32

# 1. dry run: 8 "ok library", no MISSING, 8 playlists to re-create,
#    "would: delete library 'Exercise' (3c29806b1097f5e2b21f4cec073309f1 on ['/data/media/exercise']; ...)"
python3 $T/exercise_videos.py jellyfin --remove-main-library

# 2. apply: re-creates the 8 playlists from the day libraries, deletes Exercise, writes wger_videos.json
python3 $T/exercise_videos.py jellyfin --apply --remove-main-library        # "deleted library 'Exercise'", "wrote wger_videos.json: 46 item id(s), 8 playlist(s), 8 day librar(ies)"

# 3. second run: only the 8 scans, no NOTE about Exercise
python3 $T/exercise_videos.py jellyfin --apply                              # "wger_videos.json unchanged"
unset JELLYFIN_API_KEY

# 4. commit the mapping (small PR, merge it)
git diff --stat $T/wger_videos.json                                         # 46 items, each with "library"

# 5. wger: dry run, apply, check
export WGER_URL=https://wger.kmkdp.com
export WGER_API_KEY_JMACK="$(op read op://kdk-cluster/wger-api-jmack/credential)"
export WGER_API_KEY_RACH="$(op read op://kdk-cluster/wger-api-rach/credential)"
printf '%s %s bytes\n' JMACK ${#WGER_API_KEY_JMACK} RACH ${#WGER_API_KEY_RACH}   # 40 each
python3 $T/gen_wger.py                  # dry run: about 46 "description (How / Safety / Video)" writes, nothing else
python3 $T/gen_wger.py --apply
python3 $T/gen_wger.py --check          # "wger matches the plan", exit 0
unset WGER_URL WGER_API_KEY_JMACK WGER_API_KEY_RACH
```

Then check by hand:

- **Dashboard → Libraries**: no Exercise; the 8 day libraries are there.
- `ssh kdkadmin@10.1.20.20 'ls /mnt/media/exercise | wc -l'` still says 46.
- In wger, tap two `Video:` addresses (one of jmack's exercises, one of Rachel's). Each
  opens the video's page in a day library after the Jellyfin login.
- Each person's playlists show their videos in workout order.

**If the key may not delete the library** (`could not delete library 'Exercise': HTTP
401/403`), the run prints `HAND STEPS` and exits 1. Everything else is done and recorded.
In Jellyfin as an admin: **Dashboard → Libraries → Libraries → Exercise → ⋯ → Remove**.
This removes the library only; the files stay. Then go on with step 3.

## 5. Load the wger descriptions

See [wger runbook step 9](wger.md#9-load-the-plan-built-in-exercises--weekday-routines).
`gen_wger.py` refuses `--apply` (exit 2, nothing written) and fails `--check` while any
plan exercise with a video lacks an item id for its current YouTube video. A dry run warns
and leaves those descriptions alone. It never writes a YouTube or empty `Video:` line.

## 6. Check it

- jmack's Jellyfin home shows his four day libraries and four playlists of 9 videos each,
  in workout order (the first lift first, core last; the treadmill walk has no video).
  Rachel sees her four of each.
- On the Apple TV, as jmack and as rach: each day library shows its videos in workout order
  (`01 …` first), and a video plays.
- Tap a `Video:` address in wger on the phone, once on Wi-Fi and once on cellular (through
  the tunnel). It opens the video in the browser after the Jellyfin login.
- **Third account (required):** open a private window, sign in as an account that is
  neither jmack nor rach (a Wizarr invitee or a test account), first with the password
  form and then once with **Sign in with SSO**. Do it on the LAN and through the tunnel
  (phone on cellular). Then check:
  1. Home shows the day libraries only if that account has *all libraries* (expected
     while access is left as it is).
  2. *Playlists* lists none of the Jacob/Rachel playlists, and searching for `Jacob` or
     `Rachel` finds no playlist.
  3. Paste a playlist address of jmack's (`/web/#/details?id=<playlist id>` from
     `wger_videos.json`). The page shows an error or nothing, not the playlist.

## After a plan change

Use this when an exercise was added, a video replaced or removed (`"video": null`), or a
routine changed:

1. Edit `wger_plan.json`, test, PR, merge ([exercise plan update](workout-tracking-update.md)).
2. Run `download` (step 3). It fetches the new files and lists the old ones as `EXTRA`.
   The same run brings the day folders up to date: new and re-linked links, and removed
   links for exercises a day no longer has. A whole folder whose day left the plan is
   listed as `EXTRA day folder`.
3. Run `jellyfin --apply` (step 4), then commit `wger_videos.json`. This creates and checks
   any new day library and records the new item ids.
4. Run `gen_wger.py --apply` and then `--check`.
5. Once everything works, delete the leftovers, `jellyfin` first:
   - `jellyfin --apply --prune` deletes the playlists the tool made whose day left the
     plan, and the day libraries it created (origin `api`) whose name left the plan.
   - `download --apply --prune` removes downloads that match the `<key> [<id>].mp4`
     pattern and are no longer in the plan (any other file in the folder is reported and
     never touched). It also empties and removes day folders that left the plan; a folder
     that still holds a foreign file keeps it, and the run exits 1.

An exercise with a video that **no day uses** has no day-library item, so it has no item
id and no `Video:` line. `jellyfin` prints `WARN <key> has a video but no day uses it`,
and `gen_wger.py --check` fails on it. Use it in a routine, or set its `video` to `null`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `retry ... HTTP Error 403: Forbidden`, then `FAILED <file> after 4 attempts` | YouTube refuses the video data now and then; the same video usually works a few minutes later. The rest of the run still downloads. Re-run step 3 (`--apply`) later; it fetches only the listed files. If the same video fails across several runs, open it in a browser: it may be private, removed or region-locked. Replace it in `wger_plan.json` (see *After a plan change*). |
| A video plays at low resolution (480p or less), or `--probe` prints `LOW` | Run the format check in [Resolution](#resolution) for its YouTube id. Larger available: `download --refetch <key> --apply`, then `jellyfin --apply`. Same size: YouTube has nothing better; keep it (the seven 720-only videos listed there) or replace the video in the plan. If Jellyfin still shows the old size after the scan, open the item → ⋯ → **Refresh metadata** (*Replace all metadata*). |
| `--refetch`: `FAILED <file>: .incoming/<file> already exists` | An earlier refetch finished but was not moved. Check that file (`ls -la /mnt/media/exercise/.incoming` on kdk-dkr-01), delete it or move it over the old one by hand, then run again. |
| `ERROR n of 46 video(s) not created`, a file marked `yt-dlp exited 0 but the file is not there` | yt-dlp wrote it under a different name or not at all. Check the run's output for that file and `ls -la /mnt/media/exercise/.incoming` on kdk-dkr-01. |
| `jellyfin`: `MISSING <key>: <library>/<NN> <name>.mp4 is not an item in that day library`, exit 2 | The day link is not on the share (run `download --apply`) or the scan has not finished (run `jellyfin --apply` again). `wger_videos.json` keeps its old item ids. |
| `jellyfin`: `WARN <key> has a video but no day uses it` | See the end of *After a plan change*. |
| `jellyfin`: `WARN rach does db-rdl but cannot see library 'Jacob · …'` | Expected (*Known limit*). Tick that library under rach's Library access if her wger link should work. |
| `jellyfin`: `ERROR not removing library 'Exercise': ...`, exit 1 | `--remove-main-library` refused: its folder is not exactly `/data/media/exercise`, there are two libraries of that name, or an exercise has no day-library item. Nothing was deleted. Fix the cause (or remove it by hand in the dashboard if it really is the old library), then run again. |
| `jellyfin`: `could not delete library 'Exercise'` + `HAND STEPS` | The key may not delete libraries. Remove it in the dashboard as printed; the files stay. |
| `jellyfin`: `ERROR GET /Playlists/<id>: HTTP 400 Error processing request.` | An older tool version. With an API key that call has no user and always fails; the current tool never calls it. Update the checkout and run again. |
| `jellyfin`: `playlist '...' (<id>) is visible to <user> (...)`, exit 2 | The playlist is public or shared. As the owner in Jellyfin, open the playlist, remove the shares and make it private. Or delete it; the next run creates it again. `wger_videos.json` has already been written with the item ids and the playlists that passed. |
| `jellyfin`: `ERROR could not delete playlist '<old name>' (<id>) ...`, exit 1 | The new playlist was made and recorded; only the old one is left. Run `jellyfin --apply` again (it retries the delete by exact name), or delete it by hand as its owner in Jellyfin. |
| `jellyfin`: `WARN playlist '<old name>' (<id>) is visible to <user>, so it may not be <login>'s own; not deleted`, exit 1 | A playlist with an old generated name that another account can see (shared, public, or someone else's shared with this person). Check it in Jellyfin; delete it by hand if it is the old tool playlist. |
| `jellyfin`: `library '<day>': n item(s) in name order, not the day's m file(s) in workout order` | Count differs: the folder on the share is incomplete (run `download --apply`) or the scan is not done (run again). `(the right files, wrong order ...)`: Jellyfin named the items by their embedded title. Check the library's *Prefer embedded titles* (off), then **Scan → Replace all metadata** on that library and run again. |
| `jellyfin`: `could not create library ...` + `HAND STEPS` | The API key may not create libraries. Make them by hand as printed (step 4), then run again. |
| `download`: `SKIP <day>/<NN> ...: source ... is not on the share`, exit 1 | That video failed to download; the link follows on the run that fetches it. |
| `download`: `WARN day folder '<day>' is not a plain directory` / `OTHER ...` | Something else sits at that path (a symlink, a file, a folder that is not a day name). The tool never touches it; move it out of `/mnt/media/exercise-days` by hand. |
| `WARN .incoming/ holds finished video(s), left alone` | A run stopped after the file was done but before yt-dlp moved it. Move it up: `ssh kdkadmin@10.1.20.20 'mv "/mnt/media/exercise/.incoming/<file>" /mnt/media/exercise/'`, then run step 3 again; it removes the empty folder. |

## Rollback

**Bring the old Exercise library back** (undo the 2026-11-14 removal):

1. Revert the removal PR. Merging the revert restarts Jellyfin with the
   `/mnt/media/exercise` mount again (DeployStack if the webhook did not fire).
2. In the dashboard, **Add Media Library**: Home videos and photos, name `Exercise`,
   folder `/data/media/exercise`, every metadata downloader and image fetcher off,
   *Prefer embedded titles* on. It gets a new id.
3. Run `jellyfin --apply` from the reverted tool. It scans Exercise, re-creates the
   playlists from its items and writes the old-style `wger_videos.json`. Commit it, then
   `gen_wger.py --apply`. The day libraries keep working throughout.

**Day libraries only** (needs the Exercise library first, see above; the tool now takes
every item id from the day libraries):

1. In the dashboard, delete the 8 day libraries (Dashboard → Libraries → ⋯ → Remove). This
   removes the library, never the files.
2. Revert the day-libraries PR. Merging the revert redeploys jellyfin without the
   `exercise-days` mount.
3. After that deploy: `ssh kdkadmin@10.1.20.20 'rm -r /mnt/media/exercise-days/*'`. These are
   hardlinks only, so the downloads stay.

**Everything:**

1. Revert the merged PRs (the compose mounts, the tools, and `gen_wger.py` back to YouTube
   links). Merging the revert redeploys jellyfin (DeployStack if the webhook did not fire);
   then run `gen_wger.py --apply`.
2. In the dashboard, delete the day libraries (and Exercise, if it is back) and the eight
   playlists.
3. Optional, after that deploy: `ssh kdkadmin@10.1.20.20 'rm -r /mnt/media/exercise-days /mnt/media/exercise'`.
4. Delete the API key in the dashboard and archive `kdk-cluster/jellyfin-api-exercise-videos`.
