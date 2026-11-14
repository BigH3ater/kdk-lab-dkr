FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# scale-bridge (stacks/scale-bridge): Body Pod 2 BLE scale -> wger. Talks to the
# host's bluetoothd over the D-Bus system bus (bind-mounted by compose); needs no
# Bluetooth packages in the image. Dependencies via uv from the committed uv.lock
# (owner 2026-11-01: use uv). Every dependency has an aarch64 wheel except pyric
# (pure Python, built from its sdist).
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock /app/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
RUN uv sync --frozen --no-dev --no-install-project && rm /usr/local/bin/uv
ENV PATH="/app/.venv/bin:$PATH"
COPY scale_bridge/ /app/scale_bridge/
# -I (isolated mode) drops the working dir from sys.path; a .pth file in the venv's
# site-packages is still honoured (same fix as stacks/grocery-planner).
RUN echo /app > /app/.venv/lib/python3.12/site-packages/kdk-scale-bridge.pth \
    && python -I -c "import scale_bridge.ble, scale_bridge.app"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Non-root; nothing in the image is writable by this uid (compose adds read_only).
# BlueZ's D-Bus policy lets any uid send to org.bluez (default context), but the
# host's dbus-daemon only accepts a connection from a uid that resolves on the
# host: 65534 (nobody:nogroup on Debian). 65532 has no host entry -> the bus
# resets the connection (ConnectionResetError, Errno 104).
USER 65534:65534
# The main loop touches this file at least every 30 s (and around every session).
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-I", "-c", "import os,sys,time; sys.exit(0 if time.time() - os.path.getmtime('/tmp/scale-bridge.alive') < 300 else 1)"]
ENTRYPOINT ["python", "-I", "-m", "scale_bridge"]
