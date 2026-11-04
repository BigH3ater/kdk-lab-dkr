FROM python:3.12.16-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
# Calibre ships both `calibre-server` (Content Server + OPDS) and `calibredb`
# (library management). Installed WITH recommends so the Qt runtime deps calibre
# needs are present even headless. Same base pattern as book-sync.
RUN apt-get update \
 && apt-get install -y calibre \
 && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1
