import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import pytest

from pl_lti_push.client import Client
from pl_lti_push.config import Assignment, Config
from pl_lti_push.credentials import Credentials, make_jar
from pl_lti_push.html import Page, ProtocolError
from pl_lti_push.runner import Runner
from pl_lti_push.state import State

A = Assignment("lab", "100", "200", "300", ["* * * * *"])
FORM = """<form method="POST"><input name="__csrf_token" value="fresh-token">
<input name="unsafe_assessment_id" value="300">
<button name="__action" value="send_grades">Send grades</button></form>"""
PATH = "/pl/course_instance/100/instructor/jobSequence/9"


def job(status="Success", errors=0, skipped=2):
    return (
        '<script type="application/json" data-component="JobSequenceResults" '
        'data-component-props="true">'
        + json.dumps(
            {
                "json": {
                    "jobSequence": {"id": "9", "status": status},
                    "jobs": [
                        {
                            "status": status,
                            "outputHtml": (
                                "7 scores successfully sent.\n"
                                f"{errors} scores not sent due to errors.\n"
                                f"{skipped} scores skipped (not sent).\n"
                            ),
                        }
                    ],
                }
            }
        )
        + "</script>"
    )


@pytest.fixture
def system(tmp_path):
    seen = {"posts": 0, "gets": 0, "errors": 0, "status": "Success", "mode": "normal"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, body="", headers=None):
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            seen["gets"] += 1
            assert self.headers.get("Cookie") == "pl2_session=" + (
                "initial" if seen["gets"] == 1 else "rotated"
            )
            if seen["mode"] == "login":
                self.respond(302, headers={"Location": "https://login.example.invalid/"})
            elif self.path == A.page:
                self.respond(200, FORM, {"Set-Cookie": "pl2_session=rotated; Path=/; HttpOnly"})
            else:
                assert self.path == PATH
                self.respond(200, job(seen["status"], seen["errors"]))

        def do_POST(self):
            assert self.path == A.page
            assert self.headers["Cookie"] == "pl2_session=rotated"
            data = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            assert data == {
                "__csrf_token": ["fresh-token"],
                "unsafe_assessment_id": ["300"],
                "__action": ["send_grades"],
            }
            seen["posts"] += 1
            if seen["mode"] == "disconnect":
                self.connection.close()
                return
            target = "https://evil.example.invalid/" if seen["mode"] == "evil" else PATH
            self.respond(302, headers={"Location": target})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = Config(
        f"http://127.0.0.1:{server.server_port}",
        ZoneInfo("UTC"),
        (A,),
        job_timeout_seconds=1,
        poll_interval_seconds=1,
    )
    creds = Credentials(tmp_path / "cookies.json", config.base_url)
    creds.save(
        make_jar(
            [dict(name="pl2_session", value="initial", domain="127.0.0.1", path="/", secure=False)],
            config.base_url,
        )
    )
    state = State(tmp_path, config.base_url)
    client = Client(config, creds)
    runner = Runner(config, client, state, threading.Event())
    yield runner, seen, creds, tmp_path
    client.session.close()
    server.shutdown()
    server.server_close()
    thread.join()


def test_complete_http_flow_and_cookie_persistence(system):
    runner, seen, creds, _ = system
    assert runner.run(A)
    assert seen["posts"] == 1
    assert creds.load().get("pl2_session") == "rotated"
    assert creds.path.stat().st_mode & 0o777 == 0o600
    assert runner.state.get(A.key)["counts"] == {"sent": 7, "errors": 0, "skipped": 2}


@pytest.mark.parametrize(
    "status, errors, expected",
    [
        ("Success", 3, "partial"),
        ("Error", 0, "failed"),
    ],
)
def test_server_and_partial_failure(system, status, errors, expected):
    runner, seen, _, _ = system
    seen.update(status=status, errors=errors)
    assert not runner.run(A)
    assert runner.state.get(A.key)["status"] == expected


@pytest.mark.parametrize("mode", ["disconnect", "evil"])
def test_uncertain_post_never_retried_after_restart(system, mode):
    runner, seen, _, directory = system
    seen["mode"] = mode
    assert not runner.run(A)
    assert runner.state.get(A.key)["status"] == "submitting"
    runner.state = State(directory, runner.config.base_url)
    assert not runner.run(A)
    assert seen["posts"] == 1


