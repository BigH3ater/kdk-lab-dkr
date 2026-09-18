FROM python:3.12-slim
# icalendar+recurring_ical_events parse the Proton ICS (incl. RRULE); cairosvg
# renders the Kodiak-brand planner PDF (needs cairo). Delivery = rmapi (ddvk fork)
# uploading to the live rmfakecloud, which the tablet pulls on sync. Reuses
# remarkable/pdf-templates/kodiak_lib.py (mounted at /app/tpl).
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
      libgdk-pixbuf-2.0-0 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
# rmapi (ddvk fork) — pinned; amd64 asset. curl -f so a bad URL fails loudly.
ARG RMAPI_VERSION=v0.0.34
RUN curl -fL "https://github.com/ddvk/rmapi/releases/download/${RMAPI_VERSION}/rmapi-linux-amd64.tar.gz" \
      | tar xz -C /usr/local/bin rmapi \
    && chmod +x /usr/local/bin/rmapi
RUN pip install --no-cache-dir icalendar==6.1.0 recurring_ical_events==3.3.0 cairosvg==2.7.1
WORKDIR /app
COPY planner.py /app/planner.py
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/tpl
ENTRYPOINT ["python","/app/planner.py"]
