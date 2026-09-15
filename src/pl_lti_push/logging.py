"""Consistent, single-line operational logs in the configured timezone."""

import logging
from datetime import UTC, datetime

log = logging.getLogger("pl_lti_push")
_CONTROL_ESCAPES = {code: f"\\x{code:02x}" for code in (*range(32), 127)}


class Formatter(logging.Formatter):
    def __init__(self, timezone=UTC):
        super().__init__()
        self.timezone = timezone

    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created, self.timezone).isoformat(
            timespec="seconds"
        )
        scope = str(getattr(record, "assignment", "scheduler")).translate(_CONTROL_ESCAPES)
        message = record.getMessage().translate(_CONTROL_ESCAPES)
        return f"{timestamp} | {record.levelname:<7} | {scope} | {message}"


def configure():
    formatter = Formatter()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    return formatter


def event(assignment, status, *, reason=None, **fields):
    level = {
        "pending": logging.WARNING,
        "partial": logging.ERROR,
        "failed": logging.ERROR,
        "blocked": logging.ERROR,
        "error": logging.ERROR,
    }.get(status, logging.INFO)
    details = [status.upper()]
    if reason:
        details.append(reason)
    details.extend(f"{key}={value}" for key, value in fields.items())
    log.log(level, " | ".join(details), extra={"assignment": assignment.name})
