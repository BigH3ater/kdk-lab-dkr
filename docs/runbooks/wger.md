---
title: wger — first deploy and onboarding
---

# wger: first deploy and onboarding

Bring up the wger stack ([service page](../services/wger.md)) and get both people
logging from the iOS app. Steps in order; each block says where it runs and sets its
own variables. No secret value is printed or passed as a command-line argument:
values are generated inside `op`, or travel over stdin.

Hosts: kdk-dkr-01 = `10.1.20.20` (user `kdkadmin`). 1Password vault `kdk-cluster`.

## 0. Before merge: probes (read-only)

**Checked 2026-11-06 09:50 on kdk-dkr-01; skip this step unless the lab changed since:**

| Probe | Result |
|---|---|
| Memory | 48 GB RAM, 39 GB available (the stack's limits add up to about 3.9 GiB) |
| Disk | 432 GB free under `/opt/kdk-lab` |
| `proxy` network | `10.208.15.0/24` (= the `geo` line in `stacks/wger/nginx.conf`) |
| Other kdk-dkr-01 networks noted | `proxy_sockproxy` 10.208.32.0/24, `bridge` 10.208.0.0/24 |
| `10.208.200.0/24` | not used by any docker network (free for `wger_backend`) |

To re-check:

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
# 10.208.200.0/24 must be free (the stack pins it); prints nothing when free
ssh $H 'for n in $(sudo docker network ls -q); do sudo docker network inspect $n --format "{{.Name}} {{range .IPAM.Config}}{{.Subnet}}{{end}}"; done' | grep '10\.208\.200\.'
# Traefik's proxy network must equal the geo line in nginx.conf (10.208.15.0/24)
ssh $H 'sudo docker network inspect proxy --format "{{range .IPAM.Config}}{{.Subnet}}{{end}}"'
# memory headroom: the stack's limits add up to about 3.9 GiB (typical use is far lower)
ssh $H 'free -h | head -2; df -h /opt/kdk-lab | tail -1'
```

If the `proxy` subnet differs, change the `geo` line in `stacks/wger/nginx.conf`.
If 10.208.200.0/24 is taken, change it in `stacks/wger/compose.yaml` (network subnet,
nginx `ipv4_address`, `AUTH_PROXY_TRUSTED_IPS`) and `stacks/wger/nginx.conf` (`geo`)
before merging.

## 1. Create the 1Password items (laptop, before merge)

The first deploy's `op inject` fails without them, and `backup-dkr-01` (which joins
the wger network) fails every night until wger is up, so do this first.

**Laptop**

```bash
V=kdk-cluster
op item create --vault $V --category password --title wger-secret-key        --generate-password=50,letters,digits
op item create --vault $V --category password --title wger-postgres          --generate-password=32,letters,digits
op item create --vault $V --category password --title wger-powersync-storage --generate-password=32,letters,digits
op item create --vault $V --category password --title wger-admin             --generate-password=32,letters,digits
```

The JWT keypair comes from wger's own generator, run in a throw-away container on
kdk-dkr-01 with no network. Its stdout comes back over ssh straight into a 1Password
item template on stdin.

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
IMG=docker.io/wger/server:2.7.0
ssh $H "sudo docker run --rm --network none -e PS_DATABASE_URI=postgres://x:x@localhost:5432/x $IMG python3 manage.py generate-jwt-keys" \
  | python3 -c '
import json, sys
kv = dict(l.strip().split("=", 1) for l in sys.stdin if l.startswith(("JWT_PUBLIC_KEY=", "JWT_PRIVATE_KEY=")))
if set(kv) != {"JWT_PUBLIC_KEY", "JWT_PRIVATE_KEY"}:
    sys.exit("generate-jwt-keys output not recognised; nothing created")
print(json.dumps({"title": "wger-jwt-keys", "category": "PASSWORD", "fields": [
    {"id": "public_key", "label": "public_key", "type": "CONCEALED", "value": kv["JWT_PUBLIC_KEY"]},
    {"id": "private_key", "label": "private_key", "type": "CONCEALED", "value": kv["JWT_PRIVATE_KEY"]}]}))' \
  | op item create --vault kdk-cluster -
```

Verify by length only:

**Laptop**

```bash
for r in wger-secret-key/password wger-postgres/password wger-powersync-storage/password \
         wger-admin/password wger-jwt-keys/public_key wger-jwt-keys/private_key; do
  printf '%-32s %s bytes\n' "$r" "$(op read "op://kdk-cluster/$r" | wc -c)"
done
# expect 51/33/33/33 (value + newline) and several hundred / ~1.6k for the JWKs
```

## 2. DNS

Nothing to add. `dns-reconcile` writes `wger.kmkdp.com → 10.1.20.20` from the Traefik
`Host()` label on its next gitops-sync run after merge; until then the `*.kmkdp.com`
wildcard already points there. Tailnet clients use the same lab DNS.

**Laptop (LAN or tailnet)**

```bash
dig +short wger.kmkdp.com @10.1.1.53     # 10.1.20.20
```

## 3. Merge and deploy

Merging deploys two stacks: `wger` (new, kdk-dkr-01) and `identity` (the new Authelia
access rule; the `kdk.config-sha` label recreates `authelia`, a few seconds of SSO
blip). Watch the wger deploy in Komodo; the first start runs migrations and
collectstatic (the web healthcheck allows 5 minutes).

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
ssh $H 'sudo docker ps -a --filter label=com.docker.compose.project=wger --format "{{.Names}}\t{{.Status}}"'
# 7 rows, all "Up ... (healthy)"; wger-powersync may restart until step 5
ssh $H 'for c in wger-web wger-nginx wger-db wger-cache wger-celery-worker wger-celery-beat wger-powersync; do
  printf "%-20s " $c; sudo docker logs $c 2>&1 | grep -ci "read-only file system"; done'
# all 0. A non-zero service needs the path from its log line added to its tmpfs (or a
# documented kdk.baseline.skip read_only) in a follow-up PR; see the service page.
```

Authelia (kdk-dkr-dmz-01) picked up the new rule when it restarted after the merge:

**Laptop → kdk-dkr-dmz-01**

```bash
ssh kdkadmin@192.168.191.20 'sudo docker inspect authelia --format "{{.State.StartedAt}} {{.State.Health.Status}}"'
# a start time after the merge, healthy. If older: RestartStack identity services=[authelia]
```

## 4. Replace the bootstrap admin password (right after the first start)

On an empty database wger creates `admin` / `adminadmin`, and `/api/` is not behind
Authelia. Set the 1Password value now. The script reads the password from the
container's own environment (`DJANGO_SUPERUSER_PASSWORD`, from `.env`), so it is never
typed or passed as an argument.

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "import os" \
  "from django.contrib.auth.models import User" \
  "u = User.objects.get(username=os.environ['DJANGO_SUPERUSER_USERNAME'])" \
  "u.set_password(os.environ['DJANGO_SUPERUSER_PASSWORD'])" \
  "u.save()" \
  "print('admin password set from env')" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
# the default no longer works (expect anything but 200)
curl -s -o /dev/null -w '%{http_code}\n' -X POST -d username=admin -d password=adminadmin https://wger.kmkdp.com/api/v2/login/
```

## 5. Bootstrap PowerSync storage (once)

Creates the `powersync_storage` role and schema from `PS_STORAGE_PG_URI` (idempotent).

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
ssh $H 'sudo docker exec wger-web python3 manage.py setup-powersync-storage'
ssh $H 'sudo docker restart wger-powersync && sleep 30 && sudo docker inspect wger-powersync --format "{{.State.Health.Status}}"'   # healthy
ssh $H 'sudo docker logs wger-powersync --since 2m 2>&1 | grep -iE "error|replicat" | tail -5'
```

## 6. Load the wger.de exercise database (optional, once)

The weekly exercise sync is off (`SYNC_EXERCISES_CELERY: "False"` in the compose file):
its equipment step re-creates every wger.de equipment row, which would undo step 12.
Load the upstream exercises once by hand; if you ever run `sync-exercises` again, run
step 12 straight after it.

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
ssh $H 'sudo docker exec wger-celery-worker python3 manage.py sync-exercises'
ssh $H 'sudo docker exec wger-celery-worker python3 manage.py download-exercise-images'
ssh $H 'sudo docker exec wger-web python3 manage.py warmup-exercise-api-cache --force'
```

## 7. First web login (each person, browser)

1. Open `https://wger.kmkdp.com` (LAN, or Tailscale on).
2. Authelia asks for username, password and the second factor.
3. wger opens logged in as your Authelia username. The account is created on this first
   visit (`AUTH_PROXY_CREATE_UNKNOWN_USER`).

Then give jmack admin rights (for the catalog generator in step 9 and the Django admin):

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
WHO=jmack
printf '%s\n' \
  "from django.contrib.auth.models import User" \
  "u = User.objects.get(username='$WHO')" \
  "u.is_staff = True" \
  "u.is_superuser = True" \
  "u.save()" \
  "print(u.username, 'superuser', u.is_superuser)" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
```

## 8. API key per person → 1Password

Each person, logged in on the web: user menu → **API key** (`/en/user/api-key`) →
generate. It is a 40-character hex token (`Authorization: Token …`). A long
`eyJ…` value with two dots is the PowerSync/app-auth JWT, not the key: the API
answers `Invalid token` to it. Store the key in the item's `credential` field
without it touching argv or the screen history:

**Laptop (the person's key, pasted at the hidden prompt)**

```bash
WHO=jmack        # then again with WHO=rach
read -rs -p "wger API key for $WHO: " TOKEN; echo
printf '{"title":"wger-api-%s","category":"PASSWORD","fields":[{"id":"password","label":"password","type":"CONCEALED","purpose":"PASSWORD","value":"%s"}]}' "$WHO" "$TOKEN" \
  | op item create --vault kdk-cluster -
unset TOKEN
# verify: the key works against the API (200), and nothing works without it (401/403)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Token $(op read op://kdk-cluster/wger-api-$WHO/credential)" https://wger.kmkdp.com/api/v2/userprofile/
curl -s -o /dev/null -w '%{http_code}\n' https://wger.kmkdp.com/api/v2/userprofile/
```

`printf` is a shell builtin, so the key is not in any process's argument list.

## 9. Load the catalog (our exercises + weekly routines)

`stacks/home-assistant/tools/gen_wger.py` writes the household plan into wger from the
same catalog as the HA helpers (`tools/workout_catalog.json`) and the guide output
(`workout.json` + the posters):

- one custom exercise per slug (57 today: the catalog's 56 + `arm-circles`, which only
  the guide uses), English name, category, equipment and muscles. wger exercises are
  global, so they are written once, with **jmack's** key (superuser from step 7:
  trusted, and exempt from the 20-per-hour exercise-create limit);
- muscles (primary = red, secondary = orange on the app's body map) and equipment come
  from `tools/wger_exercises.json`, one line per slug. Hands, forearms, adductors and shins
  have no built-in wger muscle: the sidecar names five local muscles for them (step 13).
  The app lists those by name and never colours them; loaded forearm work also lights
  Brachialis as secondary;
- the description follows one template: what it works (or stretches), **Dose** as one
  bullet per weekday group (`Mon, Thu, Sat: 2 × 15–30 / leg`, `Every day: …`), **How**
  (the guide's cues), **Safety** (one line). The idempotency marker
  `kdk:<slug> v:<text hash> img:<image hash>#<ids>` is an HTML comment at the end of
  `description_source`: wger strips comments when it renders the description, so it
  never shows in the app or the web UI. A description edited by hand is put back;
- images: every `exercise/ex-<slug>-step<N>.png` in N order (step 1 = start position =
  the main image; photo style, marked AI-generated), or the `ex-<slug>-rom.png` poster
  when the slug has no step photos. The set is re-uploaded only when a file changes,
  and then every other image on that exercise is deleted (images on `kdk:` exercises
  belong to the generator);
- per person, one routine per block of the plan (catalog `routines`; the app's routine
  list is the table of contents, each day card holds one block):

  | jmack | rach |
  |---|---|
  | Daily · walk + stretches (every day: walk + PT stretches) | Daily · desk + evening |
  | Lifts · Mon/Tue/Thu/Fri (Lower A, Upper A, rest, Lower B, Upper B) | Strength · Mon/Thu |
  | PT + hands · Mon/Thu/Sat (PT block + adductor, hands) | Cardio · Tue/Fri |
  | Flow + yoga · Wed/Sat/Sun (flow; Sunday adds yoga) | |

  Every routine is a "fixed weekly schedule" from this week's Monday for 17 weeks
  (wger allows at most 120 days; one window for all of a person's routines), day N =
  weekday N, a weekday the block skips is a rest day. One slot per guide step in the
  guide's order, supersets as one slot with two exercises, sets × reps / seconds /
  minutes / 10-ft lengths from the dose text, weights blank (you fill them in). The
  description ends with `[kdk:<block>]`: the generator finds its routines by that tag
  (the HA treadmill auto-log finds `[kdk:daily]`). A block's name carries its weekdays,
  so a plan change can rename it (wger allows 25 characters; the run stops on a longer one).

It is idempotent: a second run with nothing changed makes no writes. It never deletes
a routine, a slot that has logs (a wger log is deleted with its slot) or a day that has
logs or sessions (a session is deleted with its day), so it is safe to re-run after
people have logged.

**The split (once, 2026-11-07).** Before it each person had one routine, **Week (kdk)**,
with the whole day in one list. The first run after the merge renames that routine into
the person's Daily block (same routine id, same 7 days): the walk and PT-stretch slots
stay where they are, every other slot moves to a new block routine (deleted there,
created in the new one; none of them had logs), and the three (rach: two) other
routines are created. jmack's real logs (the 2026-11-06 treadmill walks, on the Friday
walk slot, and their session) stay on the renamed routine, untouched. A slot that does
have logs and leaves the Daily block would stay at the end of its day as `[retired]`.
The dry run shows it: for jmack `PATCH routine/1/ 'Daily · walk + stretches'
['description', 'name']`, then 3 `POST routine/`; on the live data of 2026-11-07 that
was 546 routine writes for jmack and 79 for rach (plus any exercise image writes).
New routines are created Daily first, then the other blocks last-to-first: the app
lists routines newest first, so the list reads in the order above with Daily at the
bottom, and the app's home-screen card shows Lifts (rach: Strength).

Needs steps 7 and 8 done, Python 3 with `requests` on the laptop, and the guide output.

**Laptop, repo root, as jmack (`op` signed in)**

```bash
G=/tmp/workout-guide
python3 stacks/home-assistant/tools/gen-workout-guide.py --out "$G"   # skip if $G is fresh
ls "$G/workout.json" && ls "$G/exercise" | grep -c -- '-rom\.png$'  # 56 posters
ls "$G/exercise" | grep -c -- '-step[0-9]*\.png$'                   # step photos (0 until they are made)
python3 -c 'import requests' || python3 -m pip install --user requests
export WGER_URL=https://wger.kmkdp.com
export WGER_API_KEY_JMACK="$(op read op://kdk-cluster/wger-api-jmack/credential)"
export WGER_API_KEY_RACH="$(op read op://kdk-cluster/wger-api-rach/credential)"
printf '%s %s bytes\n' JMACK ${#WGER_API_KEY_JMACK} RACH ${#WGER_API_KEY_RACH}   # 40 each; never echo the value
python3 stacks/home-assistant/tools/gen_wger.py --posters "$G"     # dry run: prints every write it would make
unset WGER_API_KEY_JMACK WGER_API_KEY_RACH
```

The keys travel in the environment only; the script reads `WGER_API_KEY_<LOGIN>` and
takes no key on the command line (the curl below reads its header from stdin for the
same reason). The first dry run on an empty wger lists about 1,200 writes (57
exercises + images, seven routines, ~150 slots). On the install loaded before the
template change (2026-11-06) the dry run listed 106 writes for jmack (2026-11-07,
before steps 12 and 13): 57 description PATCHes (new template, marker moved into the
comment) and 49 exercise PATCHes (muscles, equipment). There are no image uploads until
step photos exist. Run step 12 (and step 13) first: until then the dry run warns
`wger has no equipment …` / `wger has no muscle …` and leaves those out.

If the dry run printed an **ADMIN STEP** for the `10-ft lengths` unit (the REST API
cannot create units; a fresh wger has Repetitions, Seconds, Minutes, Miles, Kilometers,
Meters, but no lengths), create it once:

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "from wger.core.models import RepetitionUnit" \
  "print(RepetitionUnit.objects.get_or_create(name='10-ft lengths', defaults={'unit_type': 'DISTANCE', 'multiplier': 3}))" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
# (<RepetitionUnit: 10-ft lengths>, True) the first time, False after
```

Then write it, and check it:

**Laptop, repo root, as jmack (`op` signed in)**

```bash
G=/tmp/workout-guide
export WGER_URL=https://wger.kmkdp.com
export WGER_API_KEY_JMACK="$(op read op://kdk-cluster/wger-api-jmack/credential)"
export WGER_API_KEY_RACH="$(op read op://kdk-cluster/wger-api-rach/credential)"
python3 stacks/home-assistant/tools/gen_wger.py --posters "$G" --apply    # "... write(s) made"; a few minutes
python3 stacks/home-assistant/tools/gen_wger.py --posters "$G" --apply    # "0 write(s) made"
python3 stacks/home-assistant/tools/gen_wger.py --posters "$G" --check    # "wger matches the catalog", exit 0
for who in JMACK RACH; do
  k=WGER_API_KEY_$who
  printf 'Authorization: Token %s\n' "${!k}" | curl -s -H @- "$WGER_URL/api/v2/routine/" \
    | python3 -c 'import json,sys; [print(r["id"], r["name"], r["start"], r["end"], r["fit_in_week"]) for r in json.load(sys.stdin)["results"]]'
done        # JMACK 4 rows, RACH 3; the same Monday + 118 days and True on every row; no "Week (kdk)"
unset WGER_API_KEY_JMACK WGER_API_KEY_RACH
```

The treadmill auto-log looks the walk up by the `[kdk:daily]` tag (before the split: by
the name **Week (kdk)**). Do the `--apply` above in the same sitting as the HA side:
the merge's Komodo deploy copies `config/packages/treadmill_wger.yaml` to kdk-dkr-02,
then HA → Developer Tools → YAML → **Reload all YAML configuration** (or
`script.reload`; no restart). A walk between the deploy and the `--apply` (or between
the `--apply` and the reload) is still logged, only without its routine/slot (the
package's no-routine path). Check once:

```bash
# Laptop -> kdk-dkr-02: the deployed package looks for the tag, not the old name
ssh kdkadmin@10.1.30.21 'sudo grep -c "kdk:daily" /opt/kdk-lab/home-assistant/config/packages/treadmill_wger.yaml'   # 1 or more
```

`--person rach` limits the run to one person's routine (exercises are only written
when jmack is selected). Rachel's routine comes from her own catalog plan
(`plans.rach` in `workout_catalog.json`: desk breaks + evening mobility every day,
Strength A Monday, cardio Tuesday and Friday as one "either one" slot holding the pool
walk and the recumbent bike, Strength B Thursday), not from `workout.json`; her 14
exercises have no posters yet. Until her key exists, run `--person jmack` (that also
writes her exercises, which are global) and leave `WGER_API_KEY_RACH` unset. Warnings go to stderr: a name too close to a wger.de exercise
gets a ` · kdk plan` suffix (11 names today, e.g. Hammer Curl; wger's rule is 0.85 trigram similarity against every English exercise), a slug without a PNG poster gets no image
(`shoulder-press` has only an SVG, `flat-press` none).

Verify in the app, both phones: Routines lists the blocks (jmack: Lifts · Mon/Tue/Thu/Fri,
PT + hands · Mon/Thu/Sat, Flow + yoga · Wed/Sat/Sun, Daily · walk + stretches; rach:
Strength · Mon/Thu, Cardio · Tue/Fri, Daily · desk + evening). Open each: today's card
has the calendar icon, its steps are that block's part of the vault day note, and a
skipped weekday is a Rest day card (Daily: Wednesday and Saturday start with the
25–30 min walk; Flow + yoga: Sunday ends with yoga). Open Daily → Friday: the
2026-11-06 walks are still under Logs. Open one
exercise (e.g. Romanian Deadlift): equipment chip(s) at the top, the image carousel
(step 1 first once step photos exist), the description with **Dose / How / Safety**
and no `kdk:` line, and the body map with the hamstrings + glutes red and the lower
back + trapezius orange.

To change an exercise's muscles or equipment, edit its line in
`tools/wger_exercises.json` (names exactly as wger spells them: the `MUSCLES` tuple in
`gen_wger.py`), run the tests, open a PR, and after the merge re-run the `--apply`
above. A new catalog slug needs a line there too: `--check` reports one that is
missing.

**Renewal.** The routine window is 17 weeks. `--check` fails (exit 1) once the end is
less than 28 days away; `--apply` then moves the start to that week's Monday (logs
stay). Re-run whenever the catalog or the guide changes
([exercise plan update](workout-tracking-update.md), steps 4–5).

## 10. iOS app login (both phones)

On each phone (Tailscale on if not at home):

1. Install **wger** from the App Store.
2. On the login screen choose the self-hosted server option and enter
   `https://wger.kmkdp.com`.
3. Tap the browser login. The system browser opens `wger.kmkdp.com/user/app-auth/`,
   which goes through Authelia (password + second factor).
4. The browser hands back to the app (`wger://app-auth`), logged in as you.
5. Offline test: turn on flight mode, log one set of any exercise, turn flight mode off,
   then check the set on the web (Workout log) within a minute.

Check on the host that both people exist and synced something:

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "from django.contrib.auth.models import User" \
  "from wger.manager.models import WorkoutLog" \
  "for u in User.objects.filter(username__in=['jmack','rach']): print(u.username, WorkoutLog.objects.filter(user=u).count())" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
```

## 11. Backup check (the morning after)

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
ssh $H 'ls -l --time-style=+%F /mnt/backup/dkr/kdk-dkr-01/pgdump/ | grep -E "wger|jellystat" | tail -4'   # today's wger-*.sql.gz, non-zero
ssh $H 'f=$(ls -t /mnt/backup/dkr/kdk-dkr-01/pgdump/wger-*.sql.gz | head -1); gzip -t "$f" && zcat "$f" | grep -c "^COPY public.manager_workoutlog"'   # gzip OK, 1
```

No Pushover from `backup-verify` that morning means the wger dump passed its check too.

## 12. Household equipment list (admin, once; after the merge that turned the sync off)

wger's equipment rows are global and admin-only (the REST API cannot add or delete
them). This trims the list to what the house owns, adds local rows for the props the
plan uses (`Treadmill`, `Finger stretcher`, `Strap`, `Towel`, `Pillow`, `Door frame`,
`Wall`; they show as chips), and rebuilds the exercise API cache. Deleting a row only removes it from the upstream exercises that
listed it (about 160 links for the set below, counted 2026-11-06); no exercise is deleted. It is idempotent.

