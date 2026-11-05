# Same digest-pinned base as stacks/treadmill-bridge/Dockerfile (one slim Python
# to track for CVEs across the lab's home-built services). numpy + Pillow ship
# manylinux aarch64 wheels, so no compiler is needed on the Pi.
FROM python:3.12.16-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
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
