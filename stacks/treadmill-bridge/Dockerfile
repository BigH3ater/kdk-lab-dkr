# Same digest-pinned base as stacks/obsidian-to-koreader/Dockerfile (one slim
# Python to track for CVEs across the lab's home-built watchers).
FROM python:3.12.16-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
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
