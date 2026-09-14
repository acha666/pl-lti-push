"""Origin-bound browser session storage. No passwords or LMS OAuth keys."""

import json
import math
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from requests.cookies import RequestsCookieJar, create_cookie

from .errors import UserError

SESSION_NAME = "pl2_session"


def atomic_json(path: Path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def make_jar(cookies, origin):
    host = urlsplit(origin).hostname
    jar = RequestsCookieJar()
    for c in cookies:
        if c["name"] != SESSION_NAME:
            continue
        domain = c["domain"]
        if not (domain.lstrip(".") == host or (domain.startswith(".") and host.endswith(domain))):
            continue
        expiry = c.get("expires", -1)
        if expiry is not None:
            if type(expiry) not in (int, float) or not math.isfinite(expiry):
                raise UserError("Invalid cookie expiry")
            if expiry != -1 and expiry <= time.time():
                continue
        if not isinstance(c["value"], str) or any(x in c["value"] for x in "\r\n;"):
            raise UserError("Invalid cookie value")
        path = c.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/"):
            raise UserError("Invalid cookie path")
        secure = c.get("secure", True)
        if type(secure) is not bool:
            raise UserError("Invalid cookie secure flag")
        jar.set_cookie(
            create_cookie(
                c["name"],
                c["value"],
                domain=domain,
                path=path,
                secure=secure,
                expires=int(expiry) if expiry and expiry > 0 else None,
                rest={"HttpOnly": c.get("httpOnly", True)},
            )
        )
    if not jar:
        raise UserError("No unexpired PrairieLearn session cookie for the configured origin")
    return jar


class Credentials:
    def __init__(self, path, origin):
        self.path, self.origin = path, origin

    def load(self):
        if not self.path.exists():
            raise UserError(
                "No session configured. Run import-cookies first "
                "(Docker: docker compose run --rm scheduler import-cookies)."
            )
        if self.path.stat().st_mode & 0o077:
            raise UserError("Credential file must have permissions 0600")
        data = json.loads(self.path.read_text())
        if data["origin"] != self.origin:
            raise UserError("Credential origin does not match configuration")
        return make_jar(data["cookies"], self.origin)

    def save(self, jar):
        cookies = [
            dict(
                name=c.name,
                value=c.value,
                domain=c.domain,
                path=c.path,
                secure=c.secure,
                expires=c.expires,
                httpOnly=True,
            )
            for c in jar
            if c.name == SESSION_NAME
        ]
        atomic_json(self.path, {"origin": self.origin, "cookies": cookies})

    def import_session(self, text):
        value = text.strip()
        if not value or any(ord(c) <= 32 or ord(c) >= 127 or c in ';"\\' for c in value):
            raise UserError("Paste only the pl2_session cookie value.")
        origin = urlsplit(self.origin)
        self.save(
            make_jar(
                [
                    dict(
                        name=SESSION_NAME,
                        value=value,
                        domain=origin.hostname,
                        secure=origin.scheme == "https",
                    )
                ],
                self.origin,
            )
        )

    def import_file(self, source):
        data = json.loads(source.read_text())
        if isinstance(data, dict) and data.get("origin", self.origin) != self.origin:
            raise UserError("Imported credential origin mismatch")
        cookies = data["cookies"] if isinstance(data, dict) else data
        self.save(make_jar(cookies, self.origin))
