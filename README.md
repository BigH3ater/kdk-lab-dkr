# kdk-lab-dkr

Komodo-managed Docker Compose homelab. Git is the source of truth: Komodo pulls
this repo on each host and runs the stacks in `stacks/`. Komodo at
`https://komodo.kmkdp.com` is the single pane of glass.

| Host | Where | Runs |
|---|---|---|
| kdk-dkr-01 (10.1.20.40) | VM 300 on kdk-hyp-01, GTX 1050 Ti | komodo, op-connect, proxy, docs, media, apps, backup |
| kdk-dkr-dmz-01 (192.168.191.20) | VM on kdk-nas-01, Arc A380 | dmz-proxy, identity, dmz-apps, jellyfin, immich |
| kdk-dkr-02 (10.1.30.21) | Pi 5 | home-assistant stack (adopted) |
| kdk-dkr-03 (10.1.30.22) | Pi 5 | scrypted-homebridge stack (adopted) |
| kdk-mon-01 (10.1.20.30) | Pi 5 | external monitoring stack (adopted) |

## How deploys work

1. Edit a stack under `stacks/<name>/`, push to `main`.
2. Komodo (webhook or poll) redeploys that stack on its assigned server.
3. Secrets never live in git: each stack ships a `.env.tpl` of `op://` refs;
   Komodo's pre-deploy runs `op inject -i .env.tpl -o .env` on the host against
   1Password Connect (`stacks/op-connect`, on kdk-dkr-01).

Server and stack definitions are in `komodo/resources.toml` (Komodo Resource Sync).

Docs: `docs/` is published as a Quartz site at `https://docs.kmkdp.com`
(rebuilt on every push by the `docs` stack). Address registry: `docs/network.md`.
