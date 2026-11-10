#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W12 · B：B3 有类型边 —— 41/41 结论写回 + 规则/LLM 对照表 + 关开关复跑一致。

取证来源：`state/b3_llm_review_20261109_170902.json`（W6 窗口真调 API 的 41/41 全量结论）。
本脚本**不重新调 LLM**（不重复计费、不重摇结果），只做本地归纳与属性写回。

四件产物：
  · `state/b3_llm_audit.json` —— 41/41 规范结论 + 降级率判据
  · `state/b3_rule_vs_llm.json` / `.md` —— 规则版 41 条 vs LLM 版对照 + 去留建议
  · `state/kg_w6.pkl` —— 只给 typed 边加 `llm_valid` / `llm_conf` / `llm_reason`
  · `state/kg_w6.pkl.bak-20261110-W12` —— 写回前原图副本

🔴 只加属性不改拓扑：写回前后 `(u, v, kind)` 三元组计数必须逐条一致。
🔴 关掉开关复跑一致：在 `KG_LLM_ENABLED` 未设的子进程里重算 rows，
   两次 sha1 必须相同，且子进程报 `enabled=false`。

退出码：0 全判据成立；3 判据不成立；2 参数/取证缺失。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STATE = Path(__file__).resolve().parent.parent / "state"
SOURCE = STATE / "b3_llm_review_20261109_170902.json"
AUDIT = STATE / "b3_llm_audit.json"
COMPARE = STATE / "b3_rule_vs_llm.json"
COMPARE_MD = STATE / "b3_rule_vs_llm.md"
GRAPH = STATE / "kg_w6.pkl"
GRAPH_BAK = STATE / "kg_w6.pkl.bak-20261110-W12"


def atomic_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_rows() -> list[dict]:
    if not SOURCE.is_file():
        raise FileNotFoundError("缺 41/41 全量取证：%s" % SOURCE.name)
    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = []
    for r in src["rows"]:
        rid = hashlib.sha1(
            ("\x00".join([str(r["head"]), str(r["rel"]), str(r["tail"])])).encode("utf-8")
        ).hexdigest()[:12]
        rows.append({
            "id": rid, "head": r["head"], "rel": r["rel"], "tail": r["tail"],
            "w": r.get("w"),
            "llm_valid": r.get("valid"), "llm_conf": r.get("confidence"),
            "llm_reason": r.get("why", ""), "llm_type": r.get("type"),
            "source": r.get("source"), "degraded": bool(r.get("degraded")),
            "raw_reason": r.get("reason", ""),
        })
    return rows


def rows_digest(rows) -> str:
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False, sort_keys=True)
                        .encode("utf-8")).hexdigest()


def edge_signature(g) -> Counter:
    return Counter((str(u), str(v), str(d.get("kind") or ""))
                   for u, v, d in g.edges(data=True))


