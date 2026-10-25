#!/usr/bin/env bash
# 只安装固定渲染依赖；图标已随源码分发。
set -euo pipefail
cd "$(dirname "$0")/../apps/device-mac/dongdong/vendor/birdview"
command -v node >/dev/null || { echo '请先安装 Node.js 18+'; exit 1; }
npm ci --ignore-scripts --no-audit --no-fund
node scripts/birdview.mjs doctor
