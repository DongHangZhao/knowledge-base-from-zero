#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kg_gate — 1-hop 邻居召回 + **写死**的「做/不做」决策门（#41）

为什么决策门必须写死（不许环境变量覆盖）：
    实验最容易被"调阈值调到通过"。`MIN_R5_DELTA=0.03` 与 `MAX_P95_DELTA_MS=150`
    是**开工前**就定下的判据（+3pp 收益 / +150ms 延迟预算），一旦允许 env 覆盖，
    "通过"就变成了谈判结果而不是测量结果。所以它们是**模块级常量**，本文件里
    没有任何 `os.environ.get` 去读这两个数。

    判定（两个都满足才算 PASS）：
        r5_delta    >=  +0.03      （收益：Recall@5 至少 +3 个百分点）
        p95_delta_ms <=  +150      （成本：95 分位延迟最多多 150ms）
    边界按"含等号"处理：0.03 / 150 算通过（有测试钉 4 个边界）。

退出码：0 = PASS（允许下期做插件层）；6 = FAIL（归档不做）。6 永不出现在 CI。

用法：
    python kg_gate.py --baseline base.json --treat treat.json
    python kg_gate.py --baseline base.json --treat treat.json --write-decision docs/decisions/KG-41.md

作者：FallenPetal ｜ 许可：KBL-1.0（非商用，商用见 COMMERCIAL.md）
"""

import argparse
import datetime
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

# ============ 写死的判据（不许改成环境变量，见模块 docstring）============
MIN_R5_DELTA = 0.03          # R@5 至少 +3 个百分点
MAX_P95_DELTA_MS = 150.0     # p95 延迟最多 +150ms
_EPS = 1e-9                  # 浮点比较容差：0.88-0.85 在二进制里可能差 2e-17

HERE = os.path.dirname(os.path.abspath(__file__))


# ============ 1-hop 邻居召回 ============

def _query_entities(conn, q):
    """把查询里出现的实体取出来（两路：规则抽取 + 侧表里的实体名子串命中）。"""
    import kg_extract
    names = {e for e, _t in kg_extract.extract_entities(q)}
    # 侧表里已有的实体名，若出现在查询里也算（覆盖规则抽不到的口语说法）
    try:
        for (ent,) in conn.execute(
                "SELECT DISTINCT entity FROM %s" % kg_extract.TABLE):
            if ent and ent.lower() in (q or "").lower():
                names.add(ent)
    except Exception:
        pass                    # 侧表还没建 = 没实体，正常
    return sorted(names)


def expand_1hop(conn, q, topk=10, exclude_docs=None):
    """查询实体 → 共享这些实体的 chunk（1-hop），返回 kb_rag.add() 可直接消费的列表。

    Args:
        conn: sqlite 连接（只读即可）。
        q: 查询串。
        topk: 最多返回多少条。
        exclude_docs: 已命中的 `doc_id` 集合 —— **排除"自己"**。图实验里如果不过滤，
            这路只会给已有文档加 RRF 分（等于没做实验），必须由调用方传入。

    Returns:
        list[dict]，每项含 `doc_id / path / title / chunk_id / entity / shared`。
        按「共享实体数 desc, 路径」排序。侧表不存在/无实体 → `[]`（不抛）。
    """
    import kg_extract
    if os.environ.get("KB_KG") != "1":
        return []
    q = (q or "").strip()
    if not q:
        return []
    ents = _query_entities(conn, q)
    if not ents:
        return []
    excl = set(exclude_docs or ())

    # 逐个实体查（每个实体能命中很多 chunk，一次 IN 查询更省，但 SQLite 变量上限 999）
    shared = {}
    for i in range(0, len(ents), 200):
        part = ents[i:i + 200]
        try:
            rows = conn.execute(
                "SELECT ce.chunk_id, ce.entity, c.doc_id, d.path, d.title "
                "FROM %s ce JOIN chunks c ON c.id = ce.chunk_id "
                "JOIN docs d ON d.id = c.doc_id "
                "WHERE ce.entity IN (%s)" % (kg_extract.TABLE, ",".join("?" * len(part))),
                part).fetchall()
        except Exception:
            return []
        for chunk_id, entity, doc_id, path, title in rows:
            if doc_id in excl:
                continue
            k = (doc_id, chunk_id)
            item = shared.get(k)
            if item is None:
                item = {"doc_id": doc_id, "chunk_id": chunk_id, "path": path or "",
                        "title": title or "", "entities": []}
                shared[k] = item
            if entity not in item["entities"]:
                item["entities"].append(entity)

    out = []
    for (doc_id, chunk_id), it in shared.items():
        it["shared"] = len(it["entities"])
        it["entity"] = it["entities"][0] if it["entities"] else ""
        out.append(it)
    out.sort(key=lambda x: (-x["shared"], x["path"], x["chunk_id"]))
    return out[:max(1, int(topk or 1))]


# ============ 决策门 ============

def _pick(d, *names, default=None):
    """从 eval 结果 dict 里取指标，容忍平铺/嵌套 summary 与常见别名。"""
    if not isinstance(d, dict):
        return default
    pools = [d, d.get("summary") or {}, d.get("res") or {}]
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        for n in names:
            if n in pool and isinstance(pool[n], (int, float)):
                return float(pool[n])
    return default


def _r5(d):
    """Recall@5：接受 0-1 小数或 0-100 百分数（>1 时按百分数折算）。"""
    v = _pick(d, "r5", "recall5", "recall@5", "Recall@5", "recall")
    if v is None:
        return None
    return v / 100.0 if v > 1.0 else v


def _p95(d):
    v = _pick(d, "p95", "p95_ms", "latency_p95", "latency_p95_ms")
    return v


def decide(baseline, treat):
    """判定「做 / 不做」。

    Args:
        baseline: dict，含 r5（0-1 或百分数）与 p95（ms）。
        treat:    同结构。

    Returns:
        `{pass, r5_delta, p95_delta_ms, verdict, reasons, baseline, treat}`
        `pass=True` 要求 `r5_delta >= 0.03` **且** `p95_delta_ms <= 150`（含等号）。
    """
    b_r5, t_r5 = _r5(baseline), _r5(treat)
    b_p95, t_p95 = _p95(baseline), _p95(treat)
    reasons = []
    if b_r5 is None or t_r5 is None:
        reasons.append("缺 R@5 指标（baseline=%s treat=%s）" % (b_r5, t_r5))
        return {"pass": False, "r5_delta": None, "p95_delta_ms": None,
                "verdict": "无法判定（数据缺失）", "reasons": reasons,
                "baseline": baseline, "treat": treat}
    if b_p95 is None or t_p95 is None:
        reasons.append("缺 p95 延迟指标（baseline=%s treat=%s）" % (b_p95, t_p95))
        return {"pass": False, "r5_delta": None, "p95_delta_ms": None,
                "verdict": "无法判定（数据缺失）", "reasons": reasons,
                "baseline": baseline, "treat": treat}

    r5_delta = t_r5 - b_r5
    p95_delta = t_p95 - b_p95
    ok_r5 = r5_delta >= MIN_R5_DELTA - _EPS
    ok_p95 = p95_delta <= MAX_P95_DELTA_MS + _EPS
    if ok_r5:
        reasons.append("收益达标：R@5 %+.1fpp ≥ +%.0fpp" % (r5_delta * 100, MIN_R5_DELTA * 100))
    else:
        reasons.append("收益不足：R@5 %+.1fpp < +%.0fpp" % (r5_delta * 100, MIN_R5_DELTA * 100))
    if ok_p95:
        reasons.append("延迟达标：p95 %+.0fms ≤ +%.0fms" % (p95_delta, MAX_P95_DELTA_MS))
    else:
        reasons.append("延迟超标：p95 %+.0fms > +%.0fms" % (p95_delta, MAX_P95_DELTA_MS))
    passed = ok_r5 and ok_p95
    return {"pass": passed,
            "r5_delta": round(r5_delta, 6),
            "p95_delta_ms": round(p95_delta, 1),
            "verdict": "做插件层" if passed else "归档不做（不实现插件层）",
            "reasons": reasons,
            "baseline": {"r5": b_r5, "p95": b_p95},
            "treat": {"r5": t_r5, "p95": t_p95}}


def render_table(res):
    """判定表（stdout 与决策文档共用）。"""
    b, t = res.get("baseline") or {}, res.get("treat") or {}
    lines = [
        "| 指标 | baseline（无 kg 路） | treat（+1-hop） | delta | 阈值 | 判定 |",
        "|---|---|---|---|---|---|",
    ]

    def fmt(v, pct=False):
        if v is None:
            return "—"
        return ("%.1f%%" % (v * 100)) if pct else ("%.0fms" % v)

    r5d = res.get("r5_delta")
    p95d = res.get("p95_delta_ms")
    lines.append("| Recall@5 | %s | %s | %s | ≥ +3.0pp | %s |" % (
        fmt(b.get("r5"), True), fmt(t.get("r5"), True),
        ("%+.1fpp" % (r5d * 100)) if r5d is not None else "—",
        "✅" if (r5d is not None and r5d >= MIN_R5_DELTA - _EPS) else "🔴"))
    lines.append("| p95 延迟 | %s | %s | %s | ≤ +150ms | %s |" % (
        fmt(b.get("p95")), fmt(t.get("p95")),
        ("%+.0fms" % p95d) if p95d is not None else "—",
        "✅" if (p95d is not None and p95d <= MAX_P95_DELTA_MS + _EPS) else "🔴"))
    return "\n".join(lines)


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _squash(payload, mode=None):
    """从 eval_rag 的结果 json 里取出**指定 mode** 的指标层。

    为什么需要这层：eval_rag 写的是
        {summary: {<mode>: {recall, p95, ...}}, sets: {<set>: {res: {<mode>: ...}}}}
    而本模块的 `_pick` 只在「顶层 / summary 一层 / res 一层」找 `recall`、`p95`。
    两处口径从没对齐过 —— 直接把 payload 丢进来，决策门恒返回「无法判定」。

    Args:
        payload: eval_rag 的 --json 输出（也接受历史版本的无 summary 平铺 dict）。
        mode:    要取哪一路（如 `unified` / `kg_treat`）。summary 只有一个键时可省略。

    Returns:
        指标 dict；找不到时原样返回 payload，交由 `decide` 报「数据缺失」。
    """
    if not isinstance(payload, dict):
        return payload
    summ = payload.get("summary")
    if not isinstance(summ, dict) or not summ:
        return payload                      # 历史平铺格式，直接交给 _pick
    if mode:
        return summ.get(mode) or payload
    if len(summ) == 1:
        return next(iter(summ.values()))
    return payload                          # 多路又没指定 → 让 decide 报缺数，别猜


def write_decision(path, res, baseline_path, treat_path):
    """生成决策文档（`docs/decisions/KG-41.md` 的模板 + 实测数字）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    md = []
    md.append("# #41 知识图谱 1pt 实验 · 决策记录\n")
    md.append("> 判定时间：%s ｜ 判据**开工前写死**：R@5 ≥ +3.0pp **且** p95 ≤ +150ms\n"
              % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    md.append("## 一、结论\n")
    md.append("**%s**（pass = `%s`）\n" % (res["verdict"], res["pass"]))
    for r in res["reasons"]:
        md.append("- %s" % r)
    md.append("")
    md.append("## 二、实测数字\n")
    md.append(render_table(res))
    md.append("")
    md.append("## 三、复现命令\n")
    md.append("```bash")
    md.append("# 前置：索引四表必须一致（否则延迟虚高会污染结论）")
    md.append("python -c \"import kb_index,vec_tables;c=kb_index.connect(readonly=True);"
              "print(vec_tables.diff_report(c)['ok'])\"   # 必须 True")
    md.append("")
    md.append("# baseline = KB_KG 未设；treat = KB_KG=1")
    md.append("python eval_rag.py --set multihop --json /tmp/base.json")
    md.append("KB_KG=1 python eval_rag.py --set multihop --json /tmp/treat.json")
    md.append("")
    md.append("python kg_gate.py --baseline %s --treat %s" % (baseline_path, treat_path))
    md.append("```")
    md.append("")
    md.append("## 四、回滚（一条命令，不留残留）\n")
    md.append("```bash")
    md.append("DROP TABLE IF EXISTS chunk_entities; DROP INDEX IF EXISTS idx_ent;")
    md.append("```")
    md.append("再删 `kg_extract.py` / `kg_gate.py` / `_test_kg.py` / "
              "`eval_sets/multihop_30.example.json`，以及 `kb_rag.py` 里 `KB_KG == \"1\"` 那几行。")
    md.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="#41 图谱实验决策门（阈值写死：+3pp / +150ms）")
    ap.add_argument("--baseline", required=True, help="baseline 结果 json（无 kg 路）")
    ap.add_argument("--treat", required=True, help="treat 结果 json（+1-hop）")
    ap.add_argument("--write-decision", default=None, metavar="PATH",
                    help="把判定写成 docs/decisions/KG-41.md")
    ap.add_argument("--mode", default=None,
                    help="取 eval json 里哪一路的指标（如 unified / kg_treat）；单路时可省略")
    ap.add_argument("--baseline-mode", default=None,
                    help="baseline 单独指定 mode（两路名字不同时用）")
    args = ap.parse_args(argv)

    try:
        base, treat = _load(args.baseline), _load(args.treat)
    except Exception as e:
        print("[ERR] 读不到结果文件：%s" % str(e)[:120], file=sys.stderr)
        return 2
    base = _squash(base, args.baseline_mode or args.mode)
    treat = _squash(treat, args.mode)

    res = decide(base, treat)
    print("== #41 图谱 1pt 实验 · 决策门 ==")
    print(render_table(res))
    print("")
    for r in res["reasons"]:
        print("  · %s" % r)
    print("\n判定：%s（pass=%s）" % (res["verdict"], res["pass"]))
    if args.write_decision:
        p = write_decision(args.write_decision, res, args.baseline, args.treat)
        print("决策文档：%s" % p)
    return 0 if res["pass"] else 6


if __name__ == "__main__":
    sys.exit(main())
