import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pl_lti_push.cli import main
from pl_lti_push.config import load
from pl_lti_push.credentials import Credentials, make_jar
from pl_lti_push.scheduler import due
from pl_lti_push.state import State, exclusive


@pytest.fixture
def config_file(tmp_path):
    data = {
        "base_url": "https://pl.example.invalid",
        "timezone": "America/Vancouver",
        "assignments": [
            dict(
                name="lab",
                course_instance_id="1",
                lti_course_instance_id="2",
                assessment_id="3",
                cron=["30 1 * * *"],
            )
        ],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path, data


def test_config_defaults(config_file):
    config = load(config_file[0])
    assert config.request_timeout_seconds == 30
    assert config.job_timeout_seconds == 600
    assert config.poll_interval_seconds == 5
    assert config.workers == 4


@pytest.mark.parametrize(
    ("field", "upper"),
    [
        ("request_timeout_seconds", 300),
        ("job_timeout_seconds", 86400),
        ("poll_interval_seconds", 300),
        ("workers", 32),
    ],
)
def test_config_numeric_limits(config_file, field, upper):
    path, data = config_file
    for value in (1, upper):
        data[field] = value
        path.write_text(json.dumps(data))
        assert getattr(load(path), field) == value
    for value in (0, upper + 1, True, None, "1", 1.5):
        data[field] = value
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match=f"Invalid {field}"):
            load(path)


@pytest.mark.parametrize(
    "change",
    [
        {"base_url": "https://user:secret@pl.example.invalid"},
        {"base_url": "http://pl.example.invalid"},
        {"base_url": "https://pl.example.invalid/pl"},
        {"workers": 0},
        {"workers": True},
        {"assignments": []},
        {"unexpected": True},
        {"timezone": "not-a-zone"},
    ],
)
def test_bad_config(config_file, change):
    path, data = config_file
    data.update(change)
    path.write_text(json.dumps(data))
    with pytest.raises((ValueError, KeyError)):
        load(path)


@pytest.mark.parametrize(
    "change",
    [
        {"cron": ["* * * * * *"]},
        {"cron": ["99 * * * *"]},
        {"assessment_id": "../3"},
        {"assessment_id": 3},
        {"enabled": "false"},
        {"name": "bad\nname"},
        {"extra": 1},
    ],
)
def test_bad_assignment(config_file, change):
    path, data = config_file
    data["assignments"][0].update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load(path)


def test_duplicate_target_rejected(config_file):
    path, data = config_file
    data["assignments"].append(dict(data["assignments"][0], name="second"))
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load(path)


def test_dst_fold_and_restart_dedup(config_file, tmp_path):
    config = load(config_file[0])
    a = config.assignments[0]
    state = State(tmp_path, config.base_url)
    # Use a past fold; Vancouver no longer changes clocks after March 2026.
    for timestamp, expected in [
        ("2025-11-02T08:30:00+00:00", True),
        ("2025-11-02T09:30:00+00:00", False),
    ]:
        matched, slot = due(a, datetime.fromisoformat(timestamp), config.timezone)
        assert matched
        assert state.claim(a.key, slot) is expected
        state = State(tmp_path, config.base_url)


def test_cron_dom_dow_or(config_file):
    path, data = config_file
    data["assignments"][0]["cron"] = ["0 0 1 * MON"]
    path.write_text(json.dumps(data))
    a = load(path).assignments[0]
    assert due(a, datetime.fromisoformat("2026-09-07T00:00:00+00:00"), ZoneInfo("UTC"))[0]


def test_exclusive_state_and_origin(tmp_path):
    tmp_path.chmod(0o700)
    with exclusive(tmp_path):
        with pytest.raises(ValueError):
            with exclusive(tmp_path):
                pass
    state = State(tmp_path, "https://one.example.invalid")
    state.update("key", status="submitting")
    with pytest.raises(ValueError):
        State(tmp_path, "https://two.example.invalid")


