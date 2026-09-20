# op-injected by pre_deploy (op inject). The ONLY secret Actual needs at deploy time is the
# OIDC client secret -- the PLAINTEXT value, which must match the HASHED copy Authelia holds
# in its secrets file (/secrets/authelia-app-secrets/actual_client_secret on the DMZ). The
# server password / SimpleFIN token are set in-app, not here.
#
# Create the op item first (writable vault): the plaintext at
#   op://kdk-cluster/authelia-app-secrets/actual_oidc_plaintext
# and generate the Authelia hash from that same plaintext (see docs/services/actual.md).
ACTUAL_OPENID_CLIENT_SECRET="{{ op://kdk-cluster/authelia-app-secrets/actual_oidc_plaintext }}"
