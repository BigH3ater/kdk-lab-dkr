---
title: wger — first deploy and onboarding
updated: 2026-11-14
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

Then give jmack admin rights (for the plan generator in step 9 and the Django admin):

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

## 9. Load the plan (built-in exercises + weekday routines)

`stacks/home-assistant/tools/gen_wger.py` writes the household plan from
`tools/wger_plan.json` into wger ([exercise plan update](workout-tracking-update.md) has the
file format). Since 2026-11-14 it uses wger's **built-in** exercises only: no custom
exercises, no images, no custom muscles.

- **Exercises:** each plan entry names a built-in by uuid plus the English name wger must
  report for it; the run stops before any write when a uuid is missing, is a `kdk-lab`
  exercise, or has another name (several built-ins share a name, e.g. two "Side Plank").
  The English description is replaced by **How** (3-5 lines), **Safety** (one line) and
  `Video: <address>` (plain text: wger has no link field; the video's Jellyfin page from
  `wger_videos.json`, see [exercise-videos](exercise-videos.md): `--apply` stops while an
  exercise has no item id there), ending in a hidden
  `<!-- kdk-plan v:<hash> -->` comment. Plan `equipment` names are added, never removed
  (Treadmill on Treadmill Cardio, Resistance band on Rachel's two band moves once step 12
  has the row). Exercises are global: they are written with **jmack's** key (superuser,
  step 7) and only when jmack is selected. A description is shared by everyone who uses
  that exercise; person-specific notes go in the slot comment.
