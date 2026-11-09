---
title: SearXNG — first deploy, engines and the limiter
---

# SearXNG: first deploy, engines and the limiter

Bring up `search.kmkdp.com` ([service page](../services/searxng.md)), then the
recurring changes: engines, limiter false positives, the secret. No secret value is
printed: step 1 generates it into a pipe that `op` reads from stdin; the fallback and
the rotation put it in `argv` on the laptop for the moment `op item edit` runs.

Hosts: kdk-dkr-01 = `10.1.20.20`, kdk-dkr-dmz-01 = `192.168.191.20` (user `kdkadmin`).
1Password vault `kdk-cluster`.

## 1. Create the 1Password item (laptop, before merge)

The first deploy's `op inject` fails without it.

**Laptop**

```bash
openssl rand -hex 32 \
  | jq -Rn '{title: "searxng", category: "PASSWORD",
             fields: [{id: "secret", label: "secret", type: "CONCEALED", value: input}]}' \
  | op item create --vault kdk-cluster -
op read op://kdk-cluster/searxng/secret | tr -d '\n' | wc -c     # 64
```

If `op` rejects the template, create the item and set the field from the generator in
two steps (the value then sits in `argv` for the moment the command runs, on the
laptop only):

```bash
op item create --vault kdk-cluster --category password --title searxng >/dev/null
op item edit searxng --vault kdk-cluster "secret[concealed]=$(openssl rand -hex 32)" >/dev/null
op read op://kdk-cluster/searxng/secret | tr -d '\n' | wc -c     # 64
```

## 2. DNS

Nothing to add. `dns-reconcile` writes `search.kmkdp.com → 10.1.20.20` from the
Traefik `Host()` label on its next gitops-sync run after merge; until then the
`*.kmkdp.com` wildcard already points there.

**Laptop (LAN or tailnet)**

```bash
dig +short search.kmkdp.com @10.1.1.53     # 10.1.20.20
```

## 3. Merge and deploy

Merging deploys three stacks: `searxng` (new, kdk-dkr-01), `identity` (the new
Authelia rules; the `kdk.config-sha` label recreates `authelia`, a few seconds of SSO
blip) and `dashboard` (the Search tile; Homepage re-reads `services.yaml`). Run the
`gitops-sync` procedure in Komodo, or wait for the 15-minute poll.

**Laptop → kdk-dkr-01**

```bash
H=kdkadmin@10.1.20.20
ssh $H 'sudo docker ps -a --filter label=com.docker.compose.project=searxng --format "{{.Names}}\t{{.Status}}"'
# searxng, searxng-valkey: "Up ... (healthy)"
ssh $H 'for c in searxng searxng-valkey; do printf "%-16s " $c; sudo docker logs $c 2>&1 | grep -ci "read-only file system\|permission denied"; done'
# both 0. Non-zero: add the path from the log line to that service's tmpfs (or a
# documented kdk.baseline.skip read_only) in a follow-up PR.
ssh $H 'sudo docker logs searxng 2>&1 | grep -iE "valkey|limiter|secret_key|error" | head'
# no "limiter requires Valkey", no "secret_key is not changed"
```

Hardening as deployed:

```bash
ssh $H 'for c in searxng searxng-valkey; do sudo docker inspect $c --format "{{.Name}} user={{.Config.User}} ro={{.HostConfig.ReadonlyRootfs}} drop={{.HostConfig.CapDrop}} add={{.HostConfig.CapAdd}} sec={{.HostConfig.SecurityOpt}} mem={{.HostConfig.Memory}} pids={{.HostConfig.PidsLimit}} tmpfs={{.HostConfig.Tmpfs}}"; done'
# /searxng user=977:977 ro=true drop=[ALL] add=[] sec=[no-new-privileges:true] mem=536870912 pids=256 tmpfs=map[/tmp: /var/cache/searxng:uid=977,gid=977,mode=0755]
# /searxng-valkey user=999:1000 ro=true drop=[ALL] add=[] sec=[no-new-privileges:true] mem=134217728 pids=256 tmpfs=map[/data: /tmp:]
ssh $H 'sudo docker network inspect searxng_backend --format "internal={{.Internal}}"'          # internal=true
ssh $H 'sudo docker exec searxng wget -q -O /dev/null https://duckduckgo.com && echo egress-ok'  # egress-ok
```

