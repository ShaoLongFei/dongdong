#!/usr/bin/env bash
# 提交前必跑：格式 + lint + smoke。
set -euo pipefail
cd "$(dirname "$0")/.."

CFG=apps/device-mac/pyproject.toml
.venv/bin/ruff format --check --config "$CFG" apps tests
.venv/bin/ruff check --config "$CFG" apps tests
.venv/bin/python tests/smoke.py
.venv/bin/python tests/agent_smoke.py
.venv/bin/python tests/agent_transport.py
.venv/bin/python tests/agent_mcp.py
.venv/bin/python tests/task_smoke.py
.venv/bin/python tests/meeting_smoke.py
.venv/bin/python tests/story_smoke.py
