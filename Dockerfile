FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-deps -r requirements.lock
COPY src ./src
RUN pip install --no-deps --no-build-isolation . \
    && mkdir -m 700 /state
COPY --chmod=755 <<'EOF' /usr/local/bin/healthcheck
#!/usr/local/bin/python
import os
import sys
import time

try:
    age = time.time() - os.stat("/tmp/pl-lti-push-heartbeat").st_mtime
except OSError:
    sys.exit(1)
sys.exit(0 if 0 <= age <= 30 else 1)
EOF
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["/usr/local/bin/healthcheck"]
ENTRYPOINT ["pl-lti-push"]
CMD ["--help"]
