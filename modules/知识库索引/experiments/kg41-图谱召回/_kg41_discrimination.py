#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""KG-41 多跳集「关键词门禁」体检（一次性审计工具）

对齐 `wiki/技术/知识库检索评测体系-六桶去饱和与配对检验.md` §3 G1 硬门禁：
    mode=kw 跑一次，看 gt 是否在 top20
    - gt 全部在 top20      → 关键词单独就解得出，**无区分力**（1-hop 救不出任何东西）
    - 至少一条 gt 不在 top20 → 有区分力（单路召回漏了它，1-hop 才有发挥空间）

输出每条题的 gt 在 kw top20 里的名次，并汇总「有区分力 / 无区分力」条数。

用法：
    python _kg41_discrimination.py
"""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.join(_HERE, "state", "kb_eval_multihop_30.json")
PY = sys.executable
TOPK = 20


def run_one(item):
    """跑一条 query，返回 {gt_path: rank or None}"""
    cmd = [PY, "-X", "utf8", "kb_rag.py", "search",
           "--query", item["query"], "--mode", "kw",
           "--topk", str(TOPK), "--json"]
    try:
        out = subprocess.run(cmd, cwd=_HERE, capture_output=True, timeout=180)
        raw = out.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return item["id"], {p: None for p in item["gt"]}, "TIMEOUT"
    # 输出可能带前置日志行，取第一行能解析成 JSON 的
    payload = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
    if payload is None:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                payload = json.loads(m.group(0))
            except ValueError:
                payload = None
    if payload is None:
        return item["id"], {p: None for p in item["gt"]}, "NO_JSON"
    ranks = {}
    hit = {r["path"]: r["rank"] for r in payload.get("results", [])}
    for p in item["gt"]:
        ranks[p] = hit.get(p)
    return item["id"], ranks, payload.get("note", "")


def main():
    with open(EVAL, encoding="utf-8") as f:
        data = json.load(f)

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(run_one, data))

    print("%-6s %-9s %-9s  %s" % ("id", "gt命中数", "判定", "各 gt 在 kw-top20 的名次"))
    print("-" * 78)
    disc = 0     # 有区分力：至少一条 gt 不在 top20
    nodisc = 0   # 无区分力：全部 gt 都在 top20
    r5_full = 0  # R@5 已满格：全部 gt 都在 top5 → 关键词路已够，1-hop 无增益空间
    errs = []
    for (qid, ranks, note), item in zip(rows, data):
        got = sum(1 for v in ranks.values() if v)
        allin = got == len(item["gt"])
        in5 = all(1 <= (v or 999) <= 5 for v in ranks.values())
        if in5:
            r5_full += 1
        if note in ("TIMEOUT", "NO_JSON"):
            errs.append(qid)
            verdict = "ERR"
        elif allin:
            nodisc += 1
            verdict = "全命中"
        else:
            disc += 1
            verdict = "有区分力"
        cells = []
        for p in item["gt"]:
            r = ranks[p]
            cells.append(("第%d" % r) if r else "未进20")
        print("%-6s %-9s %-9s  %s" % (qid, "%d/%d" % (got, len(item["gt"])),
                                      verdict, " | ".join(cells)))

    print("-" * 78)
    print("有区分力（至少一条 gt 被纯关键词漏掉 top20）：%d/%d" % (disc, len(data)))
    print("无区分力（全部 gt 都在 kw top20）            ：%d/%d" % (nodisc, len(data)))
    print("R@5 已满格（全部 gt 都在 kw top5）           ：%d/%d" % (r5_full, len(data)))
    if errs:
        print("跑失败：%s" % errs)
    print("")
    print("说明：无区分力不代表题错，而是说明该条用纯关键词就能解，1-hop 对它的增量空间≈0；")
    print("      #41 判据是 R@5 ≥ +3pp，所以真正要看的是「R@5 未满格」的条数：")
    print("      只有这批 gt 掉在 top5 之外的题，才可能被 1-hop 邻居召回拉上来。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
