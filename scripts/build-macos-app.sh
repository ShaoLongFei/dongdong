#!/bin/sh
# 构建独立 arm64 App；运行时不依赖用户安装 Node、Python 或 uv。
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${DONGDONG_APP_OUT:-"$ROOT/dist"}
APP="$OUT/Dongdong.app"
WORK=${TMPDIR:-/tmp}/dongdong-build
PY_URL=${DONGDONG_PYTHON_URL:-"https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.12.15%2B20261021-aarch64-apple-darwin-install_only_stripped.tar.gz"}
SKIP_FRONTEND=0

case "${1:-}" in
  "") ;;
  --skip-frontend) SKIP_FRONTEND=1 ;;
  *) echo "用法: $0 [--skip-frontend]" >&2; exit 2 ;;
esac

die() {
  echo "构建失败：$*" >&2
  exit 1
}

[ "$(uname -s)" = "Darwin" ] || die "独立 App 只能在 macOS 构建"
[ "$(uname -m)" = "arm64" ] || die "当前只支持 Apple Silicon arm64 构建（当前：$(uname -m)）"

command -v swiftc >/dev/null || die "需要 Xcode Command Line Tools（swiftc）"
command -v xcrun >/dev/null || die "需要 Xcode Command Line Tools（xcrun）"
command -v codesign >/dev/null || die "需要 Xcode Command Line Tools（codesign）"
command -v curl >/dev/null || die "需要 curl"
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  command -v npm >/dev/null || die "首次构建需要 npm；或使用 --skip-frontend"
  (cd "$ROOT/apps/web" && npm run build)
fi
[ -f "$ROOT/apps/device-mac/dongdong/static/index.html" ] || {
  die "缺少静态前端，请先运行 npm run build"
}
[ -x "$ROOT/.venv/bin/python" ] || {
  die "需要先运行 ./scripts/setup.sh 创建 .venv"
}
[ -f "$ROOT/.venv/lib/python3.12/site-packages/_sounddevice_data/portaudio-binaries/libportaudio.dylib" ] ||
  die "找不到 arm64 PortAudio；请先运行 ./scripts/setup.sh 并确认 sounddevice 安装完整"
[ -f "$ROOT/tools/ffmpeg/darwin-arm64/ffmpeg" ] && [ -x "$ROOT/tools/ffmpeg/darwin-arm64/ffmpeg" ] ||
  die "找不到项目内 arm64 FFmpeg：$ROOT/tools/ffmpeg/darwin-arm64/ffmpeg"
[ -f /opt/homebrew/opt/opus/lib/libopus.dylib ] ||
  die "找不到 arm64 Opus；请先运行 brew install opus"

rm -rf "$WORK" "$APP"
mkdir -p "$WORK" "$APP/Contents/MacOS" "$APP/Contents/Resources/app" "$APP/Contents/Resources/lib"

PY_ARCHIVE="$WORK/python.tar.gz"
if [ ! -f "$PY_ARCHIVE" ]; then curl -L --fail --retry 2 -o "$PY_ARCHIVE" "$PY_URL"; fi
tar -xzf "$PY_ARCHIVE" -C "$WORK"
PYTHON_BIN=$(find "$WORK" -path '*/bin/python3' -perm -u+x -print -quit)
[ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ] ||
  die "Python 3.12 arm64 归档中缺少可执行 bin/python3，请检查 DONGDONG_PYTHON_URL"
PYTHON_ROOT=$(CDPATH= cd -- "$(dirname "$PYTHON_BIN")/.." && pwd)
mv "$PYTHON_ROOT" "$APP/Contents/Resources/python"

# 只复制运行时所需源码与资源；用户数据不进入 App。
mkdir -p "$APP/Contents/Resources/app/apps/device-mac"
cp -R "$ROOT/apps/device-mac/dongdong" "$APP/Contents/Resources/app/apps/device-mac/"
cp "$ROOT/apps/device-mac/pyproject.toml" "$APP/Contents/Resources/app/apps/device-mac/"
mkdir -p "$APP/Contents/Resources/app/tools"
cp -R "$ROOT/tools/ffmpeg" "$APP/Contents/Resources/app/tools/"
# FFmpeg 会直接调用 AVFoundation 抓屏；让它沿用 App 的签名标识，避免
# macOS 把内置子进程当成另一个屏幕录制权限主体。
codesign --force --sign - --identifier com.dongdong.app \
  "$APP/Contents/Resources/app/tools/ffmpeg/darwin-arm64/ffmpeg" >/dev/null
codesign --force --sign - --identifier com.dongdong.app \
  "$APP/Contents/Resources/app/tools/ffmpeg/darwin-x86_64/ffmpeg" >/dev/null

# 虚拟环境已在当前机器验证过依赖和 arm64 扩展，直接复用 site-packages。
cp -R "$ROOT/.venv/lib/python3.12/site-packages" "$APP/Contents/Resources/python/lib/python3.12/"
cp "$ROOT/.venv/lib/python3.12/site-packages/_sounddevice_data/portaudio-binaries/libportaudio.dylib" \
  "$APP/Contents/Resources/lib/"
cp /opt/homebrew/opt/opus/lib/libopus.dylib "$APP/Contents/Resources/lib/"

SDK=$(xcrun --sdk macosx --show-sdk-path)
swiftc -swift-version 5 -target arm64-apple-macosx13.0 -sdk "$SDK" \
  -framework Cocoa -framework WebKit "$ROOT/apps/device-mac/macos/main.swift" \
  -o "$APP/Contents/MacOS/Dongdong"
cp "$ROOT/apps/device-mac/macos/Info.plist" "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP" >/dev/null

echo "已构建：$APP"
du -sh "$APP"
