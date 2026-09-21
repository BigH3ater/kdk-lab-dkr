# op-injected by the pre_deploy step (op inject -f -i .env.tpl -o .env). ALL op items
# referenced here MUST exist in 1Password BEFORE the first deploy, or `op inject` fails
# and the stack won't deploy. Vault kdk-cluster is read/WRITE (create new items there);
# kdk-ops is read-only (reuse existing items only). Never print secret values.
#
# Item naming standard: {service}-api-ro (read-only API key), field `password`.

# ---- P1: Vikunja (tasks app) --------------------------------------------------
# Create: op item "vikunja-app" in kdk-cluster with a field `jwt_secret` (random 64+ chars).
VIKUNJA_SERVICE_JWTSECRET={{ op://kdk-cluster/vikunja-app/jwt_secret }}

# ---- P1: Homepage status/stat widget API keys (read-only) ---------------------
# Each is a read-only API key minted in that service's own settings, stored as the
# `password` field of the named kdk-cluster item. If one isn't ready yet, comment out
# BOTH this line AND its widget block in config/services.yaml to let P1 deploy, then
# add it back. (siteMonitor up/down health needs no key and always works.)
# P1b: HOMEPAGE_VAR_JELLYFIN_KEY <- 1P item kdk-cluster/jellyfin-api-ro field password (uncomment as a real line once the item exists)
# P1b: HOMEPAGE_VAR_SEERR_KEY <- 1P item kdk-cluster/seerr-api-ro field password (uncomment as a real line once the item exists)
# P1b: HOMEPAGE_VAR_ABS_KEY <- 1P item kdk-cluster/audiobookshelf-api-ro field password (uncomment as a real line once the item exists)
HOMEPAGE_VAR_VIKUNJA_KEY={{ op://kdk-cluster/vikunja-api-ro/password }}

# ---- P2b: per-user Vikunja tokens (task write-back attributed via SSO) --------
# Each household member mints their own full-permission token in Vikunja and it
# lands in a kdk-cluster item named vikunja-api-<user>, field password. The
# sidecar maps Remote-User -> token so completions show the right doer.
# Shared family board + jmack's completions both use the rw token (it IS jmack's
# full-permission token). rach's own token can be added later for attribution.
VIKUNJA_TOKEN_JMACK={{ op://kdk-cluster/vikunja-api-rw/credential }}
# VIKUNJA_TOKEN_RACH <- 1P item kdk-cluster/vikunja-api-rach field password (uncomment once rach has minted hers)

# ---- P3: household calendar (Proton "Share via link" ICS) ---------------------
# Reused from planner-service (kdk-ops, read-only). The calendar widget in
# config/services.yaml reads this via the HOMEPAGE_VAR_ICS_URL variable.
HOMEPAGE_VAR_ICS_URL={{ op://kdk-ops/proton-calendar-ics/url }}

# ---- P2: mealplan sidecar (dedicated Tandoor account) -------------------------
# Create: op item "dashboard-tandoor-rw" in kdk-cluster with fields `username`/`password`.
# The account is created in Tandoor (local login) and ADDED to the "Mack House" space
# (id 3). It is read/write at the API level but the sidecar only ever writes CookLog
# ratings -- nothing else.
TANDOOR_USER={{ op://kdk-cluster/dashboard-tandoor-rw/username }}
TANDOOR_PASS={{ op://kdk-cluster/dashboard-tandoor-rw/password }}
