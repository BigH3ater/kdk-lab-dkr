---
title: SearXNG
host: kdk-dkr-01
stack: searxng
tier: internal
status: new
secrets: [searxng]
updated: 2026-11-09
tags: [homelab/stack, tier/internal, status/new]
---

# SearXNG (search.kmkdp.com)

The household's private web search. [SearXNG](https://docs.searxng.org) is a
metasearch engine: it sends each query from kdk-dkr-01 to several search engines
(Google, DuckDuckGo, Brave, Wikipedia, ...), merges the results and shows them on one
page. The engines see the lab's IP, not the person, no profile builds up, and there are
no ads or trackers on the page.

LAN + Tailscale only on **kdk-dkr-01**, behind **Authelia** (two_factor, group
`dashboard-users`). No DMZ, no Cloudflare tunnel. SearXNG has no accounts of its own;
settings a person changes on the Preferences page live in a cookie in their browser.

**Related:** [[dashboard]] · [[identity]] · [[traefik]] · runbook [SearXNG](../runbooks/searxng.md)

## At a glance

| Field | Value |
|---|---|
| Purpose | Private metasearch for the household (web, images, videos, news, map, IT, science) |
| URL | https://search.kmkdp.com (internal Traefik, `websecure` only, `forwardauth@file`). Off the LAN: Tailscale |
| Auth | Authelia two_factor, group `dashboard-users`. Bypass for exactly `/healthz` and `/opensearch.xml` |
| Host | [[kdk-dkr-01]] |
| Stack | `stacks/searxng`: `searxng/searxng:2026.11.8-3a3dd15bf` (Docker Hub, rolling date tag) + `valkey/valkey:9.1.3-alpine`, both digest-pinned, Renovate-tracked |
| Config | `stacks/searxng/config/settings.yml` + `limiter.toml`, read-only over `/etc/searxng` |
| Secret | `kdk-cluster/searxng` field `secret` → `SEARXNG_SECRET` (signs the Flask session cookie and the image-proxy URLs) |
| State | none: cache dir is tmpfs, valkey has no persistence. Nothing to back up |
| Deploy | Komodo stack `searxng` (`deploy = true`), webhook + 15-min poll |
| Blast radius | Search page down; nothing else depends on it |

## Settings

`settings.yml` uses `use_default_settings`: everything not listed comes from the
upstream `searx/settings.yml` of the pinned image.

| Setting | Value | Why |
|---|---|---|
| `server.base_url` | `https://search.kmkdp.com/` | absolute links, `/opensearch.xml` |
| `server.limiter` | `true` | bot detection + rate limit (`limiter.toml`, counters in valkey) |
| `server.image_proxy` | `true` | thumbnails load through the lab, not from the engines' CDNs |
| `server.method` | `GET` | the browser default-engine template needs `?q=` |
| `search.safe_search` | `0` (off) | household choice; `1` moderate, `2` strict |
| `search.autocomplete` | `duckduckgo` | suggestions while typing |
| `search.default_lang` | `en-US` | English results, US region |
| `search.formats` | `[html]` | no JSON/CSV/RSS API output |
| `ui.default_theme` | `simple` (auto light/dark) | |
| `outgoing` | 3 s per engine, 10 s max | DNS is the container resolver (Docker → 10.1.1.53/.54), `dns_search: .` in compose |

Engines (`use_default_settings.engines.keep_only`, everything else is dropped, so no
engine that needs an API key exists here):

| Category | Engines |
|---|---|
| General | Google, DuckDuckGo, Brave, Wikipedia, Wikidata (infobox), currency |
| Images | Google images, DuckDuckGo images, Brave images, Bing images |
| Videos | YouTube, Google videos, DuckDuckGo videos, Brave videos |
| News | Google news, DuckDuckGo news, Brave news |
| Map | OpenStreetMap |
| IT | GitHub, Stack Overflow, Super User, Ask Ubuntu, MDN, Arch Linux wiki, Docker Hub, PyPI |
| Science | arXiv |

Google web and Google images ship `disabled: true` upstream (Google often answers
scripted clients with a CAPTCHA); they are switched on here. **Reddit** is not
available: upstream removed the engine on 2026-07-30 because Reddit now requires
authentication. Use `site:reddit.com` in a normal search.

