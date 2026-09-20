OBSIDIAN_AUTH_TOKEN="{{ op://kdk-ops/obsidian-sync/token }}"
# Remote Obsidian Sync vault name (not a secret). Literal because the SA op token is
# read-only on kdk-ops. Renamed "Kodiak Codex" -> "kodiak-codex" (2026-09-20).
OBSIDIAN_VAULT_NAME="kodiak-codex"
OBSIDIAN_VAULT_PASSWORD="{{ op://kdk-ops/obsidian-sync/vault_password }}"