- **Routines**, one per plan weekday, one day each, `fit_in_week` off (the day repeats on
  every date, so the app always shows it), starts staggered a day apart with one shared end
  16 weeks after this week's Monday, so the list reads Mon..Sun. Tag `[kdk:mon]` ..
  `[kdk:sun]` at the end of the description:

  | jmack (Mon/Wed/Fri/Sat; nothing on the other days) | rach (every day) |
  |---|---|
  | Mon · Lower A, Wed · Upper A, Fri · Lower B, Sat · Upper B | Mon · Strength A, Tue · Cardio, Wed · Daily parts, Thu · Strength B, Fri · Cardio, Sat · Daily parts, Sun · Daily parts |

  Each slot carries sets, reps (or seconds / minutes / 10-ft lengths) and max, the
  planned start weight (lb, `weight-config`) and the rest range (`rest-config` /
  `max-rest-config`: gym mode's timer counts it down). A per-side slot doubles the sets,
  one logged set per side (odd = left, even = right). No templates any more.
- **Never deletes a routine.** Slots are matched by exercise; a slot with logs is moved to
  the end as `[retired]`. Old kdk routines not in the plan are listed as a warning for
  `--purge-kdk` (step 9b).

Idempotent: a second `--apply` makes no writes. Needs steps 7 and 8 and Python 3 with
`requests`.

**Laptop, repo root, as jmack (`op` signed in)**

```bash
T=stacks/home-assistant/tools
python3 -c 'import requests' || python3 -m pip install --user requests
python3 $T/gen_wger.py --check-videos        # "46/46 video(s) answer oEmbed" (+ "none treadmill-walk"); no keys needed
export WGER_URL=https://wger.kmkdp.com
export WGER_API_KEY_JMACK="$(op read op://kdk-cluster/wger-api-jmack/credential)"
export WGER_API_KEY_RACH="$(op read op://kdk-cluster/wger-api-rach/credential)"
printf '%s %s bytes\n' JMACK ${#WGER_API_KEY_JMACK} RACH ${#WGER_API_KEY_RACH}   # 40 each; never echo the value
python3 $T/gen_wger.py              # dry run: every write it would make
python3 $T/gen_wger.py --apply      # "... write(s) made"
python3 $T/gen_wger.py --apply      # "0 write(s) made"
python3 $T/gen_wger.py --check      # "wger matches the plan", exit 0
unset WGER_API_KEY_JMACK WGER_API_KEY_RACH
```

Without keys the dry run still checks every exercise (the exercise endpoints are public)
and skips the routines with a warning; `--apply`, `--check` and `--purge-kdk` need both
keys (or `--person <login>` for one; exercise writes need jmack).

If the dry run printed an **ADMIN STEP** for the `10-ft lengths` unit (REST cannot create
units), run the printed `manage.py shell` one-liner on kdk-dkr-01 and re-run.

Verify on both phones (pull to refresh): jmack's list reads Mon · Lower A, Wed · Upper A,
Fri · Lower B, Sat · Upper B; gym mode pre-fills weight and reps and counts the rest down;
an exercise page shows How / Safety / `Video:` and no `kdk-plan` line. Rachel's seven
routines open with the built-in exercises and their wger images.

**Renewal.** `--check` fails (exit 1) once the window ends within 28 days; `--apply` then
moves it to this week's Monday (logs stay).

### 9b. Purge the old kdk data (once, owner present)

`--purge-kdk` deletes every routine and template tagged `[kdk:…]` that is not one of the
plan's routines (**logs and sessions included**: owner-approved 2026-11-14, no export), then
every exercise whose `license_author` is exactly `kdk-lab` (their images, translations, slot
entries and logs cascade, including old slots inside the kept plan routines). It never
touches a plan routine, an untagged routine or a built-in, refuses to run if a plan uuid is
a `kdk-lab` exercise, and is a dry run unless `--apply` is given. The previous night's
`wger-YYYY-MM-DD.sql.gz` ([[backups]]) is the fallback.

**Laptop, repo root, as jmack (`op` signed in; keys exported as above)**

```bash
T=stacks/home-assistant/tools
python3 $T/gen_wger.py --purge-kdk            # per-routine lines + "SUMMARY: N routine(s)/template(s) with L log(s) ... M kdk-lab exercise(s)"
# owner confirms the counts, then:
python3 $T/gen_wger.py --purge-kdk --apply    # ends "clean: 0 kdk routines ..." (exit 0) or "NOT CLEAN" (exit 1)
```

The API cannot see an exercise without a translation, so the run always prints a
`manage.py shell` **FALLBACK** that deletes every `license_author='kdk-lab'` exercise
directly; run it once after the `--apply` (it prints the count it deleted; `(0, {})` when
the API already got them all). Then step 9's `--apply` and `--check`.

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

## 12. Household equipment list (admin; after the merge that turned the sync off)

wger's equipment rows are global and admin-only (the REST API cannot add or delete them).
The list is trimmed to what the house owns. Since the built-in plan (2026-11-14) that is
Bench, Dumbbell, Gym mat, Incline bench (the Bowflex adjustable bench),
none (bodyweight), Treadmill and Resistance band (Rachel's Pallof press and seated row).
The local prop rows of the old plan (Finger stretcher, Strap, Towel, Pillow, Door frame,
Wall, Pool, Recumbent bike) go. Deleting a row only removes it from the exercises that list
it; no exercise is deleted. Idempotent.

Check first that the sync is off (it would bring the rows back within a week):

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
ssh $H 'sudo docker exec wger-celery-beat printenv SYNC_EXERCISES_CELERY'    # False
printf '%s\n' \
  "from wger.exercises.models import Equipment, Exercise" \
  "DROP = ['Barbell', 'Cable machine', 'Kettlebell', 'Pull-up bar', 'SZ-Bar', 'Swiss Ball', 'Finger stretcher', 'Strap', 'Towel', 'Pillow', 'Door frame', 'Wall', 'Pool', 'Recumbent bike']" \
  "ADD = ['Treadmill', 'Resistance band']" \
  "for n in ADD: print('add', n, Equipment.objects.get_or_create(name=n)[1])" \
  "for e in Equipment.objects.filter(name__in=DROP): print('delete', e.id, e.name, Exercise.objects.filter(equipment=e).count(), 'links', e.delete()[0])" \
  "print(sorted(Equipment.objects.values_list('name', flat=True)))" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
# want: ['Bench', 'Dumbbell', 'Gym mat', 'Incline bench', 'Resistance band', 'Treadmill', 'none (bodyweight exercise)']
printf '%s\n' "from django.core.cache import cache" "cache.clear()" "print('cache cleared')" | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'   # equipment/ and muscle/ lists are cached 28 days per content type; the warmup only rebuilds exercises
ssh $H 'sudo docker exec wger-web python3 manage.py warmup-exercise-api-cache --force'
curl -s 'https://wger.kmkdp.com/api/v2/equipment/?limit=50' | python3 -c 'import json,sys; print(sorted(e["name"] for e in json.load(sys.stdin)["results"]))'
```

Then re-run step 9's `--apply`: the `wger has no equipment 'Resistance band'` warning is
gone and the row is linked to the two band exercises.

To undo: `sudo docker exec wger-celery-worker python3 manage.py sync-exercises` on
kdk-dkr-01 re-creates the wger.de rows with their original ids and re-links the upstream
exercises.

## 13. Custom muscles (retired 2026-11-14)

The old plan's five local muscles (Forearm flexors, Forearm extensors, Hand intrinsics,
Adductors, Tibialis anterior; ids 17-21) were never linked after 2026-11-09 (the iOS app
errors on muscle ids above 16) and the built-in plan does not use them. Delete them once,
after step 9b:

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
printf '%s\n' \
  "from wger.exercises.models import Muscle" \
  "qs = Muscle.objects.filter(id__gt=16, name__in=['Forearm flexors', 'Forearm extensors', 'Hand intrinsics', 'Adductors', 'Tibialis anterior'])" \
  "print(list(qs.values_list('id', 'name'))); print(qs.delete())" \
  | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
printf '%s\n' "from django.core.cache import cache" "cache.clear()" "print('cache cleared')" | ssh $H 'sudo docker exec -i wger-web python3 manage.py shell'
ssh $H 'sudo docker exec wger-web python3 manage.py warmup-exercise-api-cache --force'
```

## 14. Treadmill auto-log (retired)

Retired 2026-11-08; walks are logged in the app. Home Assistant no longer posts
treadmill walks to wger (the owner logged the walk by hand too, so it was counted
twice). The `wger-api-*` keys stay: `gen_wger.py` uses them.


## Rollback

The plan load (step 9) is data, not config: revert the plan PR and re-run step 9's
`--apply` (slots with logs stay as `[retired]`). The exercise descriptions are wger's
upstream text otherwise; restore one from the nightly `wger-*.sql.gz` or a manual
`sync-exercises` (that also re-runs the equipment sync: redo step 12 after it). The purge
(step 9b) has no undo but the nightly dump.

For the stack itself: `git revert` the merge commit. Komodo then removes nothing on its own: destroy the
`wger` stack in Komodo (DestroyStack) **after** the revert has reached `backup-dkr-01`
(otherwise the nightly job looks for `wger_backend`). Left behind on kdk-dkr-01:
`/opt/kdk-lab/wger/` (database, media) and the dumps in `pgdump/` (keep them if the
data matters); the `wger-*` 1Password items. The Authelia rule goes with the revert.
