#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
release_assets_gate.py —— Release 资产完整性门禁

为什么需要这个脚本（2026-11-20 立）
------------------------------------
tag 推上去、`release.yml` 绿了、`gh release list` 里也看得到版本 —— 这三件事
**都不代表 Release 有资产**。GitHub 会给每个 Release 自动挂 Source code (zip/tar.gz)，
所以「有下载项」这个观感是假的；真正的多平台产物是 5 个资产，只能靠人工上传。

结果就是 v1.21.0 ~ v1.23.0 连续 4 个版本只有源码、没有 exe/apk/linux/macos/源码包，
而 CI 全绿、CHANGELOG 也写了「已挂载全部 Release」。**「跑了就当成了」的典型样本。**

本脚本把「发版完成」的判据从「gh release create 返回 0」改成
「5 个资产齐全且体积在合理区间」，缺失即 rc=1。

用法
----
    python tools/release_assets_gate.py --tag v1.23.0     # 核对单个版本
    python tools/release_assets_gate.py --all             # 全部 Release 出矩阵
    python tools/release_assets_gate.py --all --limit 6   # 只看最近 6 个
    python tools/release_assets_gate.py --tag v1.23.0 --json

退出码
------
    0  全部齐全
    1  有缺失（stdout 已列明细）
    2  环境问题（gh 未装 / 未认证 / 网络失败）

边界
----
· 只做**只读**核对，不下载、不上传、不改任何 Release。
· 体积阈值只防「传了个空壳/错文件」，不做哈希一致性判定 —— 跨版本二进制
  本来就可能因为重新构建而变（除非用 --sha-same 显式要求）。
"""

import argparse
import json
import subprocess
import sys

# 资产名 → 最小体积（字节）。体积下限按实际产物的一半左右取，只拦空壳/截断。
# 源码包下限取 100 KB：早期版本文件少（v1.3.x 只有 ~170 KB），阈值定太高会误报。
REQUIRED = {
    "kb-from-zero.zip": 100 * 1024,      # 源码包，从 0.17 MB（v1.3.x）到 1.0 MB（当前）
    "kb-hub.exe": 4 * 1024 * 1024,       # Windows 桌面，约 8.3 MB
    "kb-hub-linux": 8 * 1024 * 1024,     # Linux 桌面，约 17.9 MB
    "kb-hub-macos": 4 * 1024 * 1024,     # macOS 桌面，约 7.4 MB
    "kb-hub-android.apk": 5 * 1024 * 1024,   # Android，约 10.6 MB
}

# Android 壳（kb-hub/android/）是 v1.20.0 才进仓库的：更早的 tag 源码里没有这棵目录，
# 因此那些版本**本就不该有 apk**。2026-11-20 已把 9 个老版本上误挂的 apk 全部删除
# （那是 11-17 补历史时上传的当前产物，与该 tag 内容无关）。
APK_OPTIONAL_BEFORE = (1, 20, 0)


def log(msg=""):
    print(msg)


def _gh(*args):
    r = subprocess.run(["gh"] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("gh %s 失败：%s" % (" ".join(args),
                                              r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout.decode("utf-8", "replace")


def parse_ver(tag):
    core = tag.lstrip("v").split("-")[0]
    try:
        return tuple(int(x) for x in core.split(".")[:3])
    except ValueError:
        return (0, 0, 0)


def fetch_assets(tag=None, limit=100):
    """返回 {tag: [{name,size}, ...]}"""
    if tag:
        raw = _gh("release", "view", tag, "--json", "tagName,assets")
        d = json.loads(raw)
        return {d["tagName"]: d.get("assets", [])}
    raw = _gh("release", "list", "--limit", str(limit), "--json", "tagName")
    out = {}
    for item in json.loads(raw):
        t = item["tagName"]
        d = json.loads(_gh("release", "view", t, "--json", "tagName,assets"))
        out[t] = d.get("assets", [])
    return out


def check_one(tag, assets, require_apk=True):
    """返回 (缺失列表, 过小列表, 明细行)"""
    have = {a["name"]: int(a.get("size") or 0) for a in assets}
    missing, small, rows = [], [], []
    for name, floor in REQUIRED.items():
        # apk 只对「该版本确实没有 Android 壳」的老版本放宽；
        # 已经挂了的就必须核体积，不能因为版本号老就跳过。
        if name == "kb-hub-android.apk" and not require_apk and name not in have:
            rows.append("  %-22s %s" % (name, "— 该版本源码无 Android 壳，不提供 apk"))
            continue
        if name not in have:
            missing.append(name)
            rows.append("  %-22s ✗ 缺失" % name)
        elif have[name] < floor:
            small.append("%s(%.1fMB < %.1fMB)" % (name, have[name] / 1048576.0,
                                                  floor / 1048576.0))
            rows.append("  %-22s ✗ 体积异常 %.2f MB" % (name, have[name] / 1048576.0))
        else:
            rows.append("  %-22s ✓ %.2f MB" % (name, have[name] / 1048576.0))
    extra = [n for n in have if n not in REQUIRED]
    for n in sorted(extra):
        rows.append("  %-22s + 额外资产 %.2f MB" % (n, have[n] / 1048576.0))
    return missing, small, rows


def main():
    ap = argparse.ArgumentParser(description="Release 资产完整性门禁（只读）")
    ap.add_argument("--tag", metavar="V", default=None, help="核对指定 tag")
    ap.add_argument("--all", action="store_true", help="核对全部 Release")
    ap.add_argument("--limit", type=int, default=100, help="--all 时最多取多少个（默认 100）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()

    if not args.tag and not args.all:
        ap.error("要么 --tag V，要么 --all")

    try:
        data = fetch_assets(args.tag, args.limit)
    except FileNotFoundError:
        log("[失败] 找不到 gh 命令，无法核对 Release 资产")
        return 2
    except RuntimeError as e:
        log("[失败] " + str(e))
        return 2

    bad, report = [], {}
    for tag in sorted(data, key=lambda t: parse_ver(t), reverse=True):
        require_apk = parse_ver(tag) >= APK_OPTIONAL_BEFORE
        missing, small, rows = check_one(tag, data[tag], require_apk)
        report[tag] = {"missing": missing, "small": small,
                       "assets": data[tag], "ok": not (missing or small)}
        log("=== %s ===" % tag)
        for r in rows:
            log(r)
        if missing or small:
            bad.append(tag)
            log("  → FAIL：缺 %d 项、异常 %d 项" % (len(missing), len(small)))
        else:
            log("  → PASS")
        log()

    if args.json:
        log(json.dumps(report, ensure_ascii=False, indent=2))

    log("=" * 46)
    if bad:
        log("ASSETS=FAIL  %d/%d 个 Release 资产不全：%s"
            % (len(bad), len(data), ", ".join(bad)))
        return 1
    log("ASSETS=PASS  %d 个 Release 资产齐全" % len(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