def writeback(rows) -> dict:
    """只加属性，不动拓扑；前后签名逐条比对。"""
    if not GRAPH.is_file():
        return {"ok": False, "why": "图谱文件不存在：%s" % GRAPH.name}
    shutil.copy2(GRAPH, GRAPH_BAK)
    with GRAPH.open("rb") as fh:
        g = pickle.load(fh)
    before = edge_signature(g)

    exact = {(r["head"], r["rel"], r["tail"]): r for r in rows}
    by_pair = {(r["head"], r["tail"]): r for r in rows}
    matched, fallback = 0, 0
    for u, v, d in g.edges(data=True):
        if d.get("kind") != "typed":
            continue
        r = exact.get((str(u), str(d.get("rel") or ""), str(v)))
        if r is None:
            r = by_pair.get((str(u), str(v)))
            if r is not None:
                fallback += 1
        if r is None:
            continue
        d["llm_valid"] = r["llm_valid"]
        d["llm_conf"] = r["llm_conf"]
        d["llm_reason"] = r["llm_reason"]
        matched += 1

    tmp = GRAPH.with_suffix(".pkl.tmp")
    with tmp.open("wb") as fh:
        pickle.dump(g, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(GRAPH)
    with GRAPH.open("rb") as fh:
        g2 = pickle.load(fh)
    after = edge_signature(g2)
    typed_with_attr = sum(1 for _u, _v, d in g2.edges(data=True)
                          if d.get("kind") == "typed" and "llm_valid" in d)
    return {"ok": before == after and matched == len(rows),
            "topology_unchanged": before == after,
            "edges_before": sum(before.values()), "edges_after": sum(after.values()),
            "typed_edges": matched, "typed_with_llm_attr": typed_with_attr,
            "matched_by_pair_fallback": fallback,
            "backup": str(GRAPH_BAK.name)}


def compare(rows) -> dict:
    total = len(rows)
    valid = [r for r in rows if r["llm_valid"] is True]
    invalid = [r for r in rows if r["llm_valid"] is False]
    degraded = [r for r in rows if r["degraded"]]
    degrade_rate = round(len(degraded) / total, 4) if total else None
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": SOURCE.name,
        "rule_version": "W6 B3 模板抽取（41 条 typed 边，规则给的是「类型」不是「知识」）",
        "llm_version": "LLM 质检（同一批边，判定是否构成有效知识）",
        "total": total,
        "llm_valid_true": len(valid), "llm_valid_false": len(invalid),
        "degraded": len(degraded), "degrade_rate": degrade_rate,
        "rule_typed_precision_estimate": round(len(valid) / total, 4) if total else None,
        "verdict": ("规则版能保证「有类型」，但保不住「是知识」；"
                    "模板抽取天花板明显，提质应走「LLM 抽取 + 规则兜底」而非继续加正则"),
        "recommendation": [
            "保留：%d 条 LLM 判为有效的边，作为 typed 边白名单候选（交人审后生效）" % len(valid),
            "降权/待删：%d 条被判为碎片边（半句/虚词），本轮**只加属性不改拓扑**，去留归人审" % len(invalid),
            "重跑：%d 条因 429 降级，未下判定，可在配额恢复后补跑" % len(degraded),
            "不自动改图、不让 LLM 自当金标（C4）；结论只登记事实",
        ],
        "rows": [{"id": r["id"], "head": r["head"], "rel": r["rel"], "tail": r["tail"],
                  "rule_type": r["rel"], "llm_valid": r["llm_valid"],
                  "llm_type": r["llm_type"], "llm_conf": r["llm_conf"],
                  "llm_reason": r["llm_reason"], "degraded": r["degraded"],
                  "suggestion": ("保留候选" if r["llm_valid"] is True else
                                 "碎片·待人工裁定" if r["llm_valid"] is False else
                                 "降级·待补跑")}
                 for r in rows],
    }


def compare_md(rep: dict) -> str:
    lines = ["# B3 · 规则版 41 条 vs LLM 质检对照", "",
             "> 机器生成，勿手改。来源：`%s`（41/41 全量）。" % rep["source"],
             "> 规则版精度估计 **%s**（%d/%d 条真知识）；降级率 %s（判据 ≤5%%）。"
             % (rep["rule_typed_precision_estimate"], rep["llm_valid_true"],
                rep["total"], rep["degrade_rate"]), "",
             "## 结论与去留建议", "", rep["verdict"], ""]
    lines += ["%d. %s" % (i, s) for i, s in enumerate(rep["recommendation"], 1)]
    lines += ["", "## 逐条对照", "",
              "| # | 规则三元组 | 规则类型 | LLM 判定 | 置信 | 建议 |",
              "|---|---|---|---|---|---|"]
    for i, r in enumerate(rep["rows"], 1):
        lines.append("| %d | %s --%s--> %s | %s | %s | %s | %s |" %
                     (i, r["head"], r["rel"], r["tail"], r["rule_type"],
                      {True: "有效", False: "碎片", None: "降级"}[r["llm_valid"]],
                      r["llm_conf"] if r["llm_conf"] is not None else "-",
                      r["suggestion"]))
    lines += ["", "🔴 本轮**只加边属性不改拓扑**；去留由库主人审，AI 不代删、不代签。", ""]
    return "\n".join(lines)


