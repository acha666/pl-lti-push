"""Run the installed CLI and scheduler against loopback with Docker --network none."""

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

posts = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        assert "pl2_session=fake" in self.headers["Cookie"]
        self.send_response(200)
        self.end_headers()
        if "/jobSequence/" in self.path:
            data = {
                "json": {
                    "jobSequence": {"id": "9", "status": "Success"},
                    "jobs": [
                        {
                            "status": "Success",
                            "outputHtml": "1 score successfully sent.\n"
                            "0 scores not sent due to errors.\n0 scores skipped (not sent).",
                        }
                    ],
                }
            }
            body = '<script type="application/json" data-component="JobSequenceResults">'
            body += json.dumps(data) + "</script>"
        else:
            body = '<form method="POST"><input name="__csrf_token" value="fake-csrf">'
            body += '<input name="unsafe_assessment_id" value="3">'
            body += '<button name="__action" value="send_grades">Send</button></form>'
        self.wfile.write(body.encode())

    def do_POST(self):
        global posts
        data = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        assert data["__action"] == ["send_grades"]
        assert data["__csrf_token"] == ["fake-csrf"]
        assert data["unsafe_assessment_id"] == ["3"]
        posts += 1
        self.send_response(302)
        self.send_header("Location", "/pl/course_instance/1/instructor/jobSequence/9")
        self.end_headers()


def main():
    if "SMOKE_DIR" in os.environ:
        assert Path(os.environ["SMOKE_DIR"]).stat().st_mode & 0o777 == 0o700
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(dir=os.environ.get("SMOKE_DIR")) as temp:
            directory = Path(temp)
            config = directory / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "base_url": f"http://127.0.0.1:{server.server_port}",
                        "assignments": [
                            {
                                "name": "smoke",
                                "course_instance_id": "1",
                                "lti_course_instance_id": "2",
                                "assessment_id": "3",
                                "cron": ["* * * * *", "* * * * *"],
                            }
                        ],
                    }
                )
            )
            cookies = directory / "import.json"
            cookies.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "pl2_session",
                                "value": "fake",
                                "domain": "127.0.0.1",
                                "secure": False,
                            }
                        ]
                    }
                )
            )
            command = ["pl-lti-push", "--config", str(config), "--state", str(directory / "state")]
            for args in [
                ["validate"],
                ["import-cookies", "/dev/stdin"],
                ["check-auth"],
                ["run", "smoke"],
                ["status"],
            ]:
                subprocess.run(
                    command + args,
                    input=cookies.read_bytes() if args[0] == "import-cookies" else None,
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
            assert posts == 1
            process = subprocess.Popen(
                command + ["serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    state = json.loads((directory / "state/runs.json").read_text())
                    if posts == 2 and state["assignments"]["1/2/3"]["status"] == "success":
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("Scheduler did not complete its local job")
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=5)
                assert process.returncode == 0
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            assert (directory / "state/cookies.json").stat().st_mode & 0o777 == 0o600
            assert (directory / "state").stat().st_mode & 0o777 == 0o700
            print("Container smoke passed: CLI, stdin cookie import, HTTP job, cron, SIGTERM.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
