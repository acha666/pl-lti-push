"""Small HTTP client: no automatic redirects, proxies, retries or page resources."""

import re
import threading
from urllib.parse import urljoin, urlsplit

import requests

from .html import Page, ProtocolError


class Client:
    def __init__(self, config, credentials):
        self.config, self.credentials = config, credentials
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = "pl-lti-push/0.1.1"
        self.lock = threading.Lock()

    def request(self, method, path, **kwargs):
        if not path.startswith("/pl/"):
            raise ProtocolError("Invalid request path")
        with self.lock:
            # Reload for every request so renewed credentials survive process restarts.
            self.session.cookies = self.credentials.load()
            try:
                response = self.session.request(
                    method,
                    self.config.base_url + path,
                    allow_redirects=False,
                    timeout=self.config.request_timeout_seconds,
                    **kwargs,
                )
            except requests.RequestException:
                raise ProtocolError(
                    "HTTP transport failed; request outcome may be unknown"
                ) from None
            self.credentials.save(self.session.cookies)
        return response

    def read(self, path):
        response = self.request("GET", path)
        if response.status_code != 200:
            raise ProtocolError(f"GET rejected (HTTP {response.status_code}); check session/access")
        return Page(response.text)

    def csrf(self, assignment):
        return self.read(assignment.page).csrf(assignment)

    def submit(self, assignment, csrf):
        response = self.request(
            "POST",
            assignment.page,
            data={
                "__csrf_token": csrf,
                "unsafe_assessment_id": assignment.assessment_id,
                "__action": "send_grades",
            },
            headers={
                "Origin": self.config.base_url,
                "Referer": self.config.base_url + assignment.page,
            },
        )
        if response.status_code not in (302, 303):
            raise ProtocolError(f"Unexpected POST response (HTTP {response.status_code})")
        location = urlsplit(
            urljoin(
                self.config.base_url + assignment.page,
                response.headers.get("Location", ""),
            )
        )
        base = urlsplit(self.config.base_url)
        pattern = (
            rf"/pl/course_instance/{assignment.course_instance_id}"
            r"/instructor/jobSequence/[1-9][0-9]*"
        )
        if (
            location.scheme != base.scheme
            or location.netloc != base.netloc
            or location.query
            or location.fragment
            or not re.fullmatch(pattern, location.path)
        ):
            raise ProtocolError("POST did not return the expected same-origin job URL")
        return location.path

    def poll(self, path):
        return self.read(path).job(path.rsplit("/", 1)[1])
