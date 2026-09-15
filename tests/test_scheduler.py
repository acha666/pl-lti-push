from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from pl_lti_push import scheduler
from pl_lti_push.config import Assignment, Config
from pl_lti_push.state import State


def test_scheduler_skips_overlap_capacity_and_disabled(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    a = Assignment("a", "1", "2", "3", ["* * * * *", "0 * * * *"])
    b = replace(a, name="b", assessment_id="4")
    c = replace(a, name="c", assessment_id="5", enabled=False)
    config = Config("https://example.invalid", ZoneInfo("UTC"), (a, b, c), workers=1)
    state = State(tmp_path, config.base_url)
    submitted = []
    events = []
    ticks = 0

    class Clock(datetime):
        @staticmethod
        def now(_):
            return datetime(2026, 9, 12, 0, ticks // 2, tzinfo=UTC)

    class Stop:
        def is_set(self):
            return ticks >= 4

        def wait(self, _):
            nonlocal ticks
            ticks += 1

    class Pool:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def submit(self, function, assignment):
            submitted.append(assignment.name)
            return Future()  # Remain active through the following minute.

    class Runner:
        def run(self, _):
            raise AssertionError("Fake executor does not execute")

    monkeypatch.setattr(scheduler, "datetime", Clock)
    monkeypatch.setattr(scheduler, "HEARTBEAT", tmp_path / "heartbeat")
    monkeypatch.setattr(scheduler, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(scheduler, "event", lambda a, *_args, **_kw: events.append(a.name))
    scheduler.serve(config, Runner(), state, Stop())
    assert submitted == ["a"]
    assert events == ["b", "a", "b"]
    assert state.get(c.key) == {}
    assert (tmp_path / "heartbeat").exists()
    assert caplog.messages == [
        "Next run: a at 2026-09-12T00:01:00+00:00",
        "Next run: a at 2026-09-12T00:02:00+00:00",
    ]


def test_next_run_respects_windows_claims_and_timezone(tmp_path):
    now = datetime.fromisoformat("2026-09-14T15:00:00Z")
    a = Assignment("a", "1", "2", "3", ["0 9 * * *", "30 8 * * *"])
    config = Config("https://example.invalid", ZoneInfo("America/Vancouver"), (a,))
    state = State(tmp_path, config.base_url)
    assert scheduler.next_run(config, state, now) == (
        datetime.fromisoformat("2026-09-14T08:30:00-07:00"),
        "a",
    )
    state.claim(a.key, "2026-09-14T08:30")
    assert scheduler.next_run(config, state, now) == (
        datetime.fromisoformat("2026-09-14T09:00:00-07:00"),
        "a",
    )
    for unavailable in (
        replace(a, enabled=False),
        replace(a, ends_at=datetime.fromisoformat("2026-09-14T16:00:00Z")),
        replace(a, cron=["0 0 31 2 *"]),
    ):
        assert scheduler.next_run(replace(config, assignments=(unavailable,)), state, now) is None
    future = replace(
        a, cron=["* * * * *"], starts_at=datetime.fromisoformat("2026-09-15T16:00:30Z")
    )
    assert scheduler.next_run(replace(config, assignments=(future,)), state, now) == (
        future.starts_at,
        "a",
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_waiting_log_refreshes_at_activation_without_repeating(
    monkeypatch, tmp_path, caplog, enabled
):
    caplog.set_level("INFO")
    start = datetime(2026, 9, 14, 0, 0, 30, tzinfo=UTC)
    assignment = Assignment("a", "1", "2", "3", ["* * * * *"], starts_at=start, enabled=enabled)
    config = Config("https://example.invalid", ZoneInfo("UTC"), (assignment,))
    state = State(tmp_path, config.base_url)
    ticks = 0

    class Clock(datetime):
        @staticmethod
        def now(_):
            return start.replace(second=ticks * 15)

    class Stop:
        def is_set(self):
            return ticks == 4

        def wait(self, _):
            nonlocal ticks
            ticks += 1

    monkeypatch.setattr(scheduler, "datetime", Clock)
    monkeypatch.setattr(scheduler, "HEARTBEAT", tmp_path / "heartbeat")
    scheduler.serve(config, SimpleNamespace(run=lambda _: None), state, Stop())
    assert caplog.messages == (
        [
            "Next run: a at 2026-09-14T00:00:30+00:00",
            "Next run: a at 2026-09-14T00:01:00+00:00",
        ]
        if enabled
        else ["No upcoming scheduled tasks"]
    )


def test_activation_window_boundaries_and_cron():
    a = Assignment(
        "a",
        "1",
        "2",
        "3",
        ["* * * * *"],
        starts_at=datetime.fromisoformat("2026-09-01T07:00:00Z"),
        ends_at=datetime.fromisoformat("2026-09-02T07:00:00Z"),
    )
    for instant, expected in [
        ("2026-09-01T06:59:59Z", False),
        ("2026-09-01T07:00:00Z", True),
        ("2026-09-02T06:59:59Z", True),
        ("2026-09-02T07:00:00Z", False),
    ]:
        assert (
            scheduler.due(a, datetime.fromisoformat(instant), ZoneInfo("America/Vancouver"))[0]
            is expected
        )
    now = datetime.fromisoformat("2026-09-01T08:01:00Z")
    assert not scheduler.due(replace(a, cron=["0 * * * *"]), now, ZoneInfo("UTC"))[0]
    assert replace(a, starts_at=None).within_window(datetime.fromisoformat("2020-01-01T00:00:00Z"))
    assert replace(a, ends_at=None).within_window(datetime.fromisoformat("2030-01-01T00:00:00Z"))


def test_cron_list_matches_any_expression():
    a = Assignment("a", "1", "2", "3", ["0 9 * * 1-5", "30 18 * * *"])
    for instant, expected in [
        ("2026-09-14T09:00:00Z", True),
        ("2026-09-14T18:30:00Z", True),
        ("2026-09-14T10:00:00Z", False),
        ("2026-09-13T09:00:00Z", False),
    ]:
        assert scheduler.due(a, datetime.fromisoformat(instant), ZoneInfo("UTC"))[0] is expected
