# cloudflare-watch: read-only Cloudflare edge watch (stdlib only, no pip).
# Renovate manages the base image tag + digest (kdk/* images build locally).
FROM python:3.12.16-slim@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07
WORKDIR /app
COPY watch.py /app/watch.py
ENV PYTHONUNBUFFERED=1
# Runs as uid 1000 (set in compose). State is written to the mounted /state.
ENTRYPOINT ["python3", "-I", "/app/watch.py"]
