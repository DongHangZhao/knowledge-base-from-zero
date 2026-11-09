#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W6 · E 多跳评测（题目先冻结再跑，判定规则提前写死）

两条纪律（W6 卡 §三 E / §六）：
  · `--freeze` 与 `--run` 分离：run 时校验题库 sha1，题目一旦冻结不许挑、不许改；
  · 判定规则写死在 RULES 里：R@5 ≥ +3pp **且** McNemar p<0.05 → 「值得接」，
    否则一律记「能力齐备、增益不显著、不接主链路」。两种都算完成，不许凑好看改判据。

题库为**半自动标注**：题面由脱敏语料里真实跨文档共现实体生成，gt 追到具体
path + chunk_id + 块序号；`human_judged=0` / `UNCALIBRATED`，在 manifest 里明写。

用法：
  python eval_multihop.py --freeze --limit 100
  python eval_multihop.py --run
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pickle
import sqlite3
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kg_search as S  # noqa: E402

DB = os.path.join(HERE, "state", "kg_w6.db")
GRAPH = os.path.join(HERE, "state", "kg_w6.pkl")
ESET = os.path.join(HERE, "eval_sets", "kg_multihop_v2026.11.json")
MANIFEST = os.path.join(HERE, "eval_sets", "manifest.json")
RESULT = os.path.join(HERE, "state", "eval_result.json")
STATS_PY = os.path.normpath(os.path.join(HERE, "..", "..", "outputs", "知识库运维", "eval_stats.py"))

# 🔴 提前写死，不许事后改
RULES = {"min_delta_pp": 3.0, "max_p": 0.05, "topk": 5}
# 题面只给**端点实体**，不直接给桥实体（否则词法基线一眼答对，评测集饱和）
TEMPLATES = [
    "「%s」与「%s」分别在哪两篇文档里被论述？",
    "哪两篇文档分别讨论了「%s」和「%s」？",
    "跨文档看，「%s」和「%s」的论述各落在哪一篇？",
]


def load_mcnemar():
    """复用轮子：outputs/知识库运维/eval_stats.py::mcnemar_exact（先查轮子，不重写）。"""
    if not os.path.isfile(STATS_PY):
        raise SystemExit("[ERR] 找不到统计轮子 %s" % STATS_PY)
    spec = importlib.util.spec_from_file_location("_w6_eval_stats", STATS_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.mcnemar_exact


# ---------------------------------------------------------------- 冻结题库
def freeze(conn, limit):
    ent_paths = defaultdict(lambda: defaultdict(list))
    for cid, e, path, idx in conn.execute(
            "SELECT ce.chunk_id, ce.entity, c.path, c.idx "
            "FROM chunk_entities ce JOIN chunks c ON c.id = ce.chunk_id"):
        ent_paths[e][path].append({"chunk_id": cid, "chunk_idx": idx})

    # 桥实体池放宽到中英文技术实体（原版只收中文 → 只够 12 条题）；
    # 仍要求 ≥2 篇不同文档，保证是多跳跨文档题而非单文档题。
    bridges = [e for e, d in ent_paths.items()
               if 2 <= len(d) <= 20 and 2 <= len(e) <= 20
               and not str(e).startswith("P::")]
    # 固定排序，保证可复现；不按「图谱容易答对」挑题
    bridges.sort(key=lambda e: (-len(ent_paths[e]), e))

    items = []
    for e in bridges:
        paths = sorted(ent_paths[e])[:2]
        if len(paths) < 2:
            continue
        gt = []
        for p in paths:
            chunks = sorted(ent_paths[e][p], key=lambda x: x["chunk_id"])
            others = [r[0] for r in conn.execute(
                "SELECT DISTINCT ce.entity FROM chunk_entities ce "
                "JOIN chunks c ON c.id=ce.chunk_id WHERE c.path=? AND ce.entity<>? "
                "AND LENGTH(ce.entity)>=2 LIMIT 3", (p, e))]
            gt.append({"path": p,
                       "chunk_ids": [c["chunk_id"] for c in chunks],
                       "chunk_idx": [c["chunk_idx"] for c in chunks],
                       "co_entities": others})
        a = gt[0]["co_entities"][0] if gt[0]["co_entities"] else e
        b = gt[1]["co_entities"][0] if gt[1]["co_entities"] else e
        if a == b:
            b = gt[1]["co_entities"][1] if len(gt[1]["co_entities"]) > 1 else e
        items.append({
            "qid": "W6MH-%03d" % (len(items) + 1),
            "question": TEMPLATES[len(items) % len(TEMPLATES)] % (a, b),
            "bridge_entity": e,  # 只作溯源，不出现在题面
            "gt": gt,
            "gt_paths": [g["path"] for g in gt],
            "origin": "semi-auto: cross-doc co-occurrence in sanitized corpus",
        })
        if len(items) >= limit:
            break

    data = {"version": "2026.11", "items": items}
    os.makedirs(os.path.dirname(ESET), exist_ok=True)
    with open(ESET, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    sha1 = hashlib.sha1(open(ESET, "rb").read()).hexdigest()
    manifest = {
        "set": os.path.basename(ESET), "sha1": sha1, "n_items": len(items),
        "topk": RULES["topk"], "rules": RULES,
        "generation": "semi-automatic, gt traceable to path + chunk_id + chunk_idx",
        "human_judged": 0, "calibration": "UNCALIBRATED",
        "note": "半自动标注，未做人标复核；结论只作能力演示，不作为接主链路的依据。"
                "题目在 --freeze 时冻结，--run 会校验 sha1，不许事后挑题或改判据。",
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in manifest.items() if k != "note"},
                     ensure_ascii=False, indent=2))
    print("[freeze] 题库 → %s" % ESET)
    return 0


