"""One JSON line per event on stdout (Alloy ships container stdout to Loki).

Rule: weights and impedances appear only in the dry-run `would_write` line;
tokens and noise keys never. Call sites pass counts and names, not values.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
        }
        fields = getattr(record, "fields", None)
        if fields:
            out.update(fields)
        else:
            out["msg"] = record.getMessage()
        if record.exc_info and record.exc_info[0] is not None:
            out["exc"] = record.exc_info[0].__name__
        return json.dumps(out, default=str, separators=(",", ":"))


def emit(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event, extra={"fields": {"event": event, **fields}})


def setup(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.WARNING)          # third-party libraries: warnings and up
    logging.getLogger("scale_bridge").setLevel(getattr(logging, level.upper(), logging.INFO))
    # httpx logs every request URL at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
