# Same digest-pinned base as stacks/obsidian-to-koreader/Dockerfile (one slim
# Python to track for CVEs across the lab's home-built watchers).
FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# DIRCON is a plain TCP protocol (Wahoo Direct Connect) — NO bleak/BlueZ. The
# only dependency is the MQTT client. paho-mqtt 2.x uses the VERSION2 callback
# API (bridge.py targets it).
RUN pip install --no-cache-dir paho-mqtt==2.1.0
# The container runs unprivileged as 1000:1000 (compose `user:`). Bake a matching
# passwd/group entry so any library that calls getpwuid(1000) finds one; HOME is
# the writable tmpfs /tmp.
RUN groupadd -g 1000 app \
    && useradd -u 1000 -g 1000 -d /tmp -s /usr/sbin/nologin app
COPY bridge.py /app/bridge.py
WORKDIR /tmp
ENV PYTHONUNBUFFERED=1 HOME=/tmp
ENTRYPOINT ["python", "/app/bridge.py"]
