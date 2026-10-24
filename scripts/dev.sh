#!/usr/bin/env bash
# 跑咚咚：./scripts/dev.sh [doctor|bind|run|...]
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/dongdong ]; then
  echo "还没有虚拟环境，先跑 ./scripts/setup.sh"
  exit 1
fi

exec .venv/bin/dongdong "$@"
