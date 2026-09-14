"""Strict, offline configuration validation."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from croniter import croniter

from .errors import UserError


@dataclass(frozen=True)
class Assignment:
    name: str
    course_instance_id: str
    lti_course_instance_id: str
    assessment_id: str
    cron: list[str]
    enabled: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    def within_window(self, now: datetime) -> bool:
        return (self.starts_at is None or now >= self.starts_at) and (
            self.ends_at is None or now < self.ends_at
        )

    @property
    def page(self):
        return (
            f"/pl/course_instance/{self.course_instance_id}/instructor/"
            f"instance_admin/lti13_instance/{self.lti_course_instance_id}"
        )

    @property
    def key(self):
        return f"{self.course_instance_id}/{self.lti_course_instance_id}/{self.assessment_id}"


@dataclass(frozen=True)
class Config:
    base_url: str
    timezone: ZoneInfo
    assignments: tuple[Assignment, ...]
    request_timeout_seconds: int = 30
    job_timeout_seconds: int = 600
    poll_interval_seconds: int = 5
    workers: int = 4


def only_keys(data, allowed):
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise UserError("Unknown configuration keys or invalid object")


def parse_time(value, field):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(UTC)
    except ValueError:
        raise UserError(
            f"{field} must be an ISO 8601 timestamp with timezone offset or Z"
        ) from None


def load(path: Path) -> Config:
    data = json.loads(path.read_text())
    only_keys(data, Config.__dataclass_fields__)
    url = data["base_url"]
    u = urlsplit(url)
    if (
        u.scheme not in ("https", "http")
        or not u.hostname
        or u.username
        or u.password
        or u.path not in ("", "/")
        or u.query
        or u.fragment
    ):
        raise UserError("base_url must be an HTTP(S) origin without credentials or path")
    # Plain HTTP is reserved for the local mock; deployed credentials require TLS.
    if u.scheme == "http" and u.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise UserError("Non-loopback origins require HTTPS")
    zone = ZoneInfo(data.get("timezone", "UTC"))
    assignments = []
    names, keys = set(), set()
    for item in data["assignments"]:
        only_keys(item, Assignment.__dataclass_fields__)
        fields = dict(item)
        for field in ("starts_at", "ends_at"):
            fields[field] = parse_time(item.get(field), field)
        a = Assignment(**fields)
        if a.starts_at is not None and a.ends_at is not None and a.starts_at >= a.ends_at:
            raise UserError("starts_at must be earlier than ends_at")
        if not isinstance(a.name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", a.name):
            raise UserError("Assignment names must use letters, digits, underscores or hyphens")
        for value in (a.course_instance_id, a.lti_course_instance_id, a.assessment_id):
            if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
                raise UserError("IDs must be positive decimal strings")
        if not isinstance(a.cron, list) or not a.cron:
            raise UserError("cron must be a non-empty list of five-field expressions")
        for expression in a.cron:
            if (
                not isinstance(expression, str)
                or len(expression.split()) != 5
                or not croniter.is_valid(expression)
            ):
                raise UserError("Each cron entry must be a valid five-field expression")
        if type(a.enabled) is not bool or a.name in names or a.key in keys:
            raise UserError("Invalid enabled flag or duplicate assignment")
        names.add(a.name)
        keys.add(a.key)
        assignments.append(a)
    if not assignments:
        raise UserError("At least one assignment is required")
    data.update(base_url=url.rstrip("/"), timezone=zone, assignments=tuple(assignments))
    config = Config(**data)
    for field, upper in [
        ("request_timeout_seconds", 300),
        ("job_timeout_seconds", 86400),
        ("poll_interval_seconds", 300),
        ("workers", 32),
    ]:
        value = getattr(config, field)
        if type(value) is not int or not 1 <= value <= upper:
            raise UserError(f"Invalid {field}")
    return config
