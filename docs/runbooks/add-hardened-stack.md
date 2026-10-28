---
title: Add a hardened stack
---

# Add a hardened stack

The checklist for a new stack that meets the
[compose hardening baseline](../reference/compose-baseline.md) from its first
commit. The general deploy flow (Traefik labels, GitOps loop) is in
[Deploy a new service](deploy-new-service.md); this page is the security and
PR side.

## 1. Copy the template

```bash
cp -r stacks/_template stacks/<name>
```

Replace every `template` (project name, container names, routers, paths, `op://`
refs), drop what you do not need (the db service, Traefik labels, `.env.tpl`) and
rewrite the header comment: what it does, which host, how it is reached (LAN via
`stacks/proxy`, public via `stacks/dmz-proxy`, Tailscale only), how users log in.

Every service keeps `<<: *hardened` and adds only what it needs. Per item:

| Need | Do |
|---|---|
| The image needs a capability | `cap_add:` the smallest set, one comment per entry ([recipe](../reference/compose-baseline.md#choosing-cap_add)) |
| The image writes outside its volumes | `tmpfs:` for the path; skip `read_only` only after trying |
| No health endpoint | write a `healthcheck:` with whatever the image has (`wget`, `/dev/tcp`, the binary's own probe) |
| A database or cache | its own `internal: true` network; only the app joins both |
| Host networking, devices, the Docker socket | `kdk.baseline.exception: "<key>: <why>"`; socket access goes through a socket proxy (see `stacks/trivy-scan`) |
| A required item the image cannot meet | `kdk.baseline.skip: "<item>"` plus a YAML comment with the reason |

Pin the image to a full `x.y.z` tag (Renovate adds the digest later).

## 2. Register it

- `komodo/resources.toml`: a `[[stack]]` whose `name` equals the directory name
  (dns-reconcile matches them), `server`, `run_directory = "stacks/<name>"`,
  `pre_deploy` with `op inject` if it has a `.env.tpl`. One-shots: `deploy = false`
  plus a Procedure, scheduled off the gitops-sync `:00/:15/:30/:45` slots.
  Servers: `kdk-dkr-01`, `kdk-dkr-dmz-01`, `kdk-dkr-02`, `kdk-dkr-03`,
  `kdk-mon-01`, `kdk-log-01`. kdk-log-01 has no `/etc/komodo/op.env`, so
  `op inject` does not work there.
- `stacks/advisory-watch/catalog.json`: one entry per new image repository (key =
  image without tag, `docker.io/` and `library/`). How: `stacks/advisory-watch/README.md`.
  The `advisory-catalog` Action fails without it.
- Config files mounted with `configs:` or single-file binds are **not**
  content-hashed. If the service must restart on a config edit, add a sha label
  fed by `pre_deploy` (pattern: stack `loki`, label `kdk.rules-sha`).

## 3. Route and authenticate (if it has a web UI)

- **DNS**: dns-reconcile turns each `Host()` rule in the compose file into an
  AdGuard rewrite pointing at the stack's tier ingress. A route that lives in a
  Traefik file-provider file (`stacks/proxy/dynamic/*.yml`) needs a line in
  `EXTRA` in `stacks/dns-reconcile/reconcile.py` (example: `alertmanager.kmkdp.com`, PR #85).
- **Authelia**: the default policy is `deny`. A ForwardAuth-protected host needs an
  `access_control` rule in `stacks/identity/authelia/configuration.yml`
  (usually `two_factor`, `group:internal-users`). That file is a compose
  `configs:` entry: after merge run `RestartStack identity services=[authelia]`,
  or the host answers 403 after login.
- An app with no login of its own goes behind `forwardauth@file`; an app with its
  own login can stay open on the LAN (Grafana) but never goes on the tunnel
  without a security review.

## 4. Check locally

```bash
python3 scripts/compose-baseline-check.py --stack <name>                  # must be clean (or labelled)
python3 scripts/compose-baseline-check.py --allow-known scripts/compose-baseline-known-gaps.txt   # 0 regressions
scripts/compose-config-check.sh <name>                                    # docker compose config with placeholder env
python3 scripts/advisory-catalog-check.py                                  # every image mapped
python3 -c 'import tomllib;tomllib.load(open("komodo/resources.toml","rb"))'
```

A PR that closes gaps in an existing stack regenerates the known-gaps file in
the same PR:

```bash
python3 scripts/compose-baseline-check.py --write-known scripts/compose-baseline-known-gaps.txt
```

## 5. PR body checklist

- [ ] What changes and on which host; what recreates on merge (any image-string or
  env change recreates that container).
- [ ] **Hand steps** in order, before and after merge. Every new secret is created
  in 1Password and written to the host from there in the same step
  ([Rotate a secret](rotate-a-secret.md)): `op item create …`, then
  `op read … | ssh <host> 'sudo install -m 600 -o root -g root /dev/stdin <path>'`.
  Host file edits use VS Code Remote-SSH
  (`code --remote ssh-remote+kdkadmin@<ip> <path>`), then `chown root:root` + `chmod 600`.
- [ ] Host directories created with the right owner before merge
  (`sudo install -d -o <uid> -g <gid> -m 0750 /opt/kdk-lab/<name>`); a missing bind
  source becomes an empty root-owned directory.
- [ ] Validation commands with the expected output, read-only, runnable from an
  allowed host.
- [ ] Rollback: `git revert` + what state is left behind (DB migrations, files on
  the host).
- [ ] No rendered `.env`, no secret values, no security-report text (the repo is public;
  reports live in the vault, `10.Homelab/Security/`).
- [ ] After merge the reviewer posts the validation table
  ([Post-merge validation](post-merge-validation.md)).
