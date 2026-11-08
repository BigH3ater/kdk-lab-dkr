FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# Tiny read-mostly sidecar: logs into Tandoor with the dashboard account (session auth,
# since REST tokens are disabled), reads /api/meal-plan/, renders the week, and writes
# back CookLog star ratings. requests handles the cookie/CSRF session; waitress serves it.
RUN pip install --no-cache-dir flask==3.0.3 requests==2.32.3 waitress==3.0.1 pyyaml==6.0.3
WORKDIR /app
COPY app.py /app/app.py
# New Pi product-link lookup; reads foods.yaml bind-mounted at /app/foods.yaml.
COPY newpi.py /app/newpi.py
# Pantry + Thaw tabs; reads cleanup.yaml + the planner config.yaml, both bind-mounted.
COPY stock.py /app/stock.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/app.py"]