Authelia (kdk-dkr-dmz-01) picked up the rules when it restarted after the merge:

**Laptop → kdk-dkr-dmz-01**

```bash
ssh kdkadmin@192.168.191.20 'sudo docker inspect authelia --format "{{.State.StartedAt}} {{.State.Health.Status}}"'
# a start time after the merge, healthy
```

## 4. Validate through Traefik

**Laptop (LAN or tailnet)**

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://search.kmkdp.com/    # 302 https://sso.kmkdp.com/?rd=...
curl -s https://search.kmkdp.com/healthz; echo                                        # OK
curl -s -A 'Mozilla/5.0' https://search.kmkdp.com/opensearch.xml | grep -o 'template="[^"]*search?q=[^"]*"'
# template="https://search.kmkdp.com/search?q={searchTerms}"
```

With an Authelia session (browser, as a `dashboard-users` member):

1. `https://search.kmkdp.com` loads the search page (200 in the dev tools Network tab).
2. Search `wikipedia`: results from several engines (names under each result), no
   error banner except, possibly, a suspended engine.
3. Ten quick searches in a row: none returns `Too Many Requests` (the limiter does not
   block a normal LAN client; its burst limit is 15 per 20 s).
4. `https://search.kmkdp.com/stats`: per-engine response times, few errors.
5. `https://home.kmkdp.com`: the **Search** tile in the Home group has a green dot.

The limiter saw the real client address, not Traefik's:

**Laptop → kdk-dkr-01**

```bash
ssh kdkadmin@10.1.20.20 'sudo docker logs searxng 2>&1 | grep -c "X-Forwarded-For nor X-Real-IP\|missing botdetection.trusted_proxies"'   # 0
```

## 5. Docs to the vault

Run the `publish-docs` procedure in Komodo (it rebuilds docs.kmkdp.com and then runs
`docs-to-vault`), so the service page and this runbook reach `10.Homelab/Reference`.

## Add or remove an engine

See [the service page](../services/searxng.md#add-or-remove-an-engine): one PR editing
`stacks/searxng/config/settings.yml`; the config hash in the pre_deploy recreates the
container on merge.

## Limiter false positive

A person gets `Too Many Requests` during normal use:

1. Find the client and how often: Grafana Explore,
   `{host="kdk-dkr-01", container="traefik"} |= "search.kmkdp.com" | json | RequestHost="search.kmkdp.com" | DownstreamStatus=429`
   (field `ClientHost`).
2. Many different clients at once → the client IP is wrong (trusted_proxies), see
   the service page's troubleshooting table.
3. One device (a browser extension prefetching, a script) → add its address to
   `pass_ip` in `stacks/searxng/config/limiter.toml`, one PR. Each passed request
   logs one WARNING line, so keep the list short.

## Rotate the secret

**Laptop**

```bash
op item edit searxng --vault kdk-cluster "secret[concealed]=$(openssl rand -hex 32)" >/dev/null
op read op://kdk-cluster/searxng/secret | tr -d '\n' | wc -c     # 64
```

(`op item edit` has no stdin form for one field; the value is in `argv` on the laptop
for the moment the command runs.) Then Komodo → Deploy `searxng` (the pre_deploy
re-renders `.env`). Nothing else holds the value.

## Rollback

`git revert` the merge commit, then DestroyStack `searxng` in Komodo (a removed
`[[stack]]` is not torn down by itself). Nothing is left on kdk-dkr-01: no host
directories, no volumes (tmpfs only). The `kdk-cluster/searxng` item stays in
1Password until deleted by hand. The Authelia rules and the Homepage tile go with the
revert (Authelia restarts once more).
