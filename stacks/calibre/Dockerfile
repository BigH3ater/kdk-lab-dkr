FROM python:3.12-slim
# Calibre ships both `calibre-server` (Content Server + OPDS) and `calibredb`
# (library management). Installed WITH recommends so the Qt runtime deps calibre
# needs are present even headless. Same base pattern as book-sync.
RUN apt-get update \
 && apt-get install -y calibre \
 && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1
