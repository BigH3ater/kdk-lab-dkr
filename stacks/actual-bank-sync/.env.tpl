# Rendered to .env by the Komodo pre_deploy step (op inject, kdk-cluster vault).
# Never commit the rendered .env. Two fields from the actual-banksync item:
#   session_token -- Actual session token from a dedicated, never-logged-out
#     browser session of the owner's OIDC user (login is OIDC-only, no password).
#   e2e_password  -- the budget's end-to-end encryption password; update this
#     field whenever the owner rotates the budget key.
# See docs/runbooks/actual-bank-sync.md for how to obtain and store both.
ACTUAL_SESSION_TOKEN="{{ op://kdk-cluster/actual-banksync/session_token }}"
ACTUAL_E2E_PASSWORD="{{ op://kdk-cluster/actual-banksync/e2e_password }}"