The lists are the owner's (2026-11-07): keep Dumbbell, Gym mat, none (bodyweight),
Bench and Incline bench (the Bowflex adjustable bench; upstream exercises link to them
too), add the local rows, delete the seven rows nothing here uses. `Wall` is for the
three slugs that cannot be done without one (wall sit, tibialis raise, legs up the wall).

Check first that the sync is off (it would bring the rows back within a week):

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
ssh $H 'sudo docker exec wger-celery-beat printenv SYNC_EXERCISES_CELERY'    # False
```

Then:

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "from wger.exercises.models import Equipment, Exercise" \
  "DROP = ['Barbell', 'Cable machine', 'Kettlebell', 'Pull-up bar', 'SZ-Bar', 'Swiss Ball', 'Resistance band']" \
  "ADD = ['Treadmill', 'Finger stretcher', 'Strap', 'Towel', 'Pillow', 'Door frame', 'Wall']" \
  "for n in ADD: print('add', n, Equipment.objects.get_or_create(name=n)[1])" \
  "for e in Equipment.objects.filter(name__in=DROP): print('delete', e.id, e.name, Exercise.objects.filter(equipment=e).count(), 'links', e.delete()[0])" \
  "print(sorted(Equipment.objects.values_list('name', flat=True)))" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
# first run: "add <name> True" x7, one "delete ..." line per dropped row (7), then the list:
# ['Bench', 'Door frame', 'Dumbbell', 'Finger stretcher', 'Gym mat', 'Incline bench', 'Pillow',
#  'Strap', 'Towel', 'Treadmill', 'Wall', 'none (bodyweight exercise)']
# second run: "add <name> False" x7, no delete lines, same list
printf '%s\n' "from django.core.cache import cache" "cache.clear()" "print('cache cleared')" | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'   # equipment/ and muscle/ lists are cached 28 days per content type; the warmup only rebuilds exercises
ssh $H 'sudo docker exec wger-web python3 manage.py warmup-exercise-api-cache --force'
curl -s 'https://wger.kmkdp.com/api/v2/equipment/?limit=50' | python3 -c 'import json,sys; print(sorted(e["name"] for e in json.load(sys.stdin)["results"]))'
```

