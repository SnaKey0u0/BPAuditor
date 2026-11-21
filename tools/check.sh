#!/usr/bin/env bash
# 合入前的檢查：ruff（F、E9）＋ pytest。`tools/check.sh lint` 只跑 ruff。
# 用 .venv 的 Python；CI 等沒有 .venv 的環境以 PYTHON=python 指定。
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
py=${PYTHON:-.venv/Scripts/python}
[ -x "$py" ] || [ -n "${PYTHON:-}" ] || py=.venv/bin/python
"$py" -m ruff check --quiet
[ "${1:-}" = lint ] && exit 0
QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen} "$py" -m pytest -q
