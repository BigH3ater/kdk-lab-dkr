# Digest-pinned slim Python base (one slim Python to track for CVEs across the
# lab's home-built services). numpy + Pillow ship
# manylinux aarch64 wheels, so no compiler is needed on the Pi.
FROM python:3.12.16-slim@sha256:5b5fe805d367f8f5a359ded6ab2652b7f9a206453ee6ee789c0045ba6ec15016
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
# Runs unprivileged as 1000:1000 (compose `user:`); bake the matching entry.
RUN groupadd -g 1000 app \
    && useradd -u 1000 -g 1000 -d /tmp -s /usr/sbin/nologin app
COPY app.py render.py config.json /app/
WORKDIR /app
ENV PYTHONUNBUFFERED=1 HOME=/tmp
EXPOSE 8125
ENTRYPOINT ["python", "/app/app.py"]
