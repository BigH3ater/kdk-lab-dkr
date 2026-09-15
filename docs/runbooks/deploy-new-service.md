---
title: Deploy a new service (Komodo + Traefik + GitOps)
---

# Deploy a new service

Everything here runs from the repo. You add a compose stack, declare it in the
Komodo resource file, route it through Traefik, and push — the GitOps loop
deploys it. You do **not** run `docker compose` on a host by hand.

See [Komodo GitOps](../komodo-gitops.md) for how the sync loop itself works.

## 1. Write the compose stack

Create `stacks/<name>/compose.yaml`. Keep it to community images, pinned by
tag/digest. A web-facing service joins the `proxy` network and carries Traefik
labels; a headless one does not.

```yaml
name: <name>
services:
  app:
    image: ghcr.io/vendor/app:1.2.3
    restart: unless-stopped
    labels:
      traefik.enable: "true"
      traefik.http.routers.<name>.rule: "Host(`<name>.kmkdp.com`)"
      traefik.http.services.<name>.loadbalancer.server.port: "3000"   # container port
    networks: [default, proxy]
networks:
  default: {}
  proxy:
    external: true          # the shared Traefik network (stacks/proxy owns it)
```

Notes:

- The router/service label names (`<name>` in `routers.<name>` /
  `services.<name>`) must be unique across all stacks on that host.
- `server.port` is the **container** port, not a published one — Traefik reaches
  it over the `proxy` network, so you usually publish nothing.
- `*.kmkdp.com` is a wildcard in split-horizon DNS pointing at the proxy, so a
  new `<name>.kmkdp.com` needs **no DNS change**. TLS is the proxy's wildcard
  cert. Public services go on the DMZ host through `stacks/dmz-proxy` instead.

### Secrets

Never commit secrets. Add a `.env.tpl` with `op://` references and let Komodo
render it at deploy time with a `pre_deploy` step (see step 2):

```
API_KEY=op://kdk-cluster/<item>/credential
```

## 2. Declare the stack in Komodo

Add a `[[stack]]` block to `komodo/resources.toml`. This is what the
ResourceSync applies — a stack that isn't here does not exist to Komodo.

```toml
[[stack]]
tags = ["kdk-lab"]          # REQUIRED — the sync only matches tagged resources
name = "<name>"
deploy = true
[stack.config]
server = "kdk-dkr-01"       # target host
repo = "BigH3ater/kdk-lab-dkr"
run_directory = "stacks/<name>"
webhook_enabled = true
# only if the stack needs rendered secrets:
pre_deploy.command = "sh -c '. /etc/komodo/op.env && op inject -f -i .env.tpl -o .env'"
```

`tags = ["kdk-lab"]` is not optional: the sync matches on that tag, and an
untagged resource is invisible to it — the sync then tries to *create* it, hits
a name conflict, and runs "deploy on creation" against the live stack. The
`gitops-reconcile` action tags stragglers automatically, but declare it right.

## 3. Push — the GitOps loop deploys it

```bash
git add stacks/<name>/ komodo/resources.toml
git commit -m "feat(<name>): new service"
git push
```

Komodo Core is LAN-only, so GitHub push webhooks can't reach it. The
`gitops-sync` procedure polls every 15 minutes (`0 */15`): it runs the
`gitops-reconcile` action (tags + `RefreshResourceSyncPending`) and then a
`RunSync` stage that applies the toml and deploys changed stacks. So a push is
live within ~15 minutes with no host access.

To apply immediately, run the `gitops-sync` procedure from the Komodo UI
(`komodo.kmkdp.com`) instead of waiting for the poll.

## 4. Verify

- Komodo UI → the stack shows **Deployed / running**.
- `https://<name>.kmkdp.com` resolves and serves.
- On the host, `sudo docker ps` lists the container `unless-stopped`.
- The container watchdog will page Pushover if it crash-loops — see
  [Add a Pushover alert](pushover-alerts.md).

## Gotchas

- **One-shot stacks** (jobs that exit) — set `destroy_before_deploy = true` and
  gate any dependents with a `busybox` service using
  `depends_on: { <job>: { condition: service_completed_successfully } }`, because
  `DeployStack` returns when a one-shot *starts*, not when it finishes.
- **Compose `$` interpolation** — Compose eats inline `$VAR`. Escape as `$$` for
  literals, or move any non-trivial shell into a mounted `.sh` file.
- **External networks** — if a stack references another stack's network
  (`identity_default`, `immich_internal`), those IDs change on every redeploy of
  the owner; `destroy_before_deploy` clears the stale reference.