def test_auth_redirect_is_not_followed(system):
    runner, seen, _, _ = system
    seen["mode"] = "login"
    assert not runner.run(A)
    assert seen["gets"] == 1
    assert seen["posts"] == 0
    assert runner.state.get(A.key) == {}


def test_pending_resumes_without_post_after_restart(system, caplog):
    caplog.set_level("INFO", logger="pl_lti_push")
    runner, seen, _, directory = system
    seen["status"] = "Running"
    assert not runner.run(A)
    assert runner.state.get(A.key)["status"] == "pending"
    seen["status"] = "Success"
    runner.state = State(directory, runner.config.base_url)
    from datetime import UTC, datetime, timedelta

    assert runner.run(replace(A, ends_at=datetime.now(UTC) - timedelta(days=1)))
    assert seen["posts"] == 1
    assert caplog.messages == [
        f"ACCEPTED | job_path={PATH}",
        f"PENDING | Polling deadline reached; resume next run | job_path={PATH}",
        f"RESUMING | job_path={PATH}",
        f"SUCCESS | job_path={PATH} | sent=7 | errors=0 | skipped=2",
    ]


def test_shutdown_leaves_resumable_job(system):
    runner, seen, _, _ = system
    seen["status"] = "Running"
    timer = threading.Timer(0.2, runner.stop.set)
    timer.start()
    assert not runner.run(A)
    timer.join()
    assert runner.state.get(A.key)["status"] == "pending"


def test_form_is_scoped_to_enabled_assignment():
    assert Page(FORM).csrf(A) == "fresh-token"
    for html in (
        FORM.replace("send_grades", "delete"),
        FORM.replace("<button", "<button disabled"),
        FORM + FORM,
        FORM.replace('method="POST"', 'method="GET"'),
    ):
        with pytest.raises(ProtocolError):
            Page(html).csrf(A)
    with pytest.raises(ProtocolError):
        Page(FORM).csrf(replace(A, assessment_id="999"))


def test_unknown_job_data_fails_closed():
    for html in ("login", job().replace('"9"', '"10"'), job().replace("successfully", "totally")):
        with pytest.raises(ProtocolError):
            Page(html).job("9")


def test_manual_run_outside_window_does_not_request(system):
    from datetime import UTC, datetime, timedelta

    runner, seen, _, _ = system
    now = datetime.now(UTC)
    for a in (
        replace(A, starts_at=now + timedelta(days=1)),
        replace(A, ends_at=now - timedelta(days=1)),
    ):
        assert not runner.run(a)
    assert seen["gets"] == seen["posts"] == 0
    assert runner.state.get(A.key) == {}


def test_window_expires_during_csrf_fetch(system, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from pl_lti_push import runner as runner_module

    runner, seen, _, _ = system
    now = datetime.now(UTC)
    end = now + timedelta(hours=1)
    current = now

    class Clock:
        @staticmethod
        def now(_):
            return current

    original = runner.client.csrf

    def csrf(a):
        nonlocal current
        result = original(a)
        current = end
        return result

    monkeypatch.setattr(runner_module, "datetime", Clock)
    monkeypatch.setattr(runner.client, "csrf", csrf)
    assert not runner.run(replace(A, ends_at=end))
    assert seen["gets"] == 1
    assert seen["posts"] == 0
    assert runner.state.get(A.key) == {}


def test_failed_result_write_never_causes_another_post(system, monkeypatch):
    from pl_lti_push import state as state_module

    runner, seen, _, directory = system
    write = state_module.atomic_json

    def fail_result(path, data):
        if data["assignments"][A.key]["status"] == "success":
            raise OSError("Simulated disk full")
        write(path, data)

    monkeypatch.setattr(state_module, "atomic_json", fail_result)
    assert not runner.run(A)
    assert not runner.run(A)
    assert seen["posts"] == 1
    monkeypatch.setattr(state_module, "atomic_json", write)
    runner.state = State(directory, runner.config.base_url)
    assert runner.run(A)
    assert seen["posts"] == 1
