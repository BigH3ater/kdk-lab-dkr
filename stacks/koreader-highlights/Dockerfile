FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# ssh/sshpass pull the KOReader .sdr sidecars from the tablet's dropbear; slpp parses
# the Lua-serialized annotation tables into Python.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client sshpass \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir slpp==1.2.3
WORKDIR /app
COPY koreader-highlights.py /app/koreader-highlights.py
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python","/app/koreader-highlights.py"]
