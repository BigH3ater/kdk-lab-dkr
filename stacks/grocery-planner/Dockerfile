FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# Weekly grocery planner one-shot: logs into Tandoor (session auth, like the
# dashboard mealplan sidecar), generates next week's plan, builds the two-store
# shopping list, and creates a Vikunja approval task. requests + pyyaml only.
# Dependencies via uv from the committed uv.lock (owner 2026-11-01: use uv).
COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /usr/local/bin/uv
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
