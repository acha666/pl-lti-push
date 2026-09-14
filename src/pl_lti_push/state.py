"""Durable per-target state with atomic writes and a process-wide volume lock."""

import fcntl
import json
import threading
from contextlib import contextmanager

from .credentials import atomic_json
from .errors import UserError


@contextmanager
def exclusive(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_mode & 0o077:
        raise UserError("State directory must have permissions 0700")
    with (directory / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UserError("Another process is using this state directory") from None
        yield


class State:
    def __init__(self, directory, origin):
        self.path = directory / "runs.json"
        self.lock = threading.Lock()
        self.write_failed = False
        self.data = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {
                "origin": origin,
                "assignments": {},
            }
        )
        if self.data["origin"] != origin:
            raise UserError("State belongs to another origin; use a separate state directory")

    def get(self, key):
        with self.lock:
            return self._record(key)

    def _record(self, key):
        if self.write_failed:
            raise UserError("State write failed; repair storage and restart before continuing")
        return self.data["assignments"].get(key, {}).copy()

    def _write(self, key, record):
        data = {**self.data, "assignments": {**self.data["assignments"], key: record}}
        try:
            atomic_json(self.path, data)
        except Exception:
            # A failure may occur after replace but before directory fsync. Neither
            # the old nor new snapshot is safe to use for further submissions.
            self.write_failed = True
            raise
        self.data = data

    def update(self, key, **fields):
        with self.lock:
            record = self._record(key)
            record.update(fields)
            self._write(key, record)

    def claim(self, key, slot):
        with self.lock:
            record = self._record(key)
            # Wall-time high water mark also prevents reruns during the DST fold.
            if record.get("slot", "") >= slot:
                return False
            record["slot"] = slot
            self._write(key, record)
            return True
