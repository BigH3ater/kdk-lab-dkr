# Rendered by pre_deploy (op inject) to vikunja-config.yml -- mounted at
# /etc/vikunja/config.yml. Only the OIDC client secret is sensitive; provider
# arrays cannot be expressed as env vars, hence the file.
auth:
  local:
    # OIDC-only login (Authelia), matching the household standard (budget app).
    enabled: false
  openid:
    enabled: true
    providers:
      - name: Authelia
        authurl: https://sso.kmkdp.com
        clientid: vikunja
        clientsecret: {{ op://kdk-cluster/authelia-app-secrets/vikunja_oidc_plaintext }}
