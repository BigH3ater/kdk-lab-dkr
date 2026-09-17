FROM python:3.12-slim
# rmc/rmscene (.rm -> svg), cairosvg (svg -> png) needs cairo; ssh+sshpass reach
# the tablet's dropbear (password auth). No node/chromium: diagrams render via the
# pure-python topology_svg (kodiak_diagram), not mermaid.
RUN apt-get update && apt-get install -y --no-install-recommends \
      openssh-client sshpass libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
      libgdk-pixbuf-2.0-0 shared-mime-info \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir rmc==0.3.0 cairosvg==2.7.1
WORKDIR /app
COPY pipeline.py /app/pipeline.py
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/diagram
ENTRYPOINT ["python","/app/pipeline.py"]
CMD ["run"]
