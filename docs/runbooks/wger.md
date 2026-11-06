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
  from `tools/wger_exercises.json`, one line per slug. Hands, forearms, adductors, shins
  and hip flexors have no wger muscle: those keep an empty map (loaded forearm work
  lights Brachialis as secondary) and the description names the area instead;
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
- per person, the routine **Week (kdk)**: 7 days Monday..Sunday from this week's
  Monday for 17 weeks (wger allows at most 120 days), one slot per guide step in the
  guide's order, supersets as one slot with two exercises, sets × reps / seconds /
  minutes / 10-ft lengths from the dose text, weights blank (you fill them in).

It is idempotent: a second run with nothing changed makes no writes. It never deletes
a routine, a day, or a slot that has logs (a wger log is deleted with its slot), so it
is safe to re-run after people have logged.

Needs steps 7 and 8 done, Python 3 with `requests` on the laptop, and the guide output.

**Laptop, repo root, as jmack (`op` signed in)**

```bash
G=/tmp/workout-guide
python3 stacks/dashboard/guides/gen-workout-guide.py --out "$G"    # skip if $G is fresh
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
same reason). The first dry run lists about 1,200 writes (57
exercises + images, two routines of ~100 slots each). On the install loaded before the
template change (2026-11-06) the dry run lists about 104 writes for jmack: 57
description PATCHes (new template, marker moved into the comment) and ~47 exercise
PATCHes (muscles; treadmill equipment), no image uploads until step photos exist.
Run step 12 first, or `treadmill-walk` warns that wger has no `Treadmill` and goes
without an equipment chip until it exists.

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
  printf 'Authorization: Token %s\n' "${!k}" | curl -s -H @- "$WGER_URL/api/v2/routine/?name=Week%20(kdk)" \
    | python3 -c 'import json,sys; [print(r["id"], r["start"], r["end"], r["fit_in_week"]) for r in json.load(sys.stdin)["results"]]'
done                                                                       # one row each: a Monday, 118 days later, True
unset WGER_API_KEY_JMACK WGER_API_KEY_RACH
```

`--person rach` limits the run to one person's routine (exercises are only written
when jmack is selected). Warnings go to stderr: a name too close to a wger.de exercise
gets a ` · kdk plan` suffix (11 names today, e.g. Hammer Curl; wger's rule is 0.85 trigram similarity against every English exercise), a slug without a PNG poster gets no image
(`shoulder-press` has only an SVG, `flat-press` none).

Verify in the app, both phones: Routines → **Week (kdk)** → today's day shows the
same list as the guide for today; swipe through the week once (Wednesday and Saturday
start with the 25–30 min walk, Sunday ends with yoga + the stretches). Open one
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
([workout tracking update](workout-tracking-update.md), step 6).

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
them). This trims the list to what the house owns, adds `Treadmill`, and rebuilds the
exercise API cache. Deleting a row only removes it from the upstream exercises that
listed it (about 160 links for the set below, counted 2026-11-06); no exercise is deleted. It is idempotent.

The `DROP` and `ADD` lists below are the proposal; confirm them with the owner first
(open questions on the vault note `1. Projects/exercise/wger-exercise-template-2026-11-06`):
keep `Bench` and `Incline bench` only if the adjustable weight bench drawn in ten
posters is real, keep `Resistance band` only if one is owned.

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
  "ADD = ['Treadmill']" \
  "for n in ADD: print('add', n, Equipment.objects.get_or_create(name=n)[1])" \
  "for e in Equipment.objects.filter(name__in=DROP): print('delete', e.id, e.name, Exercise.objects.filter(equipment=e).count(), 'links', e.delete()[0])" \
  "print(sorted(Equipment.objects.values_list('name', flat=True)))" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
# first run: "add Treadmill True", one "delete ..." line per dropped row, then the list:
# ['Bench', 'Dumbbell', 'Gym mat', 'Incline bench', 'Treadmill', 'none (bodyweight exercise)']
# second run: "add Treadmill False", no delete lines, same list
ssh $H 'sudo docker exec wger-web python3 manage.py warmup-exercise-api-cache --force'
curl -s 'https://wger.kmkdp.com/api/v2/equipment/?limit=50' | python3 -c 'import json,sys; print(sorted(e["name"] for e in json.load(sys.stdin)["results"]))'
```

Then re-run step 9's `--apply` so `treadmill-walk` picks up the new row, and pull to
refresh the exercise list in the app (Exercises → filter: only the kept equipment).

To undo: `sudo docker exec wger-celery-worker python3 manage.py sync-exercises` on
kdk-dkr-01 re-creates the wger.de rows with their original ids and re-links the
upstream exercises; delete `Treadmill` in the Django admin
(`/admin/exercises/equipment/`) if it should go too.

## Rollback

The catalog load (step 9) is data, not config: to undo it, delete the **Week (kdk)**
routine in each person's app (this deletes that person's logs for it, too) and the
`kdk:` exercises in the Django admin (`/admin/exercises/exercise/`, superuser).

For the stack itself: `git revert` the merge commit. Komodo then removes nothing on its own: destroy the
`wger` stack in Komodo (DestroyStack) **after** the revert has reached `backup-dkr-01`
(otherwise the nightly job looks for `wger_backend`). Left behind on kdk-dkr-01:
`/opt/kdk-lab/wger/` (database, media) and the dumps in `pgdump/` (keep them if the
data matters); the `wger-*` 1Password items. The Authelia rule goes with the revert.
