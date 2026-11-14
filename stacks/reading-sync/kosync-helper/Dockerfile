FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# kosync-helper (stacks/reading-sync): KOReader Progress sync front. PDFs are synced
# page-for-page with Audiobookshelf; every other document goes to BookBridge.
# Dependencies via uv from the committed uv.lock (owner 2026-11-01: use uv).
COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /usr/local/bin/uv
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
