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
if ! p4a apk --private "$(pwd)" --dist_name kbhub; then
  echo "=== [诊断] 构建失败，dump config.log 关键段 ==="
  find "$HOME/.local/share/python-for-android" -name config.log -type f 2>/dev/null | while read -r f; do
    echo "--- $f ---"
    grep -iE "error|cannot|undefined reference|no such file|not found" "$f" 2>/dev/null | tail -25
    echo "--- 尾部 30 行 ---"
    tail -30 "$f"
  done
  echo "=== [诊断] clang 直接冒烟测试 ==="
  CC="$ANDROIDNDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android21-clang"
  echo 'int main(){return 0;}' > /tmp/_t.c
  "$CC" -v /tmp/_t.c -o /tmp/_t 2>&1 | tail -30 || true
  exit 1
fi

echo "=== 产物 ==="
find . -name "*.apk" -type f -printf "%s %p\n" | head -5

echo "=== 拷到统一 dist 目录 ==="
mkdir -p ../dist
cp "$(find . -name '*.apk' -type f | head -1)" ../dist/kb-hub-android.apk
ls -la ../dist/kb-hub-android.apk
