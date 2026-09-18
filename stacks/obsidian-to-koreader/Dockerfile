FROM python:3.12-slim
# pandoc renders Markdown -> PDF via the weasyprint engine (needs cairo/pango).
# ssh/sshpass copy the PDFs onto the tablet's dropbear for KOReader.
RUN apt-get update && apt-get install -y --no-install-recommends \
      pandoc openssh-client sshpass \
      libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
      shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
# weasyprint + a MATCHING pydyf (pinning weasyprint alone let pip pull an incompatible
# pydyf -> 'super' object has no attribute 'transform'). These versions were resolved
# together from an unpinned install and verified rendering all vault sections.
RUN pip install --no-cache-dir weasyprint==70.0 pydyf==0.12.1
WORKDIR /app
COPY obsidian-to-koreader.py /app/obsidian-to-koreader.py
ENV PYTHONUNBUFFERED=1 HOME=/tmp
ENTRYPOINT ["python","/app/obsidian-to-koreader.py"]
