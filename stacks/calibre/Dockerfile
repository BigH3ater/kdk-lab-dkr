FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# Calibre ships both `calibre-server` (Content Server + OPDS) and `calibredb`
# (library management). Installed WITH recommends so the Qt runtime deps calibre
# needs are present even headless. Same base pattern as book-sync.
RUN apt-get update \
 && apt-get install -y calibre \
 && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1
# The import loop (stacks/calibre/importer.py, tests in tests/): stdlib-only,
# runs on this image's python3 and shells out to calibredb.
COPY importer.py /app/importer.py
