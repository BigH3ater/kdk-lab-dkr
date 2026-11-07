FROM python:3.12.16-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
# Calibre ships both `calibre-server` (Content Server + OPDS) and `calibredb`
# (library management). Installed WITH recommends so the Qt runtime deps calibre
# needs are present even headless. Same base pattern as book-sync.
RUN apt-get update \
 && apt-get install -y calibre \
 && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1
