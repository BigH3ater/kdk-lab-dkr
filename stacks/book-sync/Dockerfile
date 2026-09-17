FROM python:3.12-slim
# Pure-stdlib script; only needs an SSH client + sshpass to reach the tablet's
# dropbear (password auth). No pip deps, no cairo/rmc.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client sshpass \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY book-sync.py /app/book-sync.py
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python","/app/book-sync.py"]
