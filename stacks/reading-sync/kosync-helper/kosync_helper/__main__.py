"""Entry point: python -I -m kosync_helper (uvicorn on :8080, no access log)."""
import logging
import sys

import uvicorn

from .app import JsonFormatter, create_app
from .config import from_env


def main() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("kosync_helper")
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    # The access log would print request paths (document hashes) and client addresses.
    uvicorn.run(create_app(from_env()), host="0.0.0.0", port=8080, access_log=False,
                proxy_headers=False, server_header=False, log_level="warning")


if __name__ == "__main__":
    main()
