#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_docker_assets — 容器化资产的离线自检（W32）

为什么需要这个脚本
------------------
W32 的验收判据里有 4 条要靠 `docker compose build/up` 才能取证，而目标机器
可能没装 Docker（本轮就是：docker 未安装 + wsl.exe 被安全策略拦）。**没有
Docker 不等于资产不用验**——其中相当一部分是可确定性验证的：

  C1 构建上下文里有没有隐私文件   → 按 .dockerignore 规则模拟上下文，逐条断言
  C2 镜像会不会把 db/模型烤进去   → 同上（*.db / models/ 必须被排除）+ 挂载声明
  C3 compose 会不会对外暴露       → 解析 YAML，断言端口只绑回环
  C4 容器是否非 root / 只读根     → 解析 YAML + Dockerfile 的 USER 指令
  C5 入口与依赖能否自洽           → 断言 api_server.py / kb_rag.py / requirements 在上下文内

剩下必须真 Docker 才能验的（build rc、容器内 curl 检索、docker inspect 挂载点、
干净环境照 README 复现）由脚本显式打印为 PENDING，**不许当通过**。

🔴 已知局限：.dockerignore 的模式匹配是 BuildKit 语义的**近似实现**（支持 `**`、
目录尾斜杠、basename 任意层匹配）。Docker 可用时，以 `docker build` 的真实
上下文为准；本脚本的作用是「没 Docker 时也不放过红线」。

用法（必须用 venv 解释器，脚本只依赖标准库 + PyYAML）：
    python tools/verify_docker_assets.py
    python tools/verify_docker_assets.py --root . --json
退出码：0 = 全部离线项 PASS；1 = 有 FAIL；2 = 资产缺失
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# 隐私红线：构建上下文里出现即 FAIL（AGENTS §5 L1.1）
PRIVATE_PARTS = {"raw", "个人信息", "隐私", "通话录音", "密钥", "credentials"}
PRIVATE_SUFFIX = (".env", ".key", ".pem", ".p12", ".pfx")
# 必须在上下文内，否则镜像起不来（缺一即 FAIL）
REQUIRED_IN_CONTEXT = [
    "requirements-demo.txt",
    "modules/知识库索引/api_server.py",
    "modules/知识库索引/kb_rag.py",
    "modules/知识库索引/mcp_server.py",
]

PENDING = [
    "docker compose build rc=0（未安装 Docker）",
    "docker compose up -d + 容器内 curl /search 返回命中",
    "docker inspect 验证 kb.db / models 确为挂载点而非镜像层",
    "docker images 尺寸核对（应 < 1.5G，不含 2.3G 模型与库）",
    "干净环境照 README 复现启动",
]


# ── .dockerignore 近似匹配 ───────────────────────────────────────────────────
def _pattern_to_regex(pat):
    """把一条 .dockerignore 模式转成匹配『文件相对路径』的正则。"""
    pat = pat.strip()
    leading_slash = pat.startswith("/")
    pat = pat.strip("/")
    if not pat:
        return None

    # 逐段转换，** 允许跨层
    def seg(s):
        out = []
        for ch in s:
            if ch == "*":
                out.append("[^/]*")
            elif ch == "?":
                out.append("[^/]")
            else:
                out.append(re.escape(ch))
        return "".join(out)

    parts = pat.split("/")
    body = []
    for i, p in enumerate(parts):
        if p == "**":
            body.append("(?:[^/]+/)*")
        else:
            body.append(seg(p))
            if i != len(parts) - 1:
                body.append("/")
    core = "".join(body).replace("(?:[^/]+/)*/", "(?:[^/]+/)*")
    if "**" not in pat and "/" not in pat:
        # 无限定层的 basename / 目录名：任意层级命中
        return re.compile(r"^(?:.*/)?" + core + r"(?:/.*)?$")
    if leading_slash:
        return re.compile(r"^" + core + r"(?:/.*)?$")
    return re.compile(r"^(?:.*/)?" + core + r"(?:/.*)?$")


def load_dockerignore(root):
    path = os.path.join(root, ".dockerignore")
    if not os.path.isfile(path):
        return None
    rules = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rx = _pattern_to_regex(line)
            if rx:
                rules.append((line, rx))
    return rules


