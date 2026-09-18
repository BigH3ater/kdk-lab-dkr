FROM python:3.12-slim
# pandoc renders Markdown -> PDF via the weasyprint engine (needs cairo/pango).
# ssh/sshpass copy the PDFs onto the tablet's dropbear for KOReader.
RUN apt-get update && apt-get install -y --no-install-recommends \
      pandoc openssh-client sshpass \
      libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
      shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
# Install weasyprint unpinned so pip resolves a self-consistent weasyprint+pydyf pair —
# pinning weasyprint==62.3 alone let pip pull an incompatible pydyf ('super' object has
# no attribute 'transform'). (Re-pin to the resolved versions once a build is verified.)
RUN pip install --no-cache-dir weasyprint
WORKDIR /app
COPY obsidian-to-koreader.py /app/obsidian-to-koreader.py
ENV PYTHONUNBUFFERED=1 HOME=/tmp
ENTRYPOINT ["python","/app/obsidian-to-koreader.py"]