Then re-run step 9's `--apply` so the slugs pick up the new rows (the dry run's
`wger has no equipment …` warnings are gone), and pull to refresh the exercise list in
the app (Exercises → filter: only the kept equipment).

To undo: `sudo docker exec wger-celery-worker python3 manage.py sync-exercises` on
kdk-dkr-01 re-creates the wger.de rows with their original ids and re-links the
upstream exercises; delete the local rows in the Django admin
(`/admin/exercises/equipment/`) if they should go too.

### 12b. Equipment for Rachel's plan (admin, once the owner has decided)

Her plan names `Pool` and `Recumbent bike` (local rows, add them) and two built-ins that
step 12 deletes: `Resistance band` (Pallof press, seated band row) and `Swiss Ball`
(optional for wall slides). Keep or re-add a built-in only if the house has one, and then
take it out of step 12's `DROP` list first (step 12 is re-runnable and would delete it
again). Until a row exists, gen_wger warns and leaves it off that exercise.

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "from wger.exercises.models import Equipment" \
  "ADD = ['Pool', 'Recumbent bike']            # + 'Resistance band' / 'Swiss Ball' if bought" \
  "for n in ADD: print('add', n, Equipment.objects.get_or_create(name=n)[1])" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
printf '%s\n' "from django.core.cache import cache" "cache.clear()" "print('cache cleared')" | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'   # equipment/ and muscle/ lists are cached 28 days per content type; the warmup only rebuilds exercises
ssh $H 'sudo docker exec wger-web python3 manage.py warmup-exercise-api-cache --force'
```

Then step 9's `--apply` links them (the `wger has no equipment 'Pool'` warnings are gone).

## 13. Custom muscles (admin, staged; after step 12)

wger's 16 built-in muscles have no hands, forearms, adductors or shins.
`tools/wger_exercises.json` already names five local muscles for those slugs:

| Muscle | `is_front` | Used by |
|---|---|---|
| Forearm flexors | True | wrist curl, towel wring, farmer (secondary), tendon glides, wrist flexor + prayer stretches |
| Forearm extensors | False | reverse wrist curl, towel wring, finger extension, wrist extensor + reverse prayer stretches |
| Hand intrinsics | True | warm-up, tendon glides, finger extension (secondary), thumb opposition |
| Adductors | True | adductor squeeze |
| Tibialis anterior | True | tibialis raise |

Until a row exists, `gen_wger.py` warns `wger has no muscle …` and leaves it out. A
name that is neither on the server nor in that list is a typo and stops the run.

**Limit: a custom muscle is listed by name under the app's body map and never
coloured.** The app draws the map from SVGs bundled per built-in muscle id, so a new id
has no drawing. A missing drawing could also break the map on that exercise. So create
**one** muscle first and check it on a phone before adding the rest.

**13a. One muscle. Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "from wger.exercises.models import Muscle" \
  "print(Muscle.objects.get_or_create(name='Forearm flexors', defaults={'name_en': 'Forearm flexors', 'is_front': True}))" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
# (<Muscle: Forearm flexors>, True) the first time, False after
printf '%s\n' "from django.core.cache import cache" "cache.clear()" "print('cache cleared')" | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'   # equipment/ and muscle/ lists are cached 28 days per content type; the warmup only rebuilds exercises
ssh $H 'sudo docker exec wger-web python3 manage.py warmup-exercise-api-cache --force'
```

