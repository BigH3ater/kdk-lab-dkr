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

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
# 10.208.200.0/24 must be free (the stack pins it); prints nothing when free
ssh $H 'for n in $(sudo docker network ls -q); do sudo docker network inspect $n --format "{{.Name}} {{range .IPAM.Config}}{{.Subnet}}{{end}}"; done' | grep '10\.208\.200\.'
# Traefik's proxy network must sit inside 10.208.0.0/16 (nginx.conf geo block)
ssh $H 'sudo docker network inspect proxy --format "{{range .IPAM.Config}}{{.Subnet}}{{end}}"'
# memory headroom: the stack's limits add up to about 3.9 GiB (typical use is far lower)
ssh $H 'free -h | head -2'
```

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

## 6. Load the exercise catalog (optional, once)

The Celery jobs sync exercises and images from wger.de weekly at a random time. To have
them now:

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

Then give jmack admin rights (for the A2 exercise generator and the Django admin):

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
generate. Store it without it touching argv or the screen history:

**Laptop (the person's key, pasted at the hidden prompt)**

```bash
WHO=jmack        # then again with WHO=rach
read -rs -p "wger API key for $WHO: " TOKEN; echo
printf '{"title":"wger-api-%s","category":"PASSWORD","fields":[{"id":"password","label":"password","type":"CONCEALED","purpose":"PASSWORD","value":"%s"}]}' "$WHO" "$TOKEN" \
  | op item create --vault kdk-cluster -
unset TOKEN
# verify: the key works against the API (200), and nothing works without it (401/403)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Token $(op read op://kdk-cluster/wger-api-$WHO/password)" https://wger.kmkdp.com/api/v2/userprofile/
curl -s -o /dev/null -w '%{http_code}\n' https://wger.kmkdp.com/api/v2/userprofile/
```

`printf` is a shell builtin, so the key is not in any process's argument list.

## 9. iOS app login (both phones)

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

## 10. Backup check (the morning after)

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
ssh $H 'ls -l --time-style=+%F /mnt/backup/dkr/kdk-dkr-01/pgdump/ | grep -E "wger|jellystat" | tail -4'   # today's wger-*.sql.gz, non-zero
ssh $H 'f=$(ls -t /mnt/backup/dkr/kdk-dkr-01/pgdump/wger-*.sql.gz | head -1); gzip -t "$f" && zcat "$f" | grep -c "^COPY public.manager_workoutlog"'   # gzip OK, 1
```

No Pushover from `backup-verify` that morning means the wger dump passed its check too.

## Rollback

`git revert` the merge commit. Komodo then removes nothing on its own: destroy the
`wger` stack in Komodo (DestroyStack) **after** the revert has reached `backup-dkr-01`
(otherwise the nightly job looks for `wger_backend`). Left behind on kdk-dkr-01:
`/opt/kdk-lab/wger/` (database, media) and the dumps in `pgdump/` (keep them if the
data matters); the `wger-*` 1Password items. The Authelia rule goes with the revert.
