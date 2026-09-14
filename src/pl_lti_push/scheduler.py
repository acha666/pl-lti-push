"""Minute-resolution cron scheduling, bounded concurrency, no catch-up queue."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from croniter import CroniterBadDateError, croniter

from .runner import event

HEARTBEAT = Path("/tmp/pl-lti-push-heartbeat")


def next_run(config, state, now):
    candidates = []
    for assignment in config.assignments:
        if not assignment.enabled or (assignment.ends_at is not None and now >= assignment.ends_at):
            continue
        earliest = max(now, assignment.starts_at or now).astimezone(config.timezone)
        slot = state.get(assignment.key).get("slot")
        if slot:
            after_slot = datetime.fromisoformat(slot).replace(tzinfo=config.timezone)
            earliest = max(earliest, after_slot + timedelta(minutes=1))
        base = earliest.replace(second=0, microsecond=0) - timedelta(microseconds=1)
        for expression in assignment.cron:
            try:
                scheduled = croniter(expression, base).get_next(datetime)
            except CroniterBadDateError:
                continue
            scheduled = max(scheduled, earliest)
            if assignment.within_window(scheduled):
                candidates.append((scheduled, assignment.name))
    return min(candidates, key=lambda item: (item[0].timestamp(), item[1]), default=None)


def due(assignment, now, timezone):
    local = now.astimezone(timezone)
    matched = assignment.within_window(now) and any(
        croniter.match(expression, local) for expression in assignment.cron
    )
    return matched, local.strftime("%Y-%m-%dT%H:%M")


def serve(config, runner, state, stop):
    active = {}
    next_log_at = datetime.min.replace(tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=config.workers) as pool:
        while not stop.is_set():
            now = datetime.now(UTC)
            active = {key: future for key, future in active.items() if not future.done()}
            for assignment in config.assignments:
                if not assignment.enabled:
                    continue
                matched, slot = due(assignment, now, config.timezone)
                if not matched or not state.claim(assignment.key, slot):
                    continue
                if assignment.key in active or len(active) >= config.workers:
                    event(
                        assignment,
                        "skipped",
                        reason="Worker capacity or previous run still active",
                    )
                    continue
                active[assignment.key] = pool.submit(runner.run, assignment)
            if now >= next_log_at:
                upcoming = next_run(config, state, now)
                if upcoming:
                    next_log_at, name = upcoming
                    logging.info(
                        "Waiting; next scheduled task: %s at %s", name, next_log_at.isoformat()
                    )
                else:
                    next_log_at = datetime.max.replace(tzinfo=UTC)
                    logging.info("Waiting; no upcoming scheduled tasks")
            HEARTBEAT.touch()
            stop.wait(1)
