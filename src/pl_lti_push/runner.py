"""Submission state machine; never retry a POST with an uncertain outcome."""

import json
import logging
import time
from datetime import UTC, datetime

from .errors import UserError

log = logging.getLogger(__name__)


def event(assignment, status, **fields):
    log.info(
        json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "assignment": assignment.name,
                "status": status,
                **fields,
            }
        )
    )


class Runner:
    def __init__(self, config, client, state, stop):
        self.config, self.client, self.state, self.stop = config, client, state, stop

    def run(self, assignment):
        if self.stop.is_set():
            return False
        key = assignment.key
        try:
            record = self.state.get(key)
            if record.get("status") == "submitting":
                event(
                    assignment,
                    "blocked",
                    reason="Uncertain POST; inspect PL job history then resolve",
                )
                return False
            path = record.get("job_path") if record.get("status") == "pending" else None
            if not path:
                if not assignment.within_window(datetime.now(UTC)):
                    event(
                        assignment,
                        "skipped",
                        reason="Outside assignment activation window",
                    )
                    return False
                csrf = self.client.csrf(assignment)
                if self.stop.is_set():
                    return False
                # Fetching the form may span the end of the activation window.
                if not assignment.within_window(datetime.now(UTC)):
                    event(
                        assignment,
                        "skipped",
                        reason="Outside assignment activation window",
                    )
                    return False
                # Persist BEFORE sending: a crash cannot silently cause a duplicate submission.
                self.state.update(
                    key,
                    status="submitting",
                    job_path=None,
                    counts={},
                    updated_at=datetime.now(UTC).isoformat(),
                )
                path = self.client.submit(assignment, csrf)
                self.state.update(key, status="pending", job_path=path)
                event(assignment, "accepted", job_path=path)
            deadline = time.monotonic() + self.config.job_timeout_seconds
            while not self.stop.is_set():
                status, counts = self.client.poll(path)
                if status != "running":
                    self.state.update(
                        key,
                        status=status,
                        counts=counts,
                        updated_at=datetime.now(UTC).isoformat(),
                    )
                    event(assignment, status, job_path=path, **counts)
                    return status == "success"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    event(
                        assignment,
                        "pending",
                        reason="Polling deadline reached; resume next run",
                    )
                    return False
                self.stop.wait(min(self.config.poll_interval_seconds, remaining))
            return False
        except UserError as exc:
            # Only our fixed diagnostic messages are logged, never response bodies or tokens.
            event(assignment, "error", reason=str(exc))
            return False
        except Exception:
            # Filesystem/configuration failures must not leak credentials through tracebacks.
            event(
                assignment,
                "error",
                reason="Local state/credential failure; check files and access",
            )
            return False
