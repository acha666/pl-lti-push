"""Parse only the form and server-rendered job component needed by the protocol."""

import json
import re
from html.parser import HTMLParser

from .errors import UserError


class ProtocolError(UserError):
    pass


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.form = None
        self.in_props = False
        self.props = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.form = {"attrs": attrs, "fields": {}, "actions": []}
        if self.form is not None and tag in ("input", "button") and "disabled" not in attrs:
            name, value = attrs.get("name"), attrs.get("value", "")
            if name == "__action":
                self.form["actions"].append(value)
            elif name:
                self.form["fields"][name] = value
        if (
            tag == "script"
            and attrs.get("data-component") == "JobSequenceResults"
            and attrs.get("type") == "application/json"
        ):
            self.in_props = True

    def handle_endtag(self, tag):
        if tag == "form" and self.form is not None:
            self.forms.append(self.form)
            self.form = None
        if tag == "script":
            self.in_props = False

    def handle_data(self, data):
        if self.in_props:
            self.props.append(data)

    def csrf(self, assignment):
        tokens = []
        for form in self.forms:
            attrs, fields = form["attrs"], form["fields"]
            if (
                attrs.get("method", "").upper() == "POST"
                and attrs.get("action", "") in ("", assignment.page)
                and fields.get("unsafe_assessment_id") == assignment.assessment_id
                and "send_grades" in form["actions"]
                and fields.get("__csrf_token")
            ):
                tokens.append(fields["__csrf_token"])
        if len(tokens) != 1:
            raise ProtocolError("Expected one enabled send_grades form for this assessment")
        return tokens[0]

    def job(self, expected_id):
        try:
            data = json.loads("".join(self.props))["json"]
            sequence, jobs = data["jobSequence"], data["jobs"]
            if str(sequence["id"]) != expected_id or not jobs:
                raise ValueError
            status = sequence["status"]
            if status not in ("Running", "Success", "Error", "Stopped"):
                raise ValueError
            if status == "Running":
                return "running", {}
            if status in ("Error", "Stopped") or any(
                j["status"] in ("Error", "Stopped") for j in jobs
            ):
                return "failed", {}
            counts = {"sent": 0, "errors": 0, "skipped": 0}
            for job in jobs:
                if job["status"] != "Success":
                    raise ValueError
                text = Text(job["outputHtml"]).text
                # A successful server job can still contain per-student delivery errors.
                for key, pattern in [
                    ("sent", r"(\d+) scores? successfully sent\."),
                    ("errors", r"(\d+) scores? not sent due to errors\."),
                    ("skipped", r"(\d+) scores? skipped \(not sent\)\."),
                ]:
                    matches = re.findall(pattern, text)
                    if len(matches) != 1:
                        raise ValueError
                    counts[key] += int(matches[0])
            return ("partial" if counts["errors"] else "success"), counts
        except (KeyError, TypeError, ValueError):
            raise ProtocolError(
                "Unrecognized job result; inspect the job in PrairieLearn"
            ) from None


class Text(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.text = ""
        self.feed(source)

    def handle_data(self, data):
        self.text += data