def is_ignored(rel, rules):
    """返回命中的规则原文（None 表示未被排除）。后出现的规则优先（近似）。"""
    hit = None
    for raw, rx in rules:
        if rx.match(rel):
            hit = raw
    return hit


def walk_context(root, rules):
    """模拟构建上下文：返回 (保留文件列表, [(被排除路径, 规则)] 抽样)。"""
    kept, dropped = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        # 目录整体命中就剪枝（省时间，也符合 docker 行为）
        pruned = []
        for d in list(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if is_ignored(rel, rules):
                dropped.append((rel + "/", is_ignored(rel, rules)))
                dirnames.remove(d)
                pruned.append(d)
        for fn in filenames:
            rel = f"{rel_dir}/{fn}" if rel_dir else fn
            hit = is_ignored(rel, rules)
            if hit:
                dropped.append((rel, hit))
            else:
                kept.append(rel)
    return kept, dropped


# ── 各检查项 ─────────────────────────────────────────────────────────────────
def check_dockerfile(root):
    """Dockerfile 静态检查：结构、非 root、无远程 ADD、无明文密钥。"""
    path = os.path.join(root, "Dockerfile")
    if not os.path.isfile(path):
        return {"name": "Dockerfile 存在", "status": "FAIL", "detail": "缺 Dockerfile"}
    text = open(path, encoding="utf-8").read()
    upper = text.upper()
    issues = []

    if "FROM " not in upper:
        issues.append("缺 FROM")
    if re.search(r"^\s*ADD\s+https?://", text, re.M | re.I):
        issues.append("用了远程 ADD（不可复现，应改 COPY + 固定校验）")
    if "USER 10001" not in text and "USER kb" not in text:
        issues.append("没有切非 root 用户")
    if "HEALTHCHECK" not in upper:
        issues.append("缺 HEALTHCHECK")
    if "CMD " not in upper and "ENTRYPOINT " not in upper:
        issues.append("缺 CMD/ENTRYPOINT")
    # 明文密钥：等号后接长串且出现 key/token/secret 字样
    for m in re.finditer(r"(?i)\b([A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)\s*=\s*['\"]?([A-Za-z0-9_\-]{16,})", text):
        issues.append("疑似明文凭据：%s" % m.group(1))
    # 中文路径必须配 UTF-8 locale
    if "知识库索引" in text and "LANG=C.UTF-8" not in text:
        issues.append("含中文路径但未设 LANG=C.UTF-8")
    if "COPY . ." in text and ".dockerignore" not in os.listdir(root):
        issues.append("COPY . . 但没有 .dockerignore（上下文未收敛）")

    return {
        "name": "Dockerfile 结构自检",
        "status": "FAIL" if issues else "PASS",
        "detail": "；".join(issues) if issues else "FROM/USER/HEALTHCHECK/CMD 齐备，无远程 ADD 与明文凭据",
    }


def check_compose(root):
    """compose 检查：只绑回环、卷只读、非 root、只读根、db/模型走卷。"""
    path = os.path.join(root, "docker-compose.yml")
    if not os.path.isfile(path):
        return {"name": "compose 存在", "status": "FAIL", "detail": "缺 docker-compose.yml"}
    try:
        import yaml
    except ImportError:
        return {"name": "compose 解析", "status": "FAIL", "detail": "缺 PyYAML，无法解析（venv 内 pip install pyyaml）"}

    try:
        doc = yaml.safe_load(open(path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"name": "compose 解析", "status": "FAIL", "detail": "YAML 语法错误：%s" % e}

    services = (doc or {}).get("services") or {}
    if not services:
        return {"name": "compose 解析", "status": "FAIL", "detail": "没有 services"}
    svc_name, svc = next(iter(services.items()))
    issues, notes = [], []

    for p in svc.get("ports", []):
        sp = str(p)
        if not (sp.startswith("127.0.0.1:") or sp.startswith("localhost:")):
            issues.append("端口 %s 未绑回环（红线：不对外暴露）" % sp)
        else:
            notes.append("端口 %s 绑回环" % sp)

    vols = svc.get("volumes", [])
    db_mounted = models_mounted = False
    for v in vols:
        vs = str(v)
        if not vs.endswith(":ro") and ":ro" not in vs:
            issues.append("卷 %s 未只读挂载" % vs)
        if "examples/state" in vs:
            db_mounted = True
        if "models" in vs:
            models_mounted = True
        if re.search(r"(^|/)raw(/|$)|个人信息|隐私", vs):
            issues.append("卷 %s 挂了隐私目录（红线）" % vs)
    if not db_mounted:
        issues.append("没有挂载数据库卷（db 会缺失或被迫烤进镜像）")
    else:
        notes.append("db 走卷挂载")
    if not models_mounted:
        notes.append("模型未挂载（vec 链路会降级，仅在 WITH_VECTOR=0 时合理）")
    else:
        notes.append("模型走卷挂载")

    if str(svc.get("user", "")) in ("", "0", "root", "0:0"):
        issues.append("未指定非 root user")
    else:
        notes.append("user=%s" % svc.get("user"))
    if svc.get("read_only") is not True:
        issues.append("根文件系统非只读")
    else:
        notes.append("read_only=true")
    if svc.get("privileged") is True:
        issues.append("privileged=true（红线）")
    if "docker.sock" in json.dumps(doc, ensure_ascii=False):
        issues.append("挂了 docker.sock（红线）")

    return {
        "name": "compose 安全基线",
        "status": "FAIL" if issues else "PASS",
        "detail": "；".join(issues) if issues else "；".join(notes),
        "service": svc_name,
    }


def check_context(root):
    """模拟构建上下文：隐私红线 0 命中 + 必需文件齐备 + db/模型已排除。"""
    rules = load_dockerignore(root)
    if rules is None:
        return [{"name": "构建上下文收敛", "status": "FAIL", "detail": "缺 .dockerignore"}]

    kept, dropped = walk_context(root, rules)
    kept_set = set(kept)
    results = []

    # ① 隐私红线
    bad = []
    for rel in kept:
        parts = set(rel.split("/"))
        if parts & PRIVATE_PARTS:
            bad.append(rel)
        elif rel.lower().endswith(PRIVATE_SUFFIX) or os.path.basename(rel).lower().startswith(".env"):
            bad.append(rel)
    results.append({
        "name": "上下文隐私红线（raw/个人信息/.env/密钥）",
        "status": "FAIL" if bad else "PASS",
        "detail": ("命中 %d 个：%s" % (len(bad), bad[:5])) if bad else "0 命中（%d 个文件进上下文，%d 个被排除）" % (len(kept), len(dropped)),
    })

    # ② db 与模型不得在上下文内
    db_in = [r for r in kept if r.endswith((".db", ".db-wal", ".db-shm"))]
    model_in = [r for r in kept if "/models/" in r or r.startswith("modules/知识库索引/models/")]
    results.append({
        "name": "数据库 / 模型不进镜像层",
        "status": "FAIL" if (db_in or model_in) else "PASS",
        "detail": ("db:%d 个 %s；模型:%d 个 %s" % (len(db_in), db_in[:3], len(model_in), model_in[:3]))
                  if (db_in or model_in) else "0 个 *.db、0 个 models/ 文件进上下文（均走挂载卷）",
    })

    # ③ 必需文件齐备
    missing = [f for f in REQUIRED_IN_CONTEXT if f not in kept_set]
    results.append({
        "name": "入口与检索代码在上下文内",
        "status": "FAIL" if missing else "PASS",
        "detail": ("缺：%s" % missing) if missing else "api_server.py / kb_rag.py / mcp_server.py / requirements 齐备",
    })

    # ④ 上下文体积粗估（不含已排除项）
    total = 0
    for rel in kept:
        try:
            total += os.path.getsize(os.path.join(root, rel))
        except OSError:
            pass
    results.append({
        "name": "上下文体积粗估",
        "status": "PASS" if total < 200 * 1024 * 1024 else "WARN",
        "detail": "%.1f MB（代码+文档，不含模型与库）" % (total / 1048576.0),
    })
    return results, kept


# 只对这些扩展名做内容级隐私扫描（二进制与媒体由 privacy_scan 自身规则处理）
_TEXT_EXT = {".md", ".py", ".txt", ".yml", ".yaml", ".json", ".html", ".htm",
             ".sql", ".toml", ".ini", ".cfg", ".bat", ".sh", ".ps1", ".css", ".js",
             ".mermaid", ".csv"}


def check_context_content(root, kept):
    """上下文内容级隐私扫描（关键一项）。

    为什么不能只查路径名：本轮实测发现 modules/知识库索引/state/ 下 14 个评测产物
    含本机绝对路径与真实姓名，**它们不在 git 里**（所以 `privacy_scan --git-only`
    恒为 0 BLOCK），但 `COPY . .` 不看 git，会被真烤进镜像。
    路径名白名单挡不住这类文件，只有扫内容才发现。
    """
    sys.path.insert(0, os.path.join(root, "tools"))
    try:
        import privacy_scan as ps
    except Exception as e:  # noqa: BLE001
        return {"name": "上下文内容隐私扫描", "status": "WARN",
                "detail": "无法加载 privacy_scan（%s），本项未验" % e}

    terms = ps.load_terms(os.path.expanduser("~/.kb_privacy_terms.txt"))
    self_ignore = getattr(ps, "SELF_IGNORE", set())
    hits = []
    scanned = 0
    skipped = 0
    for rel in kept:
        # 与 privacy_scan 口径一致：规则定义文件自身豁免（它的正则里必然出现敏感词形状）
        if rel in self_ignore:
            skipped += 1
            continue
        if os.path.splitext(rel)[1].lower() not in _TEXT_EXT and os.path.basename(rel) not in ("Dockerfile", ".dockerignore"):
            continue
        try:
            ps.scan_file(os.path.join(root, rel), rel, ps.RULES, terms, hits, allow_media=False)
            scanned += 1
        except Exception:  # noqa: BLE001  单条失败不中断（脚本四条）
            continue
    hits = ps.dedupe(hits)
    blocks = [h for h in hits if h["level"] == "BLOCK"]
    warns = [h for h in hits if h["level"] == "WARN"]

    if blocks:
        files = sorted({h["file"] for h in blocks})
        return {"name": "上下文内容隐私扫描", "status": "FAIL",
                "detail": "%d 条 BLOCK，涉及 %d 个文件：%s" % (len(blocks), len(files), files[:5])}
    return {"name": "上下文内容隐私扫描", "status": "PASS",
            "detail": "扫 %d 个文本文件 · 0 BLOCK%s%s"
                      % (scanned,
                         "（豁免 %d 个规则定义文件）" % skipped if skipped else "",
                         "（%d WARN 待人工确认）" % len(warns) if warns else "")}



def check_docs(root):
    """README/文档是否写了启动命令（判据 5 的前置，真复现仍需人跑）。"""
    hits = []
    for name in ("README.md", "examples/quickstart.md"):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            txt = open(p, encoding="utf-8", errors="ignore").read()
            if "docker compose" in txt.lower() or "docker-compose" in txt.lower():
                hits.append(name)
    return {
        "name": "启动命令已写入文档",
        "status": "PASS" if hits else "FAIL",
        "detail": ("命中：%s" % hits) if hits else "README / quickstart 里没有 docker compose 启动命令",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    print("根目录：%s" % root)
    print("=" * 72)

    results = [check_dockerfile(root), check_compose(root)]
    ctx_results, kept = check_context(root)
    results += ctx_results
    results.append(check_context_content(root, kept))
    results.append(check_docs(root))

    for r in results:
        print("[%s] %s\n        %s" % (r["status"], r["name"], r["detail"]))

    print("=" * 72)
    print("⏳ 必须真 Docker 才能验（本轮不计入 PASS，缺 Docker 时如实挂起）：")
    for p in PENDING:
        print("   - %s" % p)

    fails = [r for r in results if r["status"] == "FAIL"]
    rc = 1 if fails else 0
    print("=" * 72)
    print("离线项合计 %d 项：PASS %d / WARN %d / FAIL %d → rc=%d"
          % (len(results),
             len([r for r in results if r["status"] == "PASS"]),
             len([r for r in results if r["status"] == "WARN"]),
             len(fails), rc))

    if args.json:
        print(json.dumps({"results": results, "pending": PENDING, "rc": rc}, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
