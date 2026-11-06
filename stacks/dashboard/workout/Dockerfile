FROM python:3.12.16-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
# Workout log sidecar (home.kmkdp.com/workout/): renders the day's exercise steps from the
# host-managed workout.json with a log form per exercise; writes Home Assistant helpers + a CSV.
RUN pip install --no-cache-dir flask==3.0.3 requests==2.32.3 waitress==3.0.1
WORKDIR /app
COPY app.py history.py migrate_csv.py /app/
ENV PYTHONUNBUFFERED=1
# Non-root: the CSV volume (/opt/kdk-lab/dashboard/workout-log) is created uid 1000 by pre_deploy.
USER 1000:1000
EXPOSE 8080
CMD ["python", "/app/app.py"]
