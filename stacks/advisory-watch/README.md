# advisory-watch

Weekly one-shot on kdk-log-01 (Monday 07:03 America/Chicago, procedure
`advisory-watch`). For every image tag pinned in `stacks/*/compose.yaml` (and
the `FROM` of the local Dockerfiles) it asks the upstream sources in
`catalog.json` what was published since the last run, and writes one logfmt
line per (image, tag, advisory) to Loki `{job="advisory-watch"}` and stdout:

```
ts=2026-11-02T13:03:20Z image=traefik tag=v3.3 severity=High id=GHSA-46wh-3698-f2cx verdict=Vulnerable fixed_in=v3.6.8 source=ghsa stacks=home-assistant url=https://github.com/... title="..."
```

| verdict | meaning |
|---|---|
| `Vulnerable` | every version the tag can be is inside the affected range (a floating `v3.3` counts as 3.3.0 to 3.3.x) |
| `Patched` | none is, or the tag is at/after the advisory's patched version |
| `Unknown` | not comparable: `latest`/`stable`, digests, date tags, a floating tag that straddles the fix, wrapper images (`tag_is_version: false`), NVD and RSS items, OSV entries with no fix and no GHSA alias |

### Verdict rules

- **Ranges.** GitHub `vulnerable_version_range` conditions are split on commas
  *and* on whitespace before an operator, so `>=v1.68.0 <= 1.75.1` is two
  conditions; a missing space after the operator (`>=v1.68.0`) is fine.
- **Patched wins over an open range.** When the tag's lowest version is at or
  after the advisory's `patched_versions` (a leading `>=`/`v` is ignored; several
  listed fixes -> the newest), the verdict is `Patched` even if an open-ended
  range like `>= 1.75.0` still matches. The same applies to OSV `fixed` events.
- **Contradictory metadata.** If the range's own upper bound also covers the
  fix (`<= v2.36.1` patched `>= v2.36.1`), the verdict stays `Vulnerable`
  (conservative) and the line gets `note=contradictory-metadata`. Check upstream
  by hand.
- **OSV + GHSA alias.** OSV hits are re-judged from their `GHSA-...` alias: the
  catalog repo's advisory list when `github` is set and contains it, else the
  OSV mirror of the GHSA record (`/v1/vulns/GHSA-...`). Those lines say
  `source=osv+ghsa`. Many Go-vulndb entries carry only an `introduced` event,
  which OSV itself reads as "every later version".
- **No fix, no alias.** An OSV entry with no `fixed`/`last_affected` event and
  no resolvable GHSA alias is `Unknown`, `fixed_in=""`, `note=unfixed-or-unknown`.
- **One line per advisory.** The same advisory from the `ghsa` and `osv`
  sources (shared id or alias) for one image tag is emitted once; the `ghsa`
  record wins.

`python3 stacks/advisory-watch/advisory_watch.py --selftest` runs these rules
offline against canned upstream records (real cases: vikunja, rclone, trivy,
audiobookshelf, traefik, coredns) and exits non-zero on a failure.

Alerts (`stacks/loki/rules/advisories.yml`): `AdvisoryPinnedImageVulnerable`
(Vulnerable + Critical/High, warning), `AdvisoryNeedsReview` (Unknown +
Critical/High, info), `AdvisoryWatchSilent` (no output for 8 days).

## Sources

| Field | Source | Notes |
|---|---|---|
| `github` | `GET /repos/{owner}/{repo}/security-advisories` | public; `GITHUB_TOKEN` optional (60 -> 5000 req/h). Without it the job sleeps through the rate-limit window |
| `osv` | `POST https://api.osv.dev/v1/query` with the exact pinned version | only for exact `x.y.z` tags; the first run per version reports everything that affects it |
| `nvd_cpe` | NVD CVE API 2.0, `virtualMatchString=<vendor:product>` | product level, so always `Unknown`; throttled to 5 req/30 s without `NVD_API_KEY`; `NVD_DISABLE=1` turns it off |
| `feed` | RSS/Atom | items whose title matches `feed_filter` (default security/CVE keywords); always `Unknown` |

## Add or change a catalog entry

A new image in any compose file fails CI (`.github/workflows/advisory-catalog.yml`)
until it has an entry. Key = the image reference without tag/digest, with
`docker.io/` and `library/` removed (`ghcr.io/authelia/authelia`, `traefik`,
`lscr.io/linuxserver/radarr`).

```json
"ghcr.io/example/app": {
  "github": "example/app",
  "osv": {"ecosystem": "Go", "package": "github.com/example/app/v2"},
  "nvd_cpe": "cpe:2.3:a:example:app",
  "feed": "https://example.org/security.xml",
  "tag_is_version": true,
  "confirm": true,
  "note": "why the mapping looks like this"
}
```

- `github` is the upstream app repo, not the image repo (linuxserver images map
  to the app, e.g. `Radarr/Radarr`).
- `osv` only when the image tag equals the package version in that ecosystem
  (Go module major suffix included). If not, leave it out and say why in `note`.
- `tag_is_version: false` for wrapper images whose tag is not the product's version.
- Local builds: `"local": true, "base": "<catalog key of the FROM image>"`.
- `confirm: true` = not yet checked by a human; remove it once verified.

Check locally: `python3 scripts/advisory-catalog-check.py`, then
`python3 stacks/advisory-watch/advisory_watch.py --repo . --state /tmp/aw.json --dry-run --only <key>`.

`--only` takes catalog keys (exact match after the same normalisation as
compose refs: lowercased, `docker.io/` and `library/` stripped), either
comma-separated (`--only traefik,rclone/rclone`) or repeated
(`--only traefik --only rclone/rclone`). A key that no stack uses is logged.

## Weekly review (Grafana)

Grafana -> Security -> **Security Advisories**: the first table is every
non-Patched advisory line of the selected range (default 7 days, so it shows
Monday's run), sorted by severity; the second is the Trivy per-image summary
from `stacks/trivy-scan`. For each row: open `url`, decide bump (Renovate PR or
pin change), accept (note it in the weekly log), or fix the catalog (wrong
mapping). `Unknown` rows with Critical/High are the ones a human must decide.

## State and failures

`/opt/kdk-lab/advisory-watch/state.json` = `{source_id: last_success}`. A source
only advances after a clean fetch; nothing is saved if the Loki push fails
(the job then sends a Pushover fallback and exits 2). Exit 1 = some source
failed (others saved); docker-event-watch pages the non-zero exit. Delete the
state file to re-run the 30-day first-run window.
