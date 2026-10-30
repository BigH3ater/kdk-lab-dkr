FROM python:3.12.16-slim@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07
# pandoc renders Markdown -> PDF via the weasyprint engine (needs cairo/pango).
# ssh/sshpass copy the PDFs onto the tablet's dropbear for KOReader.
RUN apt-get update && apt-get install -y --no-install-recommends \
      pandoc openssh-client sshpass \
      libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
      shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
# The container runs unprivileged as 1000:1000 (compose). OpenSSH's client refuses to
# run for a uid with no passwd entry ("No user exists for uid 1000") and returns at once,
# so pick_host() would see every probe fail and treat the tablet as asleep. Bake a
# matching passwd/group entry (home /tmp = the writable HOME, no login shell).
RUN groupadd -g 1000 app \
    && useradd -u 1000 -g 1000 -d /tmp -s /usr/sbin/nologin app
# weasyprint + a MATCHING pydyf (pinning weasyprint alone let pip pull an incompatible
# pydyf -> 'super' object has no attribute 'transform'). These versions were resolved
# together from an unpinned install and verified rendering all vault sections.
RUN pip install --no-cache-dir weasyprint==70.0 pydyf==0.12.1
COPY obsidian-to-koreader.py /app/obsidian-to-koreader.py
# Default CWD = /tmp (writable by the unprivileged uid 1000) so any tool that writes scratch
# in the CWD does not hit the root-owned /app. render_pdf also passes cwd=/state/build.
WORKDIR /tmp
ENV PYTHONUNBUFFERED=1 HOME=/tmp
ENTRYPOINT ["python","/app/obsidian-to-koreader.py"]
