# op-injected by the pre_deploy step (op inject -f -i .env.tpl -o .env). ALL op items
# referenced here MUST exist in 1Password BEFORE the first deploy, or `op inject` fails
# and the stack won't deploy. Vault kdk-cluster is read/WRITE (create new items there);
# kdk-ops is read-only (reuse existing items only). Never print secret values.
#
# Item naming standard: {service}-api-ro (read-only API key), field `password`.

# ---- P1: Vikunja (tasks app) --------------------------------------------------
# Create: op item "vikunja-app" in kdk-cluster with a field `jwt_secret` (random 64+ chars).
VIKUNJA_SERVICE_JWTSECRET={{ op://kdk-cluster/vikunja-app/jwt_secret }}

# ---- Home Assistant token (household + kitchen vacuum line) ---------------------
# Long-lived HA access token, read-only use: the mealplan sidecar only GETs
# /api/states (mealplan/vacuum.py). The name HOMEPAGE_VAR_HA_TOKEN and the item
# name homepage-ha-token date from the retired Homepage widgets; the sidecar reads
# this exact variable, so a rename is a follow-up (sidecar + .env.tpl + 1P item
# together), not churn to do in passing. Owner mints it in HA -> Profile ->
# Security -> Long-lived access tokens into the 1P API-Credential item
# kdk-cluster/homepage-ha-token field `credential` (see rotate-a-secret.md).
HOMEPAGE_VAR_HA_TOKEN={{ op://kdk-cluster/homepage-ha-token/credential }}

# ---- P2b: per-user Vikunja tokens (task write-back attributed via SSO) --------
# Each household member mints their own full-permission token in Vikunja and it
# lands in a kdk-cluster item named vikunja-api-<user>, field password. The
# sidecar maps Remote-User -> token so completions show the right doer.
# Shared family board + jmack's completions both use the rw token (it IS jmack's
# full-permission token). rach's own token can be added later for attribution.
VIKUNJA_TOKEN_JMACK={{ op://kdk-cluster/vikunja-api-rw/credential }}
# VIKUNJA_TOKEN_RACH <- 1P item kdk-cluster/vikunja-api-rach field password (uncomment once rach has minted hers)

# ---- P3: household calendar (Proton "Share via link" ICS) ---------------------
# Proton "Share via link" ICS (1Password item kdk-cluster/proton-calendar-ics, field url, read-only).
# The mealplan sidecar (mealplan/homecal.py) reads it as HOMEPAGE_VAR_ICS_URL; the
# name is left over from the retired Homepage calendar widget (rename = follow-up).
HOMEPAGE_VAR_ICS_URL={{ op://kdk-cluster/proton-calendar-ics/url }}

# ---- P2: mealplan sidecar (dedicated Tandoor account) -------------------------
# Create: op item "dashboard-tandoor-rw" in kdk-cluster with fields `username`/`password`.
# The account is created in Tandoor (local login) and ADDED to the "Mack House" space
# (id 3). It is read/write at the API level but the sidecar only ever writes CookLog
# ratings -- nothing else.
TANDOOR_USER={{ op://kdk-cluster/dashboard-tandoor-rw/username }}
TANDOOR_PASS={{ op://kdk-cluster/dashboard-tandoor-rw/password }}

# ---- kitchen.kmkdp.com device pairing -----------------------------------------
# HMAC key that signs the kitchen screen's 1-year device cookie. Item
# dashboard-kitchen-device-key in kdk-cluster, field password (generated, 64
# characters). Must exist in 1Password BEFORE this merges or op inject fails and
# the stack won't deploy. Rotating it revokes every paired device (re-pair after).
KITCHEN_DEVICE_KEY={{ op://kdk-cluster/dashboard-kitchen-device-key/password }}
# Chore ticks from the kitchen screen are credited to a Vikunja "Kitchen" account
# once its token exists (item vikunja-api-kitchen in kdk-cluster, field password);
# until then they fall back to the jmack token. Add the VIKUNJA_TOKEN_KITCHEN line
# as a real assignment only after that item exists (see docs/services/dashboard.md).

# ---- admin.kmkdp.com: Komodo deploy-failures tile (optional) ---------------------
# A READ-ONLY Komodo API key for a non-admin service user (see
# docs/services/dashboard.md "Hand step: the Komodo read key"). Until the item
# kdk-cluster/komodo-api-ro exists, leave these two lines commented: the tile
# then says "not configured" and nothing else changes. Uncomment both only AFTER
# the item exists, or op inject fails and the stack won't deploy.
# KOMODO_API_KEY <- 1P item kdk-cluster/komodo-api-ro field username (no template braces in a comment: op inject would still resolve them)
# KOMODO_API_SECRET <- 1P item kdk-cluster/komodo-api-ro field password
