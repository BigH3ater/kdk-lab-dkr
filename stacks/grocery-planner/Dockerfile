FROM python:3.12.16-slim@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07
# Weekly grocery planner one-shot: logs into Tandoor (session auth, like the
# dashboard mealplan sidecar), generates next week's plan, builds the two-store
# shopping list, and creates a Vikunja approval task. requests + pyyaml only.
RUN pip install --no-cache-dir requests==2.32.3 pyyaml==6.0.2
WORKDIR /app
COPY grocery/ /app/grocery/
COPY config.yaml /app/config.yaml
COPY recipes/ /app/recipes/
ENV PYTHONUNBUFFERED=1
# -I: isolated mode so CWD cannot shadow stdlib imports. The module to run is the
# compose `command` (grocery.run for the job; grocery.seed --apply for the owner
# seed via `docker compose run`).
ENTRYPOINT ["python", "-I", "-m"]
CMD ["grocery.run"]
