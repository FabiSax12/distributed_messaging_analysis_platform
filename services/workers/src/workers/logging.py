"""JSON logging to stdout, same format as the coordinator
(`coordinator/src/coordinator/logging.py`): timestamp, level, logger, message
and any `extra={...}` (case_id, subtask_id, queue) as-is, for filtering in
journalctl or CloudWatch Insights. A small copy instead of an import so the
coordinator package does not have to be installed on the worker machines.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    # boto3/urllib3 at INFO flood the log with every request.
    logging.getLogger("botocore").setLevel(logging.WARNING)
