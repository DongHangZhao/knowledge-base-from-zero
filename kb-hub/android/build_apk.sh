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
# "C compiler cannot create executables"。在 NDK sysroot 的库搜索路径放空 stub 静态库
# （-lgcc_s 会找 libgcc_s.a/.so；configure 只验链接能过，符号由 compiler-rt 提供）。
echo "=== libgcc_s 兼容兜底 ==="
if [ -n "${ANDROIDNDK:-}" ] && [ -d "$ANDROIDNDK" ]; then
  _PREFIX="$ANDROIDNDK/toolchains/llvm/prebuilt/linux-x86_64"
  _AR="$_PREFIX/bin/llvm-ar"
  _SYSLIB="$_PREFIX/sysroot/usr/lib/aarch64-linux-android"
  echo "  sysroot lib dir: $_SYSLIB (exists=$([ -d "$_SYSLIB" ] && echo yes || echo no))"
  echo 'void __kbhub_libgcc_stub(void){}' > /tmp/_libgcc_stub.c
  "$_PREFIX/bin/aarch64-linux-android21-clang" -c /tmp/_libgcc_stub.c -o /tmp/_libgcc_stub.o 2>/dev/null || true
  # 只补 gcc_s：NDK 25c 移除该库但 CPython configure 的链接测试仍硬编码要它。
  # openssl/sqlite3 交由 p4a recipe 正常交叉编译（不再 blacklist、不造假 stub）。
  for d in "$_SYSLIB" "$_SYSLIB/21"; do
    [ -d "$d" ] || continue
    if [ -f "$_AR" ] && [ -f /tmp/_libgcc_stub.o ]; then
      "$_AR" rcs "$d/libgcc_s.a" /tmp/_libgcc_stub.o 2>/dev/null && echo "  built libgcc_s.a stub in $d" || true
    fi
  done
  echo "  verify:"; find "$_SYSLIB" -maxdepth 2 -name 'libgcc_s*' -printf "    %p\n" 2>/dev/null | head -20
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
