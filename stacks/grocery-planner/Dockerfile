FROM python:3.12.16-slim@sha256:5b5fe805d367f8f5a359ded6ab2652b7f9a206453ee6ee789c0045ba6ec15016
# Weekly grocery planner one-shot: logs into Tandoor (session auth, like the
# dashboard mealplan sidecar), generates next week's plan, builds the two-store
# shopping list, and creates a Vikunja approval task. requests + pyyaml only.
# Dependencies via uv from the committed uv.lock (owner 2026-11-01: use uv).
COPY --from=ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock /app/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
COPY grocery/ /app/grocery/
COPY config.yaml /app/config.yaml
COPY recipes/ /app/recipes/
# -I (isolated mode, below) drops the working dir AND PYTHONPATH from sys.path,
# so `python -I -m grocery.run` could not find /app/grocery (2026-11-01: the first
# weekly run and the owner's seed both exited with "No module named grocery").
# A .pth file in the venv's site-packages is still honoured under -I.
RUN echo /app > /app/.venv/lib/python3.12/site-packages/kdk-grocery-app.pth
ENV PYTHONUNBUFFERED=1
# -I: isolated mode so CWD cannot shadow stdlib imports. The module to run is the
# compose `command` (grocery.run for the job; grocery.seed --apply for the owner
# seed via `docker compose run`).
ENTRYPOINT ["python", "-I", "-m"]
CMD ["grocery.run"]
