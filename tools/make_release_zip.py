#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_release_zip.py —— 打源码发布包（Releases 用）

只收「源码 + 文档 + 模板 + 工具」，绝不收：
    · kb-hub.exe / kb-from-zero.zip 自身（Release 单独上传）
    · inventory.json（真实清单，含本机路径）
    · usage.json / *.db / __pycache__（运行期产物）
    · 任何图片 / 音视频 / PDF / Office 文件

用法
    python tools/make_release_zip.py                 # 打包当前工作区 → 仓库根 kb-from-zero.zip
    python tools/make_release_zip.py --ref v1.21.0   # 按指定 tag/commit 打包（不动工作区）
    python tools/make_release_zip.py --out D:/x.zip
    python tools/make_release_zip.py --list          # 只打印清单不打包

🔴 `--ref` 是补发历史 Release 的唯一正确方式：内容必须来自该 tag 的 git 对象，
   而不是「当前工作区打一份、随便挂到老版本上」——后者会把新代码冒充成老版本。
"""
import argparse
import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

ALLOW_EXT = {".md", ".py", ".bat", ".json", ".html", ".css", ".js", ".txt",
             ".yml", ".yaml", ".example"}
# 无扩展名但必须进包的：2026-11-20 补 VERSION / Dockerfile ——
# 之前只认 LICENSE/NOTICE/.gitignore，导致发布包里没有版本号文件、也没有 Dockerfile，
# 用户解压后既不知道自己拿的是哪个版本、也没法 docker build。
ALLOW_NAME = {"LICENSE", "NOTICE", ".gitignore", "VERSION", "Dockerfile"}
# 2026-11-17 修：state 目录含真实评测数据（ab100_*/kg_*/holdout_* 里嵌真名/公司/路径），
# 绝不能进公开 zip；build*/dist* 是 PyInstaller 构建中间物（xref HTML 带绝对路径）。
# 原脚本只精确排除 build/dist，遇 build_new 和 state 就漏，导致重打后 privacy_scan 报 50 BLOCK。
SKIP_DIRS = {".git", "__pycache__", "node_modules", "build", "dist", ".idea", ".vscode", "state",
             # 2026-11-20 加：_versions 是本地发版快照（含真实评测数据/路径），
             # 它的 .md 正好在白名单里，不加会被打进公开 zip 里。
             "_versions"}
SKIP_FILE = {"kb-hub.exe", "kb-from-zero.zip", "inventory.json", "usage.json",
             "desktop.ini", "Thumbs.db", ".DS_Store"}
SKIP_EXT = {".db", ".db-wal", ".db-shm", ".log", ".pyc", ".spec"}


def _skip_dir(name):
    return (name in SKIP_DIRS or name.startswith(".") or name.startswith("build")
            or name.startswith("dist"))


def _skip_file(fn):
    if fn in SKIP_FILE:
        return True
    return os.path.splitext(fn)[1].lower() in SKIP_EXT


def _allowed(fn):
    return os.path.splitext(fn)[1].lower() in ALLOW_EXT or fn in ALLOW_NAME


def collect():
    out = []
    for dp, dns, fns in os.walk(REPO):
        dns[:] = sorted(d for d in dns if not _skip_dir(d))
        for fn in sorted(fns):
            if _skip_file(fn) or not _allowed(fn):
                if not _skip_file(fn):
                    print("  [跳过] 不在白名单：%s" % os.path.join(dp, fn))
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, REPO).replace("\\", "/")
            out.append((p, rel))
    return out


def _git(*args):
    r = subprocess.run(["git"] + list(args), cwd=REPO,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("git %s 失败：%s" % (" ".join(args),
                                               r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout


def collect_from_ref(ref):
    """按 git ref 收集文件，返回 [(ref:path, rel)]；工作区一个字节都不动。

    🔴 必须 `-c core.quotepath=false`：默认 git 会把中文路径输出成
       `"modules/\\347\\237\\245..."` 这种带引号的八进制转义，按扩展名过滤时
       整棵中文目录会被静默丢掉（2026-11-20 实测：v1.21.0 只收到 69 个文件，
       `modules/知识库索引/` 全丢）。这种丢失在 zip 里看不出来，只有解压才知道少了什么。
    """
    raw = _git("-c", "core.quotepath=false", "ls-tree", "-r", "--name-only", ref)
    lines = raw.decode("utf-8").splitlines()
    picked = []
    for rel in sorted(lines):
        parts = rel.split("/")
        if any(_skip_dir(p) for p in parts[:-1]):
            continue
        fn = parts[-1]
        if _skip_file(fn) or not _allowed(fn):
            continue
        picked.append(("%s:%s" % (ref, rel), rel))
    return picked


def main():
    ap = argparse.ArgumentParser(description="打源码发布包")
    ap.add_argument("--out", default=os.path.join(REPO, "kb-from-zero.zip"))
    ap.add_argument("--ref", default=None,
                    help="按 git ref（tag/commit）打包，不动工作区；不传则打当前工作区")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.ref:
        try:
            files = collect_from_ref(args.ref)
        except RuntimeError as e:
            print("[失败] " + str(e))
            return 2
        src_kind = "git:%s" % args.ref
    else:
        files = collect()
        src_kind = "工作区"

    print("[收集] %s → %d 个文件" % (src_kind, len(files)))
    if args.list:
        for _, rel in files:
            print("  " + rel)
        return 0

    if os.path.exists(args.out):
        os.remove(args.out)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        if args.ref:
            for spec, rel in files:
                z.writestr(rel, _git("cat-file", "blob", spec))
        else:
            for p, rel in files:
                z.write(p, rel)

    size = os.path.getsize(args.out)
    print("[完成] %s（%.1f KB / %d 文件 / 来源 %s）"
          % (args.out, size / 1024.0, len(files), src_kind))
    return 0


if __name__ == "__main__":
    sys.exit(main())
