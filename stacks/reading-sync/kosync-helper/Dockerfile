FROM python:3.12.16-slim@sha256:5b5fe805d367f8f5a359ded6ab2652b7f9a206453ee6ee789c0045ba6ec15016
# kosync-helper (stacks/reading-sync): KOReader Progress sync front. PDFs are synced
# page-for-page with Audiobookshelf; every other document goes to BookBridge.
# Dependencies via uv from the committed uv.lock (owner 2026-11-01: use uv).
COPY --from=ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock /app/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
RUN uv sync --frozen --no-dev --no-install-project && rm /usr/local/bin/uv
ENV PATH="/app/.venv/bin:$PATH"
COPY kosync_helper/ /app/kosync_helper/
# -I (isolated mode) drops the working dir from sys.path; a .pth file in the venv's
# site-packages is still honoured (same fix as stacks/grocery-planner).
RUN echo /app > /app/.venv/lib/python3.12/site-packages/kdk-kosync-helper.pth \
    && python -I -c "import kosync_helper.app"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Non-root; nothing in the image is writable by this uid (compose adds read_only).
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-I", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["python", "-I", "-m", "kosync_helper"]
