#!/usr/bin/env bash
# 一次性环境准备。需要 uv（brew install uv）和 system 的 opus（brew install opus）。
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "先装 uv: brew install uv"; exit 1; }
command -v opus >/dev/null 2>&1 || brew list opus >/dev/null 2>&1 || echo "提醒: opus 编解码库建议 brew install opus（找不到时 pip 版 opuslib 会装不上）"

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e apps/device-mac
uv pip install --python .venv/bin/python ruff

echo
echo "装好了。下一步："
echo "  ./scripts/dev.sh doctor    # 体检（麦克风 / agent / 投屏权限）"
echo "  ./scripts/dev.sh bind      # 首次绑定，拿激活码"
echo "  ./scripts/dev.sh run       # 开始语音控制"
