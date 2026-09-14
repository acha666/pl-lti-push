"""Operational commands; validation, imports and recovery are entirely offline."""

import argparse
import getpass
import json
import logging
import os
import signal
import sys
import threading
import warnings
from pathlib import Path

from .client import Client
from .config import load
from .credentials import Credentials
from .errors import UserError
from .runner import Runner
from .scheduler import serve
from .state import State, exclusive


def resolve_submission(state, assignment, job_id):
    if state.get(assignment.key).get("status") not in ("submitting", "pending"):
        raise UserError("Assignment has no unresolved submission")
    path = None
    if job_id:
        if not job_id.isascii() or not job_id.isdecimal() or int(job_id) < 1:
            raise UserError("Invalid job ID")
        path = (
            f"/pl/course_instance/{assignment.course_instance_id}/instructor/jobSequence/{job_id}"
        )
    state.update(assignment.key, status="pending" if path else "resolved", job_path=path)


def prompt_session(origin):
    if not sys.stdin.isatty():
        raise UserError(
            "Interactive import needs a terminal. Run docker compose run --rm scheduler "
            "import-cookies without -T, or supply a JSON export file."
        )
    print(f"Log in to {origin} in your browser.")
    print("Open developer tools > Application/Storage > Cookies and select that site.")
    print("Copy the pl2_session cookie value and paste it below.")
    try:
        with warnings.catch_warnings():
            # Never fall back to echoing a session secret when terminal setup fails.
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass("Session cookie value: ")
    except (EOFError, KeyboardInterrupt):
        raise UserError("Cookie import cancelled. Existing credentials were not changed.") from None
    except getpass.GetPassWarning:
        raise UserError(
            "Cannot hide input in this terminal. Use a JSON export file instead."
        ) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/config/config.json"))
    parser.add_argument("--state", type=Path, default=Path("/state"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate configuration offline")
    commands.add_parser("status", help="Show persisted run state offline")
    commands.add_parser("serve", help="Run the cron scheduler")
    commands.add_parser(
        "check-auth", help="GET each enabled assignment form; does not submit grades"
    )
    imp = commands.add_parser(
        "import-cookies", help="Paste a session cookie or import JSON offline"
    )
    imp.add_argument(
        "file", type=Path, nargs="?", help="JSON export; omit to paste a session cookie"
    )
    run = commands.add_parser("run", help="Run or resume one assignment immediately")
    run.add_argument("assignment")
    resolve = commands.add_parser(
        "resolve", help="Resolve an uncertain submission after manual review"
    )
    resolve.add_argument("assignment")
    group = resolve.add_mutually_exclusive_group(required=True)
    group.add_argument("--job-id", help="Attach a confirmed existing job to resume polling")
    group.add_argument(
        "--clear", action="store_true", help="Permit a future submission after review"
    )
    args = parser.parse_args(argv)
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        config = load(args.config)
        if args.command == "validate":
            print(f"Valid: {len(config.assignments)} assignments; timezone={config.timezone}")
            return 0
        if args.command == "status":
            print(json.dumps(State(args.state, config.base_url).data, indent=2))
            return 0
        with exclusive(args.state):
            state = State(args.state, config.base_url)
            if args.command in ("run", "resolve"):
                assignment = next(
                    (a for a in config.assignments if a.name == args.assignment), None
                )
                if assignment is None:
                    raise UserError("Unknown assignment")
            if args.command == "resolve":
                resolve_submission(state, assignment, args.job_id)
                print("Recovery state saved. No network requests made.")
                return 0
            credentials = Credentials(args.state / "cookies.json", config.base_url)
            if args.command == "import-cookies":
                if args.file is None:
                    credentials.import_session(prompt_session(config.base_url))
                else:
                    credentials.import_file(args.file)
                print("Session cookies saved. No network requests made.")
                print(
                    "Next: run check-auth (Docker: docker compose run --rm scheduler check-auth)."
                )
                return 0
            credentials.load()
            stop = threading.Event()
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: stop.set())
            client = Client(config, credentials)
            with client.session:
                if args.command == "check-auth":
                    for a in config.assignments:
                        if a.enabled:
                            client.csrf(a)
                    print("Enabled assignment forms are accessible.")
                    return 0
                runner = Runner(config, client, state, stop)
                if args.command == "run":
                    return 0 if runner.run(assignment) else 1
                serve(config, runner, state, stop)
                return 0
    except UserError as exc:
        logging.error("%s", exc)
        return 1
    except Exception:
        # Config/cookie parsing errors can contain secrets. Keep CLI failures sanitized.
        logging.error(
            "Command failed. Check config, credentials, state permissions and access; "
            "another process may hold the state lock."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
