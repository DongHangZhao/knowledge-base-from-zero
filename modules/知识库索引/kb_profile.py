#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_profile — 人设配置矩阵：personal / team / enterprise 一句话切换（D1）

问题：本引擎有十几个 `KB_*` 环境变量（改写档位、m3 补写、备份天数、PDF 并发…），
新用户不知道该设什么，老用户每换一台机器就要重抄一遍；而"企业档要求 m3 齐"这类
**策略差异**藏在文档里，代码完全不知情。

做法：只提供**默认值**，不提供"配置中心"。

    PROFILES[name] = {KB_KEY: 默认值}     # 见下方矩阵
    apply(name)  →  对每对 kv 执行 os.environ.setdefault(k, str(v))

🔴 三条铁律（违反任何一条这个模块就该被删掉）：
  1. **显式 env 恒胜**：只用 `setdefault`。用户 `KB_REWRITE=off` 之后再
     `apply("enterprise")`，`KB_REWRITE` 仍是 `off`。人设是"没说话时的默认"，
     不是"覆盖你的选择"。
  2. **禁止导入期 apply**：`import kb_profile` 不得改动任何环境变量。
     只允许在 CLI 入口 / 进程启动时显式调用一次 `apply()`。否则任何
     `import kb_profile` 的库代码都会把全局配置悄悄改掉（最难查的一类 bug）。
  3. **绝不承载隐私 / 云端 / 法务项**：矩阵里不得出现 `*PRIVACY*` / `*CLOUD*` /
     `*TERMS*` 键。这些是**红线与承诺**，不能变成"某个档位可以放松"的开关
     （`_test_kb_profile.py` 有硬断言）。

用法：
    python kb_profile.py --show                       # 打印三档矩阵
    python kb_profile.py --apply team                 # 应用并打印生效结果
    python kb_profile.py --apply enterprise --json    # 机器可读（含 env/profile 来源）
    KB_PROFILE=team python kb_rag.py search --query x # 进程启动时按档铺默认值

