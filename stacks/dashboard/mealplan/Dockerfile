FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# Tiny read-mostly sidecar: logs into Tandoor with the dashboard account (session auth,
# since REST tokens are disabled), reads /api/meal-plan/, renders the week, and writes
# back CookLog star ratings. requests handles the cookie/CSRF session; waitress serves it.
# Pins live in requirements.txt so Renovate (pip_requirements) tracks them.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt \
    && install -d -o 1000 -g 1000 /data /state /pantry
WORKDIR /app
COPY app.py /app/app.py
# New Pi product-link lookup; reads foods.yaml bind-mounted at /app/foods.yaml.
COPY newpi.py /app/newpi.py
# Pantry + Thaw tabs; reads cleanup.yaml + the planner config.yaml, both bind-mounted.
COPY stock.py /app/stock.py
# Freezer inventory (Pantry tab Freezer section); /pantry/freezer.json is a bind mount.
COPY freezer.py /app/freezer.py
# Kitchen board + household page (/meal/kitchen, /meal/home): rendering,
# calendar, vacuum line, and the static css/js/fonts/icons served at /meal/static.
COPY kitchen.py homecal.py vacuum.py /app/
# kitchen.kmkdp.com device pairing: signed cookie, device registry (/state), pair codes.
COPY devices.py /app/
# The add box's "Manual list instead" registry (/state/manual-forced.json).
COPY forced.py /app/
# admin.kmkdp.com: status sources, LogQL escaping and the admin page HTML.
COPY admin.py /app/
COPY static /app/static
# PYTHONDONTWRITEBYTECODE: the rootfs is read-only (compose read_only), so no
# __pycache__ writes are attempted.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Non-root, same uid as the grocery-planner job that shares /pantry. The empty
# /data, /state and /pantry above are uid 1000 so a NEW named volume starts
# writable; existing host dirs are chowned by the Komodo pre_deploy.
USER 1000:1000
EXPOSE 8080
CMD ["python", "/app/app.py"]
