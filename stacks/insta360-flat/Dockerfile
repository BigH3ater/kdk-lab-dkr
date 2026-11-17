# kdk/insta360-flat: python (stdlib) + static ffmpeg + PhotoCraft CLI, both pinned by
# exact version and sha256. Bump: change the ARGs below (the sha256 values come from the
# releases' own checksum files: BtbN checksums.sha256, PhotoCraft SHA256SUMS.txt) and
# re-run the validation in docs/runbooks/imaging.md. Renovate tracks only the base image.
# compose.yaml names the image after both versions: bump that tag with the ARGs, so the
# next Komodo deploy (no run_build) builds the new image instead of reusing the old one.
FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS fetch
# BtbN static FFmpeg n9.0.2 (GPL). BtbN deletes its daily autobuilds after ~2 weeks but
# keeps the last build of each month, so pin only a month-end release (here 2026-10-31;
# the laptop ran the 2026-11-16 daily, n9.0.2-57, same release branch).
ARG FFMPEG_RELEASE=autobuild-2026-10-31-12-59
ARG FFMPEG_NAME=ffmpeg-n9.0.2-39-g7adfbe40dc-linux64-gpl-9.0
ARG FFMPEG_SHA256=73e29a56974ae5a651133a04584c7483b21afe941e17dc91d79814a758b608d3
# PhotoCraft is early alpha: exactly v0.23.1, CLI binary only.
ARG PHOTOCRAFT_VERSION=0.23.1
ARG PHOTOCRAFT_SHA256=4383aef67a07cc370ad250a6ed60015205d3e6c463faf3ff4b0f67b8178ccfc8
ADD https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_RELEASE}/${FFMPEG_NAME}.tar.xz /dl/ffmpeg.tar.xz
ADD https://github.com/storytold/photocraft/releases/download/v${PHOTOCRAFT_VERSION}/photocraft-${PHOTOCRAFT_VERSION}-linux-x86_64.tar.gz /dl/photocraft.tar.gz
RUN set -e; cd /dl \
    && echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - \
    && echo "${PHOTOCRAFT_SHA256}  photocraft.tar.gz" | sha256sum -c - \
    && python3 -I -c "import tarfile,sys; \
t=tarfile.open('ffmpeg.tar.xz'); [t.extract(m,'/x',filter='data') for m in t.getmembers() if m.name.endswith(('/bin/ffmpeg','/bin/ffprobe'))]; \
t=tarfile.open('photocraft.tar.gz'); [t.extract(m,'/x',filter='data') for m in t.getmembers() if m.name.endswith('/bin/photocraft-cli')]" \
    && mkdir /out && mv /x/*/bin/ffmpeg /x/*/bin/ffprobe /x/*/bin/photocraft-cli /out/ \
    && chmod 0755 /out/*

FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
COPY --from=fetch /out/ /usr/local/bin/
# flatten.py is bind-mounted by compose.yaml (a code change needs no rebuild).
ENV PYTHONUNBUFFERED=1 HOME=/tmp PHOTOCRAFT_NO_DESKTOP_INTEGRATION=1
USER 3003:3001
ENTRYPOINT ["python3", "-I", "/app/flatten.py"]