def test_cookie_import_scope_expiry_and_permissions(tmp_path):
    origin = "https://pl.example.invalid"
    cookies = [
        dict(name="pl2_session", value="SECRET", domain=".example.invalid", expires=-1),
        dict(name="pl2_session", value="WRONG", domain="elsewhere.invalid"),
        dict(name="pl2_session", value="OLD", domain="pl.example.invalid", expires=1),
        dict(name="pl2_access_as_administrator", value="true", domain=".example.invalid"),
        dict(name="prairielearn_session", value="LEGACY", domain="pl.example.invalid"),
    ]
    source = tmp_path / "browser.json"
    source.write_text(json.dumps({"cookies": cookies, "origins": [{"localStorage": ["private"]}]}))
    creds = Credentials(tmp_path / "cookies.json", origin)
    creds.import_file(source)
    assert len(creds.load()) == 1
    assert creds.load().get("pl2_session") == "SECRET"
    assert "localStorage" not in creds.path.read_text()
    jar = creds.load()
    jar.set("prairielearn_session", "LEGACY", domain="pl.example.invalid", path="/")
    creds.save(jar)
    assert [c["name"] for c in json.loads(creds.path.read_text())["cookies"]] == ["pl2_session"]
    with pytest.raises(ValueError):
        Credentials(creds.path, "https://other.example.invalid").load()
    creds.path.chmod(0o644)
    with pytest.raises(ValueError):
        creds.load()
    with pytest.raises(ValueError):
        make_jar(cookies[1:], origin)


def test_cli_interactive_cookie_import(config_file, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("pl_lti_push.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("pl_lti_push.cli.getpass.getpass", lambda _: "SECRET==")
    directory = tmp_path / "state"
    args = ["--config", str(config_file[0]), "--state", str(directory)]
    assert main(args + ["import-cookies"]) == 0
    creds = Credentials(directory / "cookies.json", "https://pl.example.invalid")
    cookie = next(iter(creds.load()))
    assert (cookie.name, cookie.value) == ("pl2_session", "SECRET==")
    assert (cookie.domain, cookie.path, cookie.secure, cookie.expires) == (
        "pl.example.invalid",
        "/",
        True,
        None,
    )
    assert creds.path.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err
    assert "check-auth" in output.out


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "SECRET; other=x",
        "SECRET\nx",
        "SECRET x",
        'SECRET"',
        "SECRET\\",
    ],
)
def test_invalid_paste_preserves_credentials(tmp_path, value):
    creds = Credentials(tmp_path / "cookies.json", "https://pl.example.invalid")
    creds.import_session("original")
    original = creds.path.read_bytes()
    with pytest.raises(ValueError, match="Paste only the pl2_session cookie value"):
        creds.import_session(value)
    assert creds.path.read_bytes() == original


@pytest.mark.parametrize("failure", [EOFError, KeyboardInterrupt])
def test_cancelled_paste(config_file, tmp_path, monkeypatch, caplog, failure):
    monkeypatch.setattr("pl_lti_push.cli.sys.stdin.isatty", lambda: True)

    def cancel(_):
        raise failure

    monkeypatch.setattr("pl_lti_push.cli.getpass.getpass", cancel)
    directory = tmp_path / "state"
    assert main(["--config", str(config_file[0]), "--state", str(directory), "import-cookies"]) == 1
    assert "cancelled" in caplog.text
    assert not (directory / "cookies.json").exists()


def test_paste_requires_terminal(config_file, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("pl_lti_push.cli.sys.stdin.isatty", lambda: False)
    assert main(["--config", str(config_file[0]), "--state", str(tmp_path), "import-cookies"]) == 1
    assert "without -T" in caplog.text


def test_missing_credentials_has_setup_instruction(tmp_path):
    with pytest.raises(ValueError, match="docker compose run --rm scheduler import-cookies"):
        Credentials(tmp_path / "cookies.json", "https://pl.example.invalid").load()


def test_cli_offline_validation_recovery(config_file, tmp_path, capsys):
    path, _ = config_file
    directory = tmp_path / "state"
    args = ["--config", str(path), "--state", str(directory)]
    assert main(args + ["validate"]) == 0
    assert not directory.exists()
    assert main(args + ["status"]) == 0
    directory.mkdir(mode=0o700)
    state = State(directory, load(path).base_url)
    state.update("1/2/3", status="submitting")
    assert main(args + ["resolve", "lab", "--job-id", "42"]) == 0
    assert State(directory, load(path).base_url).get("1/2/3")["job_path"].endswith("/42")
    assert main(args + ["resolve", "lab", "--clear"]) == 0
    assert "SECRET" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "change",
    [
        {"starts_at": "2026-09-01T00:00:00"},
        {"ends_at": "2026-12-20"},
        {"starts_at": 123},
        {"ends_at": "invalid"},
        {"starts_at": "2026-09-01T07:00:00Z", "ends_at": "2026-09-01T00:00:00-07:00"},
        {"starts_at": "2026-09-02T00:00:00Z", "ends_at": "2026-09-01T00:00:00Z"},
    ],
)
def test_invalid_activation_window(config_file, change):
    path, data = config_file
    data["assignments"][0].update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load(path)


