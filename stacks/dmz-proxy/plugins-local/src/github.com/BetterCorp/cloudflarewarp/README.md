# cloudflarewarp (vendored Traefik local plugin)

Copied verbatim from https://github.com/BetterCorp/cloudflarewarp at tag v1.3.3
(MIT, see LICENSE): `cloudflarewarp.go`, `ips/ips.go`, `go.mod`, `.traefik.yml`.
Tests and CI files left out. Loaded by the DMZ Traefik as a *local* plugin
(`--experimental.localPlugins`), so nothing is downloaded at start and the code
that runs is the code in this directory. Review it before bumping: it is one file.

What it does (security plan A5 step 3b, 2026-10-29): for requests whose TCP
peer is in `trustip` (cloudflared at 10.211.2.2), it overwrites
`X-Forwarded-For` and `X-Real-Ip` with `CF-Connecting-IP`, the visitor address
Cloudflare sets and a client cannot forge, and `X-Forwarded-Proto` from
`Cf-Visitor`. Cloudflare's own "Remove X-Forwarded-For" transform was verified
not to clear a client-supplied chain, so this is the only place the real
visitor address is enforced before Authelia and Jellyfin read it.
