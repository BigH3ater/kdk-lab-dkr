---
title: Post-merge validation
---

# Post-merge validation

After every merge the reviewer proves the change is live and did no harm,
then records the proof on the PR. Builders write the checks into the PR body;
the reviewer runs them. Read-only only: nothing here changes a host.

## 1. Collect the checks

- The PR body's **Validation / After merge / Post-merge** list, plus the owner
  hand steps that had to happen first.
- The "What recreates on merge" table: each recreated stack gets a health check.
- For a security change, one negative check (the thing that should now fail does).

## 2. Wait for the deploy

gitops-sync runs at `:00/:15/:30/:45`. Komodo → Updates shows the run and each
`DeployStack`. A `deploy = false` stack (`komodo`, one-shots) is not deployed by
the loop: confirm the owner deployed it, or that the next scheduled run used
the new version.

## 3. Run the checks from an allowed host

| Target | Run from | Why |
|---|---|---|
| Loki API `10.1.20.21:3100` | kdk-mon-01 or kdk-dkr-01 (`ssh … curl`), or Grafana Explore | nft allows only log senders and Grafana; the laptop times out |
| Alertmanager `10.1.20.30:9093` | kdk-mon-01 (`docker exec alertmanager-ext amtool …`) or `https://alertmanager.kmkdp.com` (SSO) | |
| Komodo API | `https://komodo.kmkdp.com/read` with `X-Api-Key`/`X-Api-Secret`, or `--network container:komodo-core-1` on kdk-dkr-01 | the UI is behind Authelia |
| Container state | `ssh kdkadmin@<host> 'sudo docker ps …'` | |
| Public paths | the laptop (`curl -s -o /dev/null -w '%{http_code}'`) | |

Typical set:

```bash
ssh kdkadmin@<host> 'sudo docker ps --format "{{.Names}} {{.Image}} {{.Status}}" | grep <stack>'      # new tag, Up (healthy)
ssh kdkadmin@<host> 'sudo docker logs <ctr> --since 15m 2>&1 | grep -i -c -E "error|fatal"'            # 0, or explain each
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://<host>.kmkdp.com/                      # expected code
ssh kdkadmin@10.1.20.30 'curl -s http://10.1.20.30:9090/api/v1/alerts' | grep -c '"firing"'            # 0 new
```

Never print a secret to prove it exists: check names (`env | grep -c '^KEY='`),
byte counts (`stat -c %s`) and HTTP codes.

## 4. Post the table on the PR

One comment, headed with the time in UTC:

```markdown
## Post-merge validation (reviewer, 2026-10-28 16:06Z) — COMPLETE

| Check | Result |
|---|---|
| six observability-agent stacks recreated (16:02Z) | every Alloy has its real NODE_NAME ✅ |
| docker streams in Loki by host (last 3 min) | all six hosts present ✅ |
| `.env` on each agent | Komodo-rendered keys kept, sha appended ✅ |
```

- `COMPLETE` only when every row passed; otherwise `deploy OK; owner steps N–M remain`
  and list them.
- A row that fails names the defect and the follow-up PR (example: #67 found
  `host="unknown"`, fixed in #84).
- Owner-only checks (visual, phone, a login) are listed as "owner to confirm".

## 5. Close the loop

- Tick the item in the vault follow-up list
  (`1. Projects/homelab-followups-2026-10/homelab-followups.README.md`) with the date,
  or add a new line for anything left open.
- A lesson that changes how future PRs are written (a header Authelia rejects, a
  `pre_deploy` that overwrote `.env`) goes into the relevant `docs/` page in the
  next PR.

## Lessons from 2026-10-28

| PR | Found after merge | Fix |
|---|---|---|
| #61 | Komodo UI loaded, every API call 401: Traefik ForwardAuth copied the UI's `Authorization: Bearer` header and Authelia accepts only `Basic` there | #69: `authRequestHeaders: Accept,Cookie` on a dedicated middleware |
| #67 | every log stream `host="unknown"`: the agents' `pre_deploy` wrote `> .env` over the Komodo-rendered `NODE_NAME` | #84: append (`>> .env`) |
| #67 | the periphery installer failed in cloud-init without `HOME` | fixed in the cloud-init before merge |
| #85 | `alertmanager.kmkdp.com` 403 after SSO until Authelia restarted | `RestartStack identity services=[authelia]` (configs are not hashed) |
| #86 | Pushover "Open Alertmanager" link pointed at the container id | `--web.external-url=https://alertmanager.kmkdp.com` |
