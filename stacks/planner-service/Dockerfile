FROM python:3.12-slim
# icalendar+recurring_ical_events parse the Proton ICS (incl. RRULE); cairosvg
# renders the Kodiak-brand planner PDF (needs cairo); ssh+sshpass push it to the
# tablet's dropbear. Reuses remarkable/pdf-templates/kodiak_lib.py (mounted).
RUN apt-get update && apt-get install -y --no-install-recommends \
      openssh-client sshpass libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
      libgdk-pixbuf-2.0-0 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir icalendar==6.1.0 recurring_ical_events==3.3.0 cairosvg==2.7.1
WORKDIR /app
COPY planner.py /app/planner.py
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/tpl
ENTRYPOINT ["python","/app/planner.py"]
