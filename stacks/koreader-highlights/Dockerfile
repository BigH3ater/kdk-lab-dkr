FROM python:3.12.16-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
# ssh/sshpass pull the KOReader .sdr sidecars from the tablet's dropbear; slpp parses
# the Lua-serialized annotation tables into Python.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client sshpass \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir slpp==1.2.3
WORKDIR /app
COPY koreader-highlights.py /app/koreader-highlights.py
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python","/app/koreader-highlights.py"]
