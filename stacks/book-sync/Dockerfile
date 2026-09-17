FROM python:3.12-slim
# ssh/sshpass reach the tablet's dropbear. Calibre's `ebook-convert` turns non-native
# ebook formats (azw3/mobi/...) into EPUB, which the reMarkable can read. Calibre is
# installed WITH recommends so ebook-convert has its Qt runtime deps.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client sshpass \
 && apt-get install -y calibre \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY book-sync.py /app/book-sync.py
ENV PYTHONUNBUFFERED=1 HOME=/tmp QT_QPA_PLATFORM=offscreen
ENTRYPOINT ["python","/app/book-sync.py"]
