#!/bin/sh
# 构建独立 arm64 App；运行时不依赖用户安装 Node、Python 或 uv。
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${DONGDONG_APP_OUT:-"$ROOT/dist"}
APP="$OUT/Dongdong.app"
WORK=${TMPDIR:-/tmp}/dongdong-build
PY_URL=${DONGDONG_PYTHON_URL:-"https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.12.15%2B20261021-aarch64-apple-darwin-install_only_stripped.tar.gz"}
SKIP_FRONTEND=0
[ "${1:-}" = "--skip-frontend" ] && SKIP_FRONTEND=1

command -v swiftc >/dev/null || { echo "需要 Xcode Command Line Tools（swiftc）" >&2; exit 1; }
command -v curl >/dev/null || { echo "需要 curl" >&2; exit 1; }
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  command -v npm >/dev/null || { echo "首次构建需要 npm；或使用 --skip-frontend" >&2; exit 1; }
  (cd "$ROOT/apps/web" && npm run build)
fi
[ -f "$ROOT/apps/device-mac/dongdong/static/index.html" ] || {
  echo "缺少静态前端，请先运行 npm run build" >&2; exit 1;
}
[ -x "$ROOT/.venv/bin/python" ] || {
  echo "需要先运行 ./scripts/setup.sh 创建 .venv" >&2; exit 1;
}

rm -rf "$WORK" "$APP"
mkdir -p "$WORK" "$APP/Contents/MacOS" "$APP/Contents/Resources/app" "$APP/Contents/Resources/lib"

PY_ARCHIVE="$WORK/python.tar.gz"
if [ ! -f "$PY_ARCHIVE" ]; then curl -L --fail --retry 2 -o "$PY_ARCHIVE" "$PY_URL"; fi
tar -xzf "$PY_ARCHIVE" -C "$WORK"
mv "$WORK/python" "$APP/Contents/Resources/python"

# 只复制运行时所需源码与资源；用户数据不进入 App。
mkdir -p "$APP/Contents/Resources/app/apps/device-mac"
cp -R "$ROOT/apps/device-mac/dongdong" "$APP/Contents/Resources/app/apps/device-mac/"
cp "$ROOT/apps/device-mac/pyproject.toml" "$APP/Contents/Resources/app/apps/device-mac/"
mkdir -p "$APP/Contents/Resources/app/tools"
cp -R "$ROOT/tools/ffmpeg" "$APP/Contents/Resources/app/tools/"

# 虚拟环境已在当前机器验证过依赖和 arm64 扩展，直接复用 site-packages。
cp -R "$ROOT/.venv/lib/python3.12/site-packages" "$APP/Contents/Resources/python/lib/python3.12/"
cp "$ROOT/.venv/lib/python3.12/site-packages/_sounddevice_data/portaudio-binaries/libportaudio.dylib" \
  "$APP/Contents/Resources/lib/"
if [ -f /opt/homebrew/opt/opus/lib/libopus.dylib ]; then
  cp /opt/homebrew/opt/opus/lib/libopus.dylib "$APP/Contents/Resources/lib/"
else
  echo "警告：未找到 Homebrew libopus，语音功能需要用户安装 opus" >&2
fi

SDK=$(xcrun --sdk macosx --show-sdk-path)
swiftc -swift-version 5 -target arm64-apple-macosx13.0 -sdk "$SDK" \
  -framework Cocoa -framework WebKit "$ROOT/apps/device-mac/macos/main.swift" \
  -o "$APP/Contents/MacOS/Dongdong"
cp "$ROOT/apps/device-mac/macos/Info.plist" "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP" >/dev/null

echo "已构建：$APP"
du -sh "$APP"