Then step 9 with `--person jmack` (dry run, then `--apply`): it links Forearm flexors
to its slugs. On jmack's phone, pull to refresh, then open two exercises:

- **Tendon glides**: the body map still draws (an empty figure), and "Forearm flexors" is listed under the red key.
- **Wrist Curl**: brachialis is still orange on the map, and "Forearm flexors" is listed.

If both are fine, go to 13b. If the map is blank, the page errors, or the app crashes:
delete the row, re-apply, and drop the custom names from the sidecar in a follow-up PR.

**Laptop → kdk-dkr-01 (only if 13a failed)**

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "from wger.exercises.models import Muscle" \
  "print(Muscle.objects.filter(name='Forearm flexors').delete())" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
printf '%s\n' "from django.core.cache import cache" "cache.clear()" "print('cache cleared')" | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'   # equipment/ and muscle/ lists are cached 28 days per content type; the warmup only rebuilds exercises
ssh $H 'sudo docker exec wger-web python3 manage.py warmup-exercise-api-cache --force'
```

**13b. The other four (only after 13a passed). Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "from wger.exercises.models import Muscle" \
  "for n, f in [('Forearm extensors', False), ('Hand intrinsics', True), ('Adductors', True), ('Tibialis anterior', True)]: print(Muscle.objects.get_or_create(name=n, defaults={'name_en': n, 'is_front': f}))" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
printf '%s\n' "from django.core.cache import cache" "cache.clear()" "print('cache cleared')" | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'   # equipment/ and muscle/ lists are cached 28 days per content type; the warmup only rebuilds exercises
ssh $H 'sudo docker exec wger-web python3 manage.py warmup-exercise-api-cache --force'
```

