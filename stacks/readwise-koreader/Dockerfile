FROM python:3.12-slim
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
