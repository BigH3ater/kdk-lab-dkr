FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# Tiny read-mostly sidecar: logs into Tandoor with the dashboard account (session auth,
# since REST tokens are disabled), reads /api/meal-plan/, renders the week, and writes
# back CookLog star ratings. requests handles the cookie/CSRF session; waitress serves it.
RUN pip install --no-cache-dir flask==3.0.3 requests==2.32.3 waitress==3.0.1 pyyaml==6.0.3
# Kitchen board calendar: parse the household ICS feed and expand recurring
# events. Transitive deps pinned too (recurring-ical-events wants dateutil < 3).
RUN pip install --no-cache-dir icalendar==7.3.0 recurring-ical-events==3.8.2 \
    x-wr-timezone==2.0.1 python-dateutil==2.9.0.post0 tzdata==2026.5 \
    typing-extensions==4.16.0 click==8.5.0
# Kitchen device pairing: the QR code for the pairing link (pure Python, no deps).
RUN pip install --no-cache-dir segno==1.6.6
WORKDIR /app
COPY app.py /app/app.py
# New Pi product-link lookup; reads foods.yaml bind-mounted at /app/foods.yaml.
COPY newpi.py /app/newpi.py
# Pantry + Thaw tabs; reads cleanup.yaml + the planner config.yaml, both bind-mounted.
COPY stock.py /app/stock.py
# Kitchen board + household page (/meal/kitchen, /meal/home): rendering,
# calendar, vacuum line, and the static css/js/fonts/icons served at /meal/static.
COPY kitchen.py homecal.py vacuum.py /app/
# kitchen.kmkdp.com device pairing: signed cookie, device registry (/state), pair codes.
COPY devices.py /app/
COPY static /app/static
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/app.py"]
