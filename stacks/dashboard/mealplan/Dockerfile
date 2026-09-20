FROM python:3.12-slim
# Tiny read-mostly sidecar: logs into Tandoor with the dashboard account (session auth,
# since REST tokens are disabled), reads /api/meal-plan/, renders the week, and writes
# back CookLog star ratings. requests handles the cookie/CSRF session; waitress serves it.
RUN pip install --no-cache-dir flask==3.0.3 requests==2.32.3 waitress==3.0.0
WORKDIR /app
COPY app.py /app/app.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/app.py"]
