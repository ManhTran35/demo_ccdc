#!/usr/bin/env bash
set -euo pipefail
cd /workspace/demo_ccdc
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check --cache-dir /workspace/ccdc-tooling/pip-cache -r requirements-lock.txt
.venv/bin/python scripts/prepare.py
.venv/bin/python -m unittest discover -s tests -v
