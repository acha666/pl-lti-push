import logging
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from pl_lti_push.logging import Formatter, event


@pytest.mark.parametrize(
    "instant, expected",
    [
        ("2026-09-14T17:00:01.425205+00:00", "2026-09-14T10:00:01-07:00"),
        ("2026-12-14T17:00:01+00:00", "2026-12-14T09:00:01-08:00"),
    ],
)
def test_log_timestamp_uses_configured_timezone_and_dst(instant, expected):
    record = logging.makeLogRecord(
        {
            "created": datetime.fromisoformat(instant).timestamp(),
            "levelname": "INFO",
            "msg": "Next run: %s",
            "args": ("WS1",),
        }
    )
    assert Formatter(ZoneInfo("America/Vancouver")).format(record) == (
        f"{expected} | INFO    | scheduler | Next run: WS1"
    )


def test_log_escapes_control_characters_and_omits_exception_details():
    record = logging.makeLogRecord(
        {
            "created": 0,
            "levelname": "ERROR",
            "assignment": "WS1\nforged",
            "msg": "Failure\r\n\x1b[31m",
            "exc_info": (ValueError, ValueError("SECRET"), None),
        }
    )
    assert Formatter().format(record) == (
        "1970-01-01T00:00:00+00:00 | ERROR   | WS1\\x0aforged | Failure\\x0d\\x0a\\x1b[31m"
    )


@pytest.mark.parametrize(
    "status, level",
    [
        ("accepted", "INFO"),
        ("resuming", "INFO"),
        ("success", "INFO"),
        ("skipped", "INFO"),
        ("pending", "WARNING"),
        ("partial", "ERROR"),
        ("failed", "ERROR"),
        ("blocked", "ERROR"),
        ("error", "ERROR"),
    ],
)
def test_event_severity_and_context(caplog, status, level):
    caplog.set_level(logging.INFO)
    event(SimpleNamespace(name="WS1"), status, job_path="/job/1", sent=10, errors=0, skipped=3)
    record = caplog.records[-1]
    record.created = 0
    assert Formatter().format(record) == (
        f"1970-01-01T00:00:00+00:00 | {level:<7} | WS1 | {status.upper()}"
        " | job_path=/job/1 | sent=10 | errors=0 | skipped=3"
    )