Shortcuts: start a query with `!` + the engine shortcut to ask only that engine,
e.g. `!gh komodo`, `!wp tiffin iowa`, `!osm coralville`, `!yt sourdough`.

## Add or remove an engine

One PR, `stacks/searxng/config/settings.yml` only:

1. Find the engine's exact `name:` in upstream `searx/settings.yml` for the pinned
   build (the commit after the `-` in the image tag), e.g.
   `https://github.com/searxng/searxng/blob/3a3dd15bf/searx/settings.yml`.
2. Add (or delete) that name under `use_default_settings.engines.keep_only`.
3. If upstream has `disabled: true` on it, add `- name: <name>` + `disabled: false`
   under `engines:` (as for Google).
4. Skip engines that need an `api_key` unless the key goes to 1Password and into
   `.env.tpl` in the same PR.
5. After merge, the pre_deploy hash (`SEARXNG_CONFIG_SHA`) changes and Komodo recreates
   the container. Check `https://search.kmkdp.com/preferences` → Engines.

A person can also switch engines off for themselves on the Preferences page (stored
in their browser cookie); that needs no PR.

## Set it as the browser's search engine

The search page links `/opensearch.xml` (Authelia lets that one file through), so
browsers can add it. Searches from the address bar still go through Authelia: after
the session ends the first search shows the SSO login, then the results.

- **Firefox (desktop)**: open `https://search.kmkdp.com`, then right-click the
  address bar → **Add "Kodiak Search"**. Settings → Search → Default Search Engine →
  Kodiak Search.
- **Chrome / Edge / Brave**: Settings → Search engine → Manage search engines → Site
  search → **Add**: name `Kodiak Search`, shortcut `s`, URL
  `https://search.kmkdp.com/search?q=%s`. Then ⋮ → **Make default**. (Chrome also
  lists the site under "Inactive shortcuts" after a visit; activate it there.)
- **Safari (macOS, iOS)**: Safari only offers its built-in engines as the default and
  ignores OpenSearch for that. Use a bookmark/Home Screen icon for
  `https://search.kmkdp.com`, or a search-redirect extension. On iPhone, Home Screen
  icon: Safari → Share → **Add to Home Screen**.
- **Homepage**: the **Search** tile in the Home group opens it.

Address-bar suggestions use `/autocompleter`, which is behind Authelia. A browser that
sends those requests without the Authelia cookie gets no suggestions; typing on the
search page itself always suggests.

## Verify

