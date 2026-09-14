#!/bin/sh
set -eu

python3.13 -m venv "$VIRTUAL_ENV"
"$VIRTUAL_ENV/bin/python" -m pip install -r requirements.lock
"$VIRTUAL_ENV/bin/python" -m pip install --no-build-isolation -e '.[dev]'
"$VIRTUAL_ENV/bin/python" -m pip check
npm ci