# ---------------------------------------------------------------- 三路对照
def hits(conn, G, runner, question, topk):
    if runner == "baseline":
        res = S.vector_fallback(conn, question, topk, set())
    else:
        res, _dbg = S.RUNNERS[runner](conn, G, question, topk)
    return [r[1] for r in res]


def rate(items, paths, metric):
    out = []
    for it, got in zip(items, paths):
        gt = set(it["gt_paths"])
        ok = (len(gt & set(got)) > 0) if metric == "any" else gt.issubset(set(got))
        out.append(1 if ok else 0)
    return out


def verdict(base, treat, p):
    delta = (sum(treat) - sum(base)) / max(1, len(base)) * 100.0
    passed = delta >= RULES["min_delta_pp"] and p < RULES["max_p"]
    return {"delta_pp": round(delta, 2), "p": round(float(p), 6),
            "n_discordant": None,
            "conclusion": "值得接" if passed else "能力齐备、增益不显著、不接主链路"}


def run(conn, G):
    if not os.path.isfile(ESET):
        raise SystemExit("[ERR] 先跑 --freeze 冻结题库")
    sha1 = hashlib.sha1(open(ESET, "rb").read()).hexdigest()
    mf = json.load(open(MANIFEST, encoding="utf-8"))
    if sha1 != mf["sha1"]:
        raise SystemExit("[BLOCK] 题库 sha1 与冻结时不一致，拒绝评测（题目不许事后改）")
    items = json.load(open(ESET, encoding="utf-8"))["items"]
    mcnemar = load_mcnemar()
    topk = RULES["topk"]

    systems = {}
    for name in ("baseline", "1hop", "ppr"):
        systems[name] = [hits(conn, G, name, it["question"], topk) for it in items]

    out = {"set": mf["set"], "sha1": sha1, "n_items": len(items),
           "topk": topk, "rules": RULES, "calibration": mf["calibration"],
           "systems": {}, "comparisons": []}
    for name, paths in systems.items():
        out["systems"][name] = {
            "any@5": round(sum(rate(items, paths, "any")) / len(items), 4),
            "all@5": round(sum(rate(items, paths, "all")) / len(items), 4),
        }
    for metric in ("any", "all"):
        base = rate(items, systems["baseline"], metric)
        for treat_name in ("1hop", "ppr"):
            treat = rate(items, systems[treat_name], metric)
            p, nd, na, nb = mcnemar(base, treat)
            v = verdict(base, treat, p)
            v.update({"metric": metric + "@5", "pair": "baseline → +" + treat_name,
                      "n_discordant": nd, "a_only": na, "b_only": nb})
            out["comparisons"].append(v)
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    # 🔴 如实回写饱和度：W6 卡 §六 要求「新集必须去饱和」，饱和就写饱和，不粉饰
    mf["baseline_any_at5"] = out["systems"]["baseline"]["any@5"]
    mf["saturation"] = ("SATURATED — 词法基线过高，去饱和未完成，⏳ 需人标复核"
                        if mf["baseline_any_at5"] >= 0.80 else "OK")
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("[eval] 结果 → %s" % RESULT)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="W6 多跳评测：先冻结再跑")
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--db", default=DB)
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db)
    try:
        if a.freeze:
            return freeze(conn, a.limit)
        with open(GRAPH, "rb") as f:
            G = pickle.load(f)
        return run(conn, G)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
