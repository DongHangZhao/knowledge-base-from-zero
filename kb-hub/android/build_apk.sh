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

# libgcc_s 兜底：CPython configure 在 api21 target 下链接测试硬编码要 -lgcc_s，
# 但 r25c 已用 compiler-rt(builtins+unwind) 取代、不提供该库，导致 configure 误判
# "C compiler cannot create executables"。用 NDK clang 编一个空 stub .so 补齐符号名。
echo "=== libgcc_s 兼容兜底 ==="
if [ -n "${ANDROIDNDK:-}" ] && [ -d "$ANDROIDNDK" ]; then
  _CC="$ANDROIDNDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android21-clang"
  while IFS= read -r d; do
    if [ ! -e "$d/libgcc_s.so" ]; then
      if [ -f "$d/libgcc.a" ]; then
        ln -s libgcc.a "$d/libgcc_s.so" 2>/dev/null && echo "  linked libgcc.a -> libgcc_s.so in $d" || true
      elif [ -x "$_CC" ]; then
        echo 'void __kbhub_libgcc_stub(void){}' > /tmp/_libgcc_stub.c
        "$_CC" -shared -fPIC /tmp/_libgcc_stub.c -o "$d/libgcc_s.so" 2>/dev/null \
          && echo "  built empty libgcc_s.so stub in $d" || true
      fi
    fi
  done < <(find "$ANDROIDNDK/sysroot/usr/lib" -type d -name "aarch64-linux-android*" 2>/dev/null)
fi

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