**Laptop (LAN or tailnet)**

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://search.kmkdp.com/                 # 302 (to sso.kmkdp.com)
curl -s https://search.kmkdp.com/healthz; echo                                     # OK  (Authelia bypass, limiter-exempt)
curl -s -A 'Mozilla/5.0' https://search.kmkdp.com/opensearch.xml | grep -c 'search.kmkdp.com/search'   # 1
```

`curl` without a browser User-Agent gets **429** on every path except `/healthz`: that
is the limiter, not a fault.

**kdk-dkr-01 (`ssh kdkadmin@10.1.20.20`)**

```bash
sudo docker ps --filter label=com.docker.compose.project=searxng --format '{{.Names}}\t{{.Status}}'   # searxng, searxng-valkey: Up (healthy)
sudo docker inspect searxng --format '{{.Config.User}} ro={{.HostConfig.ReadonlyRootfs}} caps={{.HostConfig.CapDrop}}+{{.HostConfig.CapAdd}} {{.HostConfig.SecurityOpt}}'
# 977:977 ro=true caps=[ALL]+[] [no-new-privileges:true]
```

In a browser: a search for `wikipedia` returns results from several engines (the
engine names show under each result).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Too Many Requests` (429) page for a person | the limiter: more than 15 searches in 20 s or 150 in 10 min from one IP, or a request without the usual browser headers (`Accept: text/html`, `Accept-Language`) | wait a minute. If one device keeps hitting it, add its address to `pass_ip` in `config/limiter.toml` (one PR). Find the 429s in Grafana Explore: `{host="kdk-dkr-01", container="traefik"} \|= "search.kmkdp.com" \| json \| RequestHost="search.kmkdp.com" \| DownstreamStatus=429` |
| Every household client is rate-limited at once | SearXNG sees Traefik's address instead of the client's: Traefik is outside `trusted_proxies` (`10.208.0.0/16`) | `sudo docker network inspect proxy --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'` must be inside 10.208.0.0/16; fix `trusted_proxies` |
| An engine shows "suspended" / "CAPTCHA" / "too many requests" under the results | that engine blocked the lab's IP for a while; SearXNG pauses it (CAPTCHA 1 h, access denied / 429 3 min, Cloudflare CAPTCHA 15 days) | nothing: the other engines answer meanwhile. Per-engine errors: `https://search.kmkdp.com/stats`. If Google stays blocked for days, switch it off for yourself on Preferences or drop it from `keep_only` |
| No results at all, every engine times out | no internet egress from the container, or DNS | `sudo docker exec searxng wget -q -O /dev/null https://duckduckgo.com && echo ok`; DOCKER-USER must not drop public destinations ([host firewall](../runbooks/host-firewall.md)) |
| 403 from Authelia after login | the person is not in `dashboard-users`, or Authelia has not loaded the rule | LLDAP group; on the DMZ `sudo docker inspect authelia --format '{{index .Config.Labels "kdk.config-sha"}}'` changed after the merge |
| Container restart-loops, log says `Read-only file system` | the image wrote outside the tmpfs paths (read_only was not runtime-tested before the first deploy) | add the path to the service's `tmpfs:`; only if it is the app tree, `kdk.baseline.skip: "read_only"` with the error in a comment |
| Container exits at start, `Permission denied` on `/etc/searxng/settings.yml` | uid 977 cannot read Komodo's repo checkout (the clone is root-owned; files must be world-readable, directories world-executable) | `sudo stat -c '%a %U' <run dir>/stacks/searxng/config <run dir>/stacks/searxng/config/settings.yml` (want `755`/`644`); fix the clone's umask rather than running searxng as root |
| Log: `The limiter requires Valkey` | valkey not reachable at start | `sudo docker logs searxng-valkey`; the limiter is off until searxng restarts with valkey up |
| Log: `Sec-Fetch cannot be verified for non-secure requests` (WARNING, each search) | Traefik terminates TLS and Granian sees plain HTTP, so SearXNG skips its Sec-Fetch header check | expected; the other limiter checks still run |
| Log: `WARNING ... "/etc/searxng" directory is not owned by "searxng:searxng"` at start | the config dir is a read-only repo mount, owned by root | expected |

## Dependencies

| Needs | Why |
|---|---|
| [[traefik]] (`proxy` stack, `forwardauth@file`) | ingress + Authelia hand-off |
| [[identity]] (Authelia rules for `search.kmkdp.com`) | the only login gate |
| [[op-connect]] | `op inject` in the pre_deploy |
| Internet egress (HTTPS to the engines) | every search |

## Observability

Container logs go to Loki through Alloy (`{container="searxng"}`,
`{container="searxng-valkey"}`); Traefik's access log covers the requests. No alert
rule: SearXNG logs limiter blocks (429s) at DEBUG, below the production WARNING
level, so there is no log line to alert on; the Traefik query in Troubleshooting
finds them. Engine health is on `/stats`.

## Architecture

| Service | Image | Networks | Writes | Hardening |
|---|---|---|---|---|
| `searxng` (Granian, :8080) | searxng/searxng | backend, proxy (egress) | tmpfs `/tmp`, `/var/cache/searxng` | uid 977, read_only, cap_drop ALL (no cap_add), no-new-privileges, 512m / 256 pids |
| `valkey` | valkey/valkey alpine | backend (internal) | tmpfs `/tmp`, `/data` (no persistence) | uid 999, read_only, cap_drop ALL, no-new-privileges, 128m / 256 pids |

The image would start as root to chown its mounts and run `update-ca-certificates`;
as uid 977 the entrypoint skips both, so it needs no capabilities.

## Secret Rotation

| Secret (1P item, `kdk-cluster`) | Used for | Class | Rotate live? |
|---|---|---|---|
| `searxng` field `secret` | `SEARXNG_SECRET`: signs the Flask session cookie and the image-proxy URLs | 🟢 self-service | new value ([runbook](../runbooks/searxng.md#rotate-the-secret)), Deploy `searxng`; thumbnails on an already-open results page stop loading until a new search |