Then run step 9 in full (`--apply`, `--apply` again to see 0 writes, then `--check`). The
`wger has no muscle` warnings should be gone.

## 14. Treadmill auto-log

Home Assistant posts each finished treadmill walk to wger as the person who walked
(`stacks/home-assistant/config/packages/treadmill_wger.yaml`; what it writes is on the
[service page](../services/wger.md#treadmill-auto-log-track-a3)). It needs one line per
person in HA's host-managed `secrets.yaml` on kdk-dkr-02, **before** the package
deploys (a missing `!secret` fails HA's config load). Each line is the whole header
value, `"Token <40-hex key>"`. Until rach has a key, her line holds the placeholder
`"Token none"`: wger answers 403, HA logs a warning and skips her walks.

Hosts: kdk-dkr-02 = `10.1.30.21` (user `kdkadmin`), HA container `homeassistant`,
config `/opt/kdk-lab/home-assistant/config/`. The live `secrets.yaml` is `1001:1001 600`;
`tee -a` keeps the owner.

**14a. Before merge: add both lines (main session, laptop → kdk-dkr-02)**

```bash
H=kdkadmin@10.1.30.21
F=/opt/kdk-lab/home-assistant/config/secrets.yaml
ssh $H "sudo grep -c '^wger_api_key_' $F"          # 0 the first time; if 2, skip this block
{ printf '\n'
  printf 'wger_api_key_jmack: "Token %s"\n' "$(op read op://kdk-cluster/wger-api-jmack/credential | tr -d '[:space:]')"
  printf 'wger_api_key_rach: "Token none"\n'      # placeholder until op://kdk-cluster/wger-api-rach exists
} | ssh $H "sudo tee -a $F >/dev/null && sudo chmod 600 $F"
# verify by length only: 46 = "Token " + 40, and 10 for the placeholder
ssh $H "sudo awk -F'\"' '/^wger_api_key_/{print \$1, length(\$2)}' $F"
```

The `tr` matters: the jmack item's stored value carries one trailing whitespace
character (41 bytes, checked 2026-11-06), and wger refuses a key with it.

**14b. Merge, then check the config and reload (main session, laptop → kdk-dkr-02)**

The Komodo deploy copies the package. `rest_command`, `script` and `automation` are
already loaded, so a reload is enough (no restart).

```bash
H=kdkadmin@10.1.30.21
ssh $H 'ls -l /opt/kdk-lab/home-assistant/config/packages/treadmill_wger.yaml'
ssh $H 'sudo docker exec homeassistant python -m homeassistant --script check_config -c /config 2>&1 | tail -5'
# expect no "Failed config" block; a "Secret wger_api_key_… not defined" means 14a is missing
```

Then in HA: **Developer Tools → YAML → Reload all YAML configuration** (or
`rest_command.reload`, `script.reload`, `automation.reload` from Developer Tools →
Actions). Check that `script.tm_wger_log_walk` and `automation.treadmill_log_walk_to_wger`
exist (Settings → Automations & scenes).

**14c. Verify with a real walk (owner)**

1. Select **Jacob** on the console (or the dashboard), walk at least 2 minutes, stop
   the belt, and wait 2 more minutes.
2. In the wger app (or `https://wger.kmkdp.com` → Workout log): today shows a session
   with the notes `treadmill auto-log: … km, … km/h avg, … min (HH:MM–HH:MM)` and a
   treadmill-walk set with that many minutes.
3. Nothing in wger? HA → Settings → Automations → **Treadmill: log walk to wger** →
   Traces, then the script's trace; a persistent notification names the failure.

The main session can confirm on kdk-dkr-01 without a key:

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "from wger.manager.models import WorkoutSession, WorkoutLog" \
  "for s in WorkoutSession.objects.filter(notes__contains='treadmill auto-log').order_by('-datetime_start')[:3]:" \
  "    print(s.user.username, s.datetime_start, s.datetime_end, repr(s.notes), WorkoutLog.objects.filter(session=s).count())" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
```

To re-log a walk the automation missed (wger was down), run `script.tm_wger_log_walk`
from Developer Tools → Actions with `walker`, `start_ts`, `end_ts` (unix seconds) and
`dist_m`; it writes nothing for a walk that is already there.

**14d. When rach has a key (owner, then main session)**

Owner: step 8 with `WHO=rach` (creates `op://kdk-cluster/wger-api-rach/credential`).
Optionally step 9 with `--person rach` so her walks land on her routine's walk slot;
without a routine they are still logged, just not on a slot.

Main session, replacing the placeholder line (the key goes over stdin only):

**Laptop → kdk-dkr-02**

```bash
H=kdkadmin@10.1.30.21
F=/opt/kdk-lab/home-assistant/config/secrets.yaml
ssh $H "sudo sed -i '/^wger_api_key_rach:/d' $F"
printf 'wger_api_key_rach: "Token %s"\n' "$(op read op://kdk-cluster/wger-api-rach/credential | tr -d '[:space:]')" \
  | ssh $H "sudo tee -a $F >/dev/null && sudo chmod 600 $F"
ssh $H "sudo awk -F'\"' '/^wger_api_key_rach:/{print \$1, length(\$2)}' $F"   # 46
```

Then reload `rest_command` (Developer Tools → Actions → `rest_command.reload`) and let
Rachel do the 14c walk. The same block with `jmack` rotates jmack's key.


## Rollback

The catalog load (step 9) is data, not config: to undo it, delete the generated
routines (description ends `[kdk:…]`) in each person's app (this deletes that person's
logs for them, too) and the
`kdk:` exercises in the Django admin (`/admin/exercises/exercise/`, superuser).

For the stack itself: `git revert` the merge commit. Komodo then removes nothing on its own: destroy the
`wger` stack in Komodo (DestroyStack) **after** the revert has reached `backup-dkr-01`
(otherwise the nightly job looks for `wger_backend`). Left behind on kdk-dkr-01:
`/opt/kdk-lab/wger/` (database, media) and the dumps in `pgdump/` (keep them if the
data matters); the `wger-*` 1Password items. The Authelia rule goes with the revert.
