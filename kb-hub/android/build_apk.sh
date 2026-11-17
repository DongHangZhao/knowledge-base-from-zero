#!/usr/bin/env bash
# build_apk.sh —— 在 Linux(GitHub Actions ubuntu) 上用 p4a 构建 KB Hub Android APK
# 前置：系统依赖 + Android SDK/NDK 已由 CI 安装；ANDROIDSDK/ANDROIDNDK/ANDROIDAPI/NDKAPI 已导出。
# 产物：../dist/kb-hub-v<VERSION>-arm64-v8a-debug.apk
set -euo pipefail
cd "$(dirname "$0")"

echo "=== p4a 版本 ==="
python3 -m pythonforandroid.toolchain --version 2>&1 | head -2 || p4a --version 2>&1 | head -2 || true

echo "=== 清理旧 dist ==="
rm -rf dist

echo "=== 构建 APK（配置来自同目录 .p4a）==="
p4a apk --private "$(pwd)" --dist_name kbhub

echo "=== 产物 ==="
find . -name "*.apk" -type f -printf "%s %p\n" | head -5

echo "=== 拷到统一 dist 目录 ==="
mkdir -p ../dist
cp "$(find . -name '*.apk' -type f | head -1)" ../dist/kb-hub-android.apk
ls -la ../dist/kb-hub-android.apk
