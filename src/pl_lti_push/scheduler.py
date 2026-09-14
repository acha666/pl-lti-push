"""Minute-resolution cron scheduling, bounded concurrency, no catch-up queue."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from croniter import croniter

from .runner import event


def due(assignment, now, timezone):
    local = now.astimezone(timezone)
    matched = assignment.within_window(now) and any(
        croniter.match(expression, local) for expression in assignment.cron
    )
    return matched, local.strftime("%Y-%m-%dT%H:%M")


def serve(config, runner, state, stop):
    active = {}
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
            stop.wait(1)
