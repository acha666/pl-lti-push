from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pl_lti_push import scheduler
from pl_lti_push.config import Assignment, Config
from pl_lti_push.state import State


def test_scheduler_skips_overlap_capacity_and_disabled(monkeypatch, tmp_path):
    a = Assignment("a", "1", "2", "3", ["* * * * *", "0 * * * *"])
    b = replace(a, name="b", assessment_id="4")
    c = replace(a, name="c", assessment_id="5", enabled=False)
    config = Config("https://example.invalid", ZoneInfo("UTC"), (a, b, c), workers=1)
    state = State(tmp_path, config.base_url)
    submitted = []
    events = []
    ticks = 0

    class Clock:
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
    monkeypatch.setattr(scheduler, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(scheduler, "event", lambda a, *_args, **_kw: events.append(a.name))
    scheduler.serve(config, Runner(), state, Stop())
    assert submitted == ["a"]
    assert events == ["b", "a", "b"]
    assert state.get(c.key) == {}


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
