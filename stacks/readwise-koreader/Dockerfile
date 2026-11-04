FROM python:3.12.16-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
# Same digest-pinned base as the other build-from-source KOReader one-shots
# (obsidian-to-koreader, koreader-highlights). ebook-convert needs Calibre's Qt
# runtime, which is a Debian apt package (Trivy covers it via advisory-watch);
# the pinned alpine python used by the no-build one-shots can't run it.
# ssh/sshpass reach the tablet's dropbear to drop plain EPUB files for KOReader.
# Calibre's `ebook-convert` renders each Readwise article's HTML into an EPUB.
# Installed WITH recommends so ebook-convert has its Qt runtime deps.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client sshpass ca-certificates \
 && apt-get install -y calibre \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY readwise-koreader.py /app/readwise-koreader.py
ENV PYTHONUNBUFFERED=1 HOME=/tmp QT_QPA_PLATFORM=offscreen
ENTRYPOINT ["python","/app/readwise-koreader.py"]
