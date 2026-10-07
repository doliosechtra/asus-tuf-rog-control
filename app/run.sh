#!/bin/bash
# Run TUF Control (uses system PySide6 on CachyOS — no venv needed)
cd "$(dirname "$0")" || exit 1
exec python3 main.py "$@"