作者：FallenPetal ｜ 许可：KBL-1.0（非商用，商用见 COMMERCIAL.md）
"""

import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

#: 三档默认值矩阵（改这里 = 改产品默认行为，必须同步 CHANGELOG）
#: 依据：`docs` 里三种人设的真实差异只在「要不要跨语向量 / m3 是否强制齐 /
#: 备份留多久 / PDF 并发几路」。其余一律三档一致 —— 差异越多越难解释。
PROFILES = {
    "personal": {
        "KB_REWRITE": "l0",
        "KB_HYDE": "0",
        "KB_BACKFILL_M3": "auto",
        "KB_VEC_ROUTE": "0",
        "KB_RERANK_DEVICE": "auto",
        "KB_BACKUP_DAYS": "3",
        "KB_RERANK_POOL": "30",
        "KB_PDF_WORKERS": "2",
        "KB_KG": "0",
    },
    "team": {
        "KB_REWRITE": "l1",
        "KB_HYDE": "0",
        "KB_BACKFILL_M3": "auto",
        "KB_VEC_ROUTE": "1",
        "KB_RERANK_DEVICE": "auto",
        "KB_BACKUP_DAYS": "14",
        "KB_RERANK_POOL": "30",
        "KB_PDF_WORKERS": "4",
        "KB_KG": "0",
    },
    "enterprise": {
        "KB_REWRITE": "l1",
        "KB_HYDE": "0",          # LLM 出口另议，本期三档一律关
        "KB_BACKFILL_M3": "1",   # 企业档要求 m3 齐（build 会在全量漂移时 rc=3）
        "KB_VEC_ROUTE": "1",
        "KB_RERANK_DEVICE": "auto",   # 个人档「有 GPU 用 GPU」策略三档一致
        "KB_BACKUP_DAYS": "30",
        "KB_RERANK_POOL": "30",
        "KB_PDF_WORKERS": "4",
        "KB_KG": "0",            # 实验期一律 0
    },
}

DEFAULT_PROFILE = "personal"

#: 明确禁止出现在矩阵里的键（隐私 / 云端 / 法务红线）
FORBIDDEN_KEY_PARTS = ("PRIVACY", "CLOUD", "TERMS")


def active(env=None):
    """当前档位名（不读盘、不改环境）。非法值回落 personal 并保持安静。"""
    env = os.environ if env is None else env
    name = (env.get("KB_PROFILE") or "").strip().lower()
    return name if name in PROFILES else DEFAULT_PROFILE


def apply(name=None, env=None):
    """把人设默认值铺进环境（**只 setdefault**），返回生效明细。

    Args:
        name: `personal|team|enterprise`；None → 读 env 的 `KB_PROFILE`，再回落 personal。
        env: 注入用的映射（默认 `os.environ`，单测可直接传 dict）。

    Returns:
        dict: `{KEY: {"value": <当前生效值>, "from": "env"|"profile"}}`
              `from="env"` 表示这个键在 apply 之前就已被显式设置（人设不覆盖）。
    """
    env = os.environ if env is None else env
    target = (name or env.get("KB_PROFILE") or DEFAULT_PROFILE)
    target = str(target).strip().lower()
    if target not in PROFILES:
        target = DEFAULT_PROFILE
    matrix = PROFILES[target]
    out = {}
    for k, v in matrix.items():
        had = k in env
        env.setdefault(k, str(v))
        out[k] = {"value": env[k], "from": "env" if had else "profile"}
    return out


def matrix():
    """三档矩阵（供 `--show` / 文档生成）。返回 [{key, personal, team, enterprise}]。"""
    keys = sorted(PROFILES[DEFAULT_PROFILE])
    rows = []
    for k in keys:
        row = {"key": k}
        for name in PROFILES:
            row[name] = PROFILES[name].get(k, "")
        rows.append(row)
    return rows


def validate():
    """矩阵自检：三档 key 集合一致 + 无隐私/云端/法务键。返回问题列表（空=健康）。"""
    problems = []
    base = set(PROFILES[DEFAULT_PROFILE])
    for name, m in PROFILES.items():
        if set(m) != base:
            problems.append("档位 %s 的 key 集合与 %s 不一致：多 %s 少 %s" % (
                name, DEFAULT_PROFILE, sorted(set(m) - base), sorted(base - set(m))))
        for k in m:
            if any(p in k.upper() for p in FORBIDDEN_KEY_PARTS):
                problems.append("档位 %s 含红线键 %s（隐私/云端/法务不得进矩阵）" % (name, k))
    return problems


def _print_show():
    rows = matrix()
    names = list(PROFILES)
    w = max([len("key")] + [len(r["key"]) for r in rows]) + 2
    head = "%-*s" % (w, "key") + "".join("%-14s" % n for n in names)
    print("人设配置矩阵（当前生效档：%s；env 已设置的值不会被覆盖）" % active())
    print(head)
    print("-" * len(head))
    for r in rows:
        print("%-*s" % (w, r["key"]) + "".join("%-14s" % r[n] for n in names))
    print("\n用法：python kb_profile.py --apply team ｜ 或进程内 kb_profile.apply(\"team\")")


def main(argv=None):
    ap = argparse.ArgumentParser(description="人设配置矩阵（personal / team / enterprise）")
    ap.add_argument("--show", action="store_true", help="打印三档矩阵（默认行为）")
    ap.add_argument("--json", action="store_true", help="机器可读输出（含 env/profile 来源）")
    ap.add_argument("--apply", metavar="NAME", default=None,
                    help="应用某档默认值（只 setdefault，不覆盖你已设的 env）")
    ap.add_argument("--check", action="store_true", help="只做矩阵自检（供 CI）")
    args = ap.parse_args(argv)

    problems = validate()
    if problems:
        for p in problems:
            print("[FAIL] %s" % p)
        return 1
    if args.check:
        print("[PASS] 矩阵自检通过：三档 key 集合一致、无隐私/云端/法务键")
        return 0

    if args.apply:
        detail = apply(args.apply)
        if args.json:
            print(json.dumps(detail, ensure_ascii=False, indent=2))
        else:
            print("已应用档位：%s" % args.apply)
            for k, v in detail.items():
                mark = "（env 已设，保留）" if v["from"] == "env" else ""
                print("  %-18s = %-8s %s" % (k, v["value"], mark))
        return 0

    if args.json:
        # 只读：不给 --apply 时输出矩阵本身（**不动环境**，和 --show 行为一致）
        print(json.dumps({"active": active(), "matrix": matrix()},
                         ensure_ascii=False, indent=2))
        return 0

    _print_show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
