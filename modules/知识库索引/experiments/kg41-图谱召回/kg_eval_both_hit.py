#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kg_eval_both_hit — #41 的「全命中」口径评测（沙盘专用）

## 为什么 eval_rag 的 R@5 测不出多跳难度

    eval_rag 的 hit 判定是 `metrics.hit_rate_at_k`：**任一** gt 落在 top-k 就算命中。
    而多跳集的 gt = [第一跳文档, 第二跳文档]，第一跳（总览页）在库里被提问得最多、
    关键词路一抓就有 —— 于是 baseline R@5 直接 100%，treat 没有提升空间。

    实测就是这么回事：
        baseline  R@5 = 100.0%（30/30 条都有任一 gt 命中）
        treat     R@5 = 100.0%（同上）

    100% 的基线说明这个口径**测不出 1-hop 的价值**，不是「图谱没用」。

## 本脚本用三种口径分别算

    any@k   —— 任一 gt 命中（= eval_rag 现有口径，作为对照）
    all@k   —— **所有** gt 都在 top-k（这才是「多跳答对」）
    jump@k  —— 第一跳命中、第二跳**没**命中（= 1-hop 应该救回来的那一批）

    只有 `all@k` 和 `jump@k` 能回答「1-hop 有没有用」。

跑法：
    python kg_eval_both_hit.py --db <沙盘> --set kb_eval_multihop_30.json --k 5
"""

import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--set", required=True, help="评测集 json 路径")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--mode", default="unified", choices=["unified", "fused_tri"])
    args = ap.parse_args(argv)

    os.environ["KB_DB_PATH"] = os.path.abspath(args.db)
    # 🔴 必须用**仓库侧**代码跑：生产侧 kb_rag 故意没移植 rewrite 参数与 KG 路
    # （#41 结论未出前不把未验代码放进生产），拿生产侧跑 treat 组会直接
    # `search_unified() got an unexpected keyword argument 'rewrite'`。
    # 库指向沙盘，代码用仓库 —— 这样两组都跑得通，且不污染生产。
    repo_dir = os.environ.get("KB_REPO_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    if not os.path.isdir(repo_dir):
        print("[ERR] 找不到仓库侧模块目录：%s（用 KB_REPO_DIR 指定）" % repo_dir, file=sys.stderr)
        return 2
    sys.path.insert(0, repo_dir)
    import sqlite3
    import kb_rag
    import kb_index

    conn = kb_index.connect(os.path.abspath(args.db), readonly=True)
    items = json.load(open(args.set, encoding="utf-8"))
    print("[both] 评测集 %s ｜ %d 条 ｜ k=%d ｜ mode=%s"
          % (os.path.basename(args.set), len(items), args.k, args.mode))

    def run(kg_on):
        os.environ["KB_KG"] = "1" if kg_on else "0"
        n = len(items)
        any_hit = all_hit = jump = first_only = 0
        rows = []
        for it in items:
            q = it["query"]
            gts = it["gt"] if isinstance(it.get("gt"), list) else [it["gt"]]
            try:
                res, _note = kb_rag.search_unified(conn, q, topk=args.k, mode="fused",
                                                   rerank=False, rewrite="off")
            except Exception as e:
                res = []
                print("  [ERR] %s: %s" % (q[:20], str(e)[:60]))
            paths = [r.get("path") for r in res]
            hit_any = any(g in paths for g in gts)
            hit_all = all(g in paths for g in gts)
            f_hit = bool(gts) and gts[0] in paths
            s_hit = bool(gts) and gts[-1] in paths
            any_hit += hit_any
            all_hit += hit_all
            if f_hit and not s_hit:
                jump += 1                          # 第一跳中、第二跳没中
            if f_hit and not s_hit and len(gts) > 1:
                first_only += 1
            rows.append((it.get("id", "?"), hit_any, hit_all, f_hit, s_hit, paths[:3]))
        return {"n": n, "any": any_hit, "all": all_hit, "jump": jump,
                "first_only": first_only, "rows": rows}

    base = run(False)
    treat = run(True)

    def line(tag, r):
        print("  %-10s any@%d=%d/%d (%.1f%%)  all@%d=%d/%d (%.1f%%)  第一跳中第二跳空=%d"
              % (tag, args.k, r["any"], r["n"], 100.0 * r["any"] / r["n"],
                 args.k, r["all"], r["n"], 100.0 * r["all"] / r["n"], r["jump"]))

    print("\n=== 三口径对照 ===")
    line("baseline", base)
    line("+1hop", treat)
    print("\n  Δall@%d = %+d 条   Δ第一跳中第二跳空 = %+d 条"
          % (args.k, treat["all"] - base["all"], treat["jump"] - base["jump"]))

    print("\n=== 逐条（只列「全命中」有变化的） ===")
    changed = 0
    for rb, rt in zip(base["rows"], treat["rows"]):
        if rb[2] != rt[2]:
            changed += 1
            print("  %s  all: %s → %s ｜ q=%s" % (rb[0], rb[2], rt[2], ""))
    if not changed:
        print("  （无变化 —— 说明 1-hop 未改变任何一条的全命中状态）")

    print("\n=== baseline 里「第一跳中、第二跳空」的条目（1-hop 的机会窗口） ===")
    for rb in base["rows"]:
        if rb[3] and not rb[4]:
            print("  %s  top3=%s" % (rb[0], [p.split("/")[-1] for p in rb[5]]))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
