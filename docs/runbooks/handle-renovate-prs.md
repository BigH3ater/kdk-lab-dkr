---
title: Handle Renovate PRs
---

# Handle Renovate PRs

Renovate (Mend GitHub App, config `renovate.json`) opens image-update PRs on
weekends. It never merges: **a merge deploys within 15 minutes**, so the merge
is the change window. Policy and grouping: [Komodo GitOps → Dependency
updates](../komodo-gitops.md#dependency-updates-renovate). Dashboard: the
"Dependency Dashboard" issue (#82).

## 1. Read the dashboard

- **Open PRs**: `renovate/dmz-patch`, `renovate/internal-patch`, one PR per minor.
- **Pending approval**: majors and the NUT digest. Tick a box only when you plan
  to merge it this session.
- **Rate-limited**: waits for the weekend window and `prHourlyLimit 2`.
- **Repository problems / lookup failures**: fix the config (example: PR #83
  ignored the `_template` placeholder image).

Cross-check with Grafana → Security → Security Advisories: a `Vulnerable` row
whose fix is in an open Renovate PR goes first.

## 2. Merge order

One PR at a time; wait for its deploy before the next.

1. Anything labelled `security`.
2. `renovate/dmz-patch` (dmz-proxy, identity, the DMZ apps) → check a public host
   and an Authelia login.
3. A traefik / cloudflared / authelia / lldap minor (label `identity`) → same checks.
4. `renovate/internal-patch`, then the other minors.
5. Majors last, each on its own evening, after reading the upstream release notes.

Merge on a quiet evening when the PR touches `home-assistant`,
`scrypted-homebridge`, `proxy`, `dmz-proxy`, `identity`, `komodo`,
`tailscale-subnet-router` or `tailscale-dns`: those blip user-facing paths.

## 3. What a merge recreates

| Change | Effect |
|---|---|
| Any image string (tag or digest) | that container recreates on the next gitops-sync, even if the image ID is the same |
| `deploy = false` stacks (`komodo`, one-shots) | nothing on merge; one-shots use the new tag on their next run; `komodo` needs a manual Deploy |
| Local builds (`kdk/*`) | Renovate skips them; their `FROM` line updates and the image rebuilds on the stack's next deploy |
| A file mounted with `configs:` or a single-file bind | nothing restarts: run `RestartStack <stack>` (or `services=[<svc>]`) |

## 4. Special cases

- **NUT** (`stacks/nut/`): never a version PR. A digest refresh needs a dashboard
  tick and a person watching. Recreating upsd once shut down kdk-hyp-01
  ([NUT](../services/nut.md)).
- **kdk-mon-01** has `auto_pull = false` (Docker Hub rate-limits the Pi). A
  changed tag still pulls because the image is missing; if the pull is denied:
  `ssh kdkadmin@10.1.20.30 'sudo docker pull <image:tag>'`, then Deploy
  `monitoring-ext`.
- **Home Assistant** minors: take an HA backup first (Settings → System →
  Backups); read the breaking-changes section for every minor skipped.
- **Authelia**: take a `pg_dump` on kdk-dkr-dmz-01 before a release with
  storage migrations (see PR #61).
- Old image tags stay on disk until the Sunday `prune-images` Procedure
  (05:10 CT). Execute it by hand if a root disk fills after a big wave.

## 5. Check the deploy

```bash
# the container runs the new tag (on its host)
ssh kdkadmin@<host> 'sudo docker ps --format "{{.Names}} {{.Image}} {{.Status}}" | grep <service>'
# nothing restarting or unhealthy
ssh kdkadmin@<host> 'sudo docker ps --filter health=unhealthy --filter status=restarting --format "{{.Names}}"'   # empty
# the app answers
curl -s -o /dev/null -w '%{http_code}\n' https://<service>.kmkdp.com/
```

Komodo → Updates shows the deploy; gitops-sync runs at `:00/:15/:30/:45`.
Expect `docker-event-watch` "container down (137)" pages for containers
Komodo recreates (SIGKILL after the stop timeout, not a crash) and a quiet
"kdk container watch online" when a watcher itself recreates.

## 6. Baseline known gaps

A Renovate PR that pins an image can close an `image-pin` gap. The
`compose-baseline` Action then passes with a hint that known-gap lines shrank.
Regenerate in a follow-up PR (example: PR #76):

```bash
python3 scripts/compose-baseline-check.py --write-known scripts/compose-baseline-known-gaps.txt
git diff --stat scripts/compose-baseline-known-gaps.txt     # only removed lines
```

A new image in a Renovate PR (a renamed repository) also needs a
`stacks/advisory-watch/catalog.json` entry, or the `advisory-catalog` Action fails.

## Rollback

Revert the merge commit (`git revert -m 1 <sha>`, PR) and let gitops-sync
deploy the old tag. Data migrations (HA, Authelia, databases) may not
downgrade: restore the backup taken before the merge.