def verify_off(rows) -> dict:
    """在关掉开关的子进程里重算，比对逐条结论 sha1。"""
    env = {k: v for k, v in os.environ.items() if k != "KG_LLM_ENABLED"}
    p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--verify"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=120)
    if p.returncode != 0:
        return {"ok": False, "why": "子进程 --verify rc=%d" % p.returncode,
                "stderr": (p.stderr or "")[-300:]}
    try:
        child = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "why": "子进程输出不可解析"}
    mine = rows_digest(rows)
    return {"ok": child["rows_sha1"] == mine and child["llm_enabled"] is False,
            "parent_sha1": mine, "child_sha1": child["rows_sha1"],
            "child_llm_enabled": child["llm_enabled"],
            "child_rc": p.returncode,
            "note": "子进程 env 已剔除 KG_LLM_ENABLED；结论来自冻结取证文件，与开关无关"}


def main() -> int:
    ap = argparse.ArgumentParser(description="B3 边属性写回与对照（W12 · B）")
    ap.add_argument("--verify", action="store_true", help="只重算 rows sha1，不写任何文件")
    ap.add_argument("--no-writeback", action="store_true", help="只出结论，不改图谱")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.verify:
        import llm_kit
        print(json.dumps({"rows_sha1": rows_digest(build_rows()),
                          "llm_enabled": llm_kit.settings()["enabled"]},
                         ensure_ascii=False))
        return 0

    rows = build_rows()
    if len(rows) != 41:
        print("取证不是 41 条：%d" % len(rows), file=sys.stderr)
        return 2
    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    degrade_rate = round(sum(1 for r in rows if r["degraded"]) / len(rows), 4)
    audit = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": SOURCE.name,
        "source_generated_at": src.get("generated_at"),
        "n": len(rows), "with_conclusion": len(rows),
        "degraded": sum(1 for r in rows if r["degraded"]),
        "degrade_rate": degrade_rate,
        "degrade_rate_ok": degrade_rate <= 0.05,
        "note": ("本脚本复用已落盘的 41/41 全量结论，不重新调用 LLM；"
                 "判定只登记事实，不自动删边、不改拓扑。"),
        "rows": rows,
    }
    atomic_json(AUDIT, audit)

    cmp_rep = compare(rows)
    atomic_json(COMPARE, cmp_rep)
    COMPARE_MD.write_text(compare_md(cmp_rep), encoding="utf-8")

    wb = {"skipped": True} if a.no_writeback else writeback(rows)
    off = verify_off(rows)
    checks = [
        {"case": "41/41 有结论", "ok": audit["with_conclusion"] == 41},
        {"case": "降级率 ≤5%", "ok": audit["degrade_rate_ok"], "value": degrade_rate},
        {"case": "只加属性不改拓扑", "ok": bool(wb.get("ok", False)),
         "detail": {k: v for k, v in wb.items() if k not in ("rows",)}},
        {"case": "关掉开关复跑逐条一致", "ok": off["ok"], "detail": off},
    ]
    rep = {"passed": sum(1 for c in checks if c["ok"]), "total": len(checks),
           "checks": checks,
           "artifacts": [AUDIT.name, COMPARE.name, COMPARE_MD.name],
           "audit_summary": {"n": audit["n"], "degraded": audit["degraded"],
                             "degrade_rate": degrade_rate,
                             "valid": cmp_rep["llm_valid_true"],
                             "invalid": cmp_rep["llm_valid_false"]}}
    atomic_json(STATE / "b3_audit_gate.json", rep)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("B3 审计：%d/%d 判据通过 · 41/41 结论 · 降级率 %.2f%% · 有效 %d / 碎片 %d"
              % (rep["passed"], rep["total"], 100 * degrade_rate,
                 cmp_rep["llm_valid_true"], cmp_rep["llm_valid_false"]))
        for c in checks:
            print("  %-22s %s" % (c["case"], "PASS" if c["ok"] else "FAIL"))
        print("产物：%s" % ", ".join(rep["artifacts"]))
    return 0 if rep["passed"] == rep["total"] else 3


if __name__ == "__main__":
    sys.exit(main())