def test_activation_timestamps_normalized(config_file):
    path, data = config_file
    data["assignments"][0].update(starts_at="2026-09-01T00:00:00-07:00", ends_at=None)
    path.write_text(json.dumps(data))
    a = load(path).assignments[0]
    assert a.starts_at == datetime.fromisoformat("2026-09-01T07:00:00Z")
    assert a.ends_at is None


@pytest.mark.parametrize(
    "cron",
    [
        "* * * * *",
        [],
        None,
        [123],
        ["* * * * *", "invalid"],
        [["* * * * *"]],
    ],
)
def test_invalid_cron_list(config_file, cron):
    path, data = config_file
    data["assignments"][0]["cron"] = cron
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load(path)


def test_multiple_cron_expressions(config_file):
    path, data = config_file
    expressions = ["0 9 * * 1-5", "30 18 * * *"]
    data["assignments"][0]["cron"] = expressions
    path.write_text(json.dumps(data))
    assert load(path).assignments[0].cron == expressions


@pytest.mark.parametrize("operation", ["update", "claim"])
@pytest.mark.parametrize("after_replace", [False, True])
def test_state_write_failure_blocks_further_use(tmp_path, monkeypatch, operation, after_replace):
    from pl_lti_push import state as state_module

    state = State(tmp_path, "https://pl.example.invalid")
    state.update("key", status="pending", slot="2026-09-12T01:00")
    original = json.loads(state.path.read_text())
    write = state_module.atomic_json

    def fail(path, data):
        if after_replace:
            write(path, data)
        raise OSError("Simulated storage failure")

    monkeypatch.setattr(state_module, "atomic_json", fail)
    with pytest.raises(OSError):
        if operation == "update":
            state.update("key", status="success")
        else:
            state.claim("key", "2026-09-12T01:01")
    assert state.data == original
    if not after_replace:
        assert json.loads(state.path.read_text()) == original
    for action in (
        lambda: state.get("key"),
        lambda: state.update("key", status="submitting"),
        lambda: state.claim("key", "2026-09-12T01:02"),
    ):
        with pytest.raises(ValueError, match="repair storage and restart"):
            action()


@pytest.mark.parametrize("expiry", [0, -2, 1])
def test_expired_cookie_is_not_imported(expiry):
    with pytest.raises(ValueError, match="No unexpired"):
        make_jar(
            [
                dict(
                    name="pl2_session", value="expired", domain="pl.example.invalid", expires=expiry
                )
            ],
            "https://pl.example.invalid",
        )


@pytest.mark.parametrize("expiry", [True, "0", float("nan"), float("inf")])
def test_invalid_cookie_expiry_rejected(expiry):
    with pytest.raises(ValueError, match="Invalid cookie expiry"):
        make_jar(
            [
                dict(
                    name="pl2_session", value="invalid", domain="pl.example.invalid", expires=expiry
                )
            ],
            "https://pl.example.invalid",
        )


@pytest.mark.parametrize("expiry", [None, -1, 4102444800])
def test_session_and_future_cookie_expiry_preserved(expiry):
    jar = make_jar(
        [dict(name="pl2_session", value="valid", domain="pl.example.invalid", expires=expiry)],
        "https://pl.example.invalid",
    )
    assert next(iter(jar)).expires == (None if expiry in (None, -1) else expiry)
