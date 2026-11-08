# Wizarr with the kdk LDAP patches (docs/services/wizarr.md, "Local image (patched)").
# Same digest-pinned upstream ref the compose file used before, so Renovate keeps
# bumping it (dmz patch group). The image has no `patch`/`git`, so patches/apply.py
# applies each patch with zero fuzz: if a bump changes the patched code, the build
# FAILS here and the old container keeps running until the patch is re-based.
FROM ghcr.io/wizarrrr/wizarr:v2026.10.1@sha256:08f2c6de534ec7d36d39a4f55b7c5c379021ca881dc519c702aad0ddafbdc67a

COPY patches/ /tmp/kdk-patches/
# grep = belt and braces: the refusal string must be in the shipped file. The
# __pycache__ wipe stops a stale upstream .pyc from ever shadowing the patch.
RUN python3 -I /tmp/kdk-patches/apply.py /tmp/kdk-patches/0001-refuse-existing-ldap-user.patch /app \
    && grep -q "That username is already taken" /app/app/services/ldap/client.py \
    && rm -rf /tmp/kdk-patches /app/app/services/ldap/__pycache__

# No USER / ENTRYPOINT / CMD / WORKDIR here: upstream's (root, then the PUID/PGID
# drop in docker-entrypoint.sh, gunicorn via uv, /app) are inherited unchanged.
