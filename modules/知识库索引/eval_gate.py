#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eval_gate — 检索质量门禁（W3：把评测从「参考」变成「会拦人」）
=============================================================

🔴 **kill criterion（写死，不许绕过）**
   新方案要在**同一集**上做配对检验（McNemar）p<0.05 才算赢，否则算**平**，
   不许宣布「有效」。配对检验实现在 `outputs/知识库运维/eval_stats.py`。
   本脚本只负责拦「明显掉分 / 缺桶 / 尺子坏了」，不负责证明「变好了」。

🔴 **readonly 铁律**：`kb_index.connect(DB_PATH, readonly=True)`。
   不调 `kb_rag.py` 的 CLI（CLI 的 cmd_search 会 log_query 写 query_log）。
   防回退测试：`_test_eval_readonly.py`。本库被污染过 93%（6363 行里 5924 行是评测题）。

口径（🔴 与历史 85.0% 不可比，别拿来对账）：
  · 检索路 = `kb_rag.search_unified(mode="fused", rerank=False)`，与 `eval_rag.py`
    的 unified 包装一致；生产 CLI 默认开 rerank，本门禁默认关（快、可复现、不需 CE 模型）。
    想验开精排加 `--rerank`，两次结果**不可直接比**。
  · 主指标 R@5 = 命中数 / **有 gt 的条数**（E 桶负样本 gt 为空，不进分母）。
    同时报全 184 分母的数，方便对照历史，但门禁只用前者。
  · 多答案走 all@5：`metrics.ranking_metrics` 的 hit_rate_at_k 对 gt 集合判定。
  · 「引用准确率」属答案层，不是检索题集指标，本脚本**不算也不混报**；
    要那个数请跑 `answer_eval.py report`。

判据与退出码：
  0 PASS｜3 R@5 掉 >2pp｜4 缺桶（相对基线）｜5 gt 空数 ≠ E 桶数（尺子坏了）
  6 规模/manifest 不符｜7 空检率 >5%｜2 参数/文件错误
  首版无基线时写基线并 rc 0，但明确打印「本次不判定」，**不假装 PASS**。

用法（解释器必须 venv 版）：
  PY="E:/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
  "$PY" modules/知识库索引/eval_gate.py --self-test
  "$PY" modules/知识库索引/eval_gate.py --freeze-baseline      # 首版：跑全量并存基线
  "$PY" modules/知识库索引/eval_gate.py                        # 改索引/切块/RRF 后必跑
  "$PY" modules/知识库索引/eval_gate.py --inject-drop 3        # 故意造掉分场景，验门禁会拦
  "$PY" modules/知识库索引/eval_gate.py --limit 20 --no-save   # 快速冒烟
"""

import argparse
import collections
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
EVAL_DIR = os.path.join(HERE, "eval_sets")
MANIFEST = os.path.join(EVAL_DIR, "manifest.json")
BASELINE = os.path.join(EVAL_DIR, "baseline.json")
LATEST = os.path.join(EVAL_DIR, "gate_latest.json")

DROP_LIMIT_PP = 2.0      # W3 卡：R@5 掉 >2pp 拦
MAX_EMPTY_PCT = 5.0      # 沿用 weekly_selfcheck 的 max-empty


def load_set():
    man = json.load(open(MANIFEST, encoding="utf-8"))
    rec = next(s for s in man["sets"] if s["version"] == man["latest"])
    items = json.load(open(os.path.join(EVAL_DIR, rec["file"]), encoding="utf-8"))
    return rec, items


def run_eval(items, topk=5, rerank=False, limit=0):
    """真跑融合检索。只读连接；复用 metrics 口径，不自己算 hit。"""
    import kb_index
    import kb_rag
    import metrics as mtr

    conn = kb_index.connect(kb_index.DB_PATH, readonly=True)
    # 🔴 硬自证：只看 query_log 行数变化不够——并行窗口（W1/W2…）也会写这张表。
    # 直接在本连接上试一次 INSERT，必须被 OperationalError 挡住，才证明 readonly 真生效。
    # `_ro_probe_` 是固定标记，万一 readonly 失效也能一眼认出并清掉。
    readonly_ok = False
    try:
        conn.execute(
            "INSERT INTO query_log(ts,query,mode,topk,n_results,latency_ms,top1_path) "
            "VALUES('_ro_probe_','_ro_probe_','x',1,1,1,'x')")
        conn.commit()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        readonly_ok = True
    n0 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    use = items[:limit] if limit else items
    # 预热：模型加载 / jieba 首分词不能算进第一条查询（eval_rag.py 同款处理）
    if use:
        try:
            kb_rag.search_unified(conn, use[0]["query"], topk=topk,
                                  mode="fused", rerank=rerank)
        except Exception as e:
            print("  ⚠ 预热失败（继续）：%s" % str(e)[:80])

    hit = 0
    mrr = 0.0
    n_with_gt = 0
    n_neg = 0
    empty_res = 0
    per_bucket = collections.defaultdict(lambda: {"n": 0, "hit": 0})
    for it in use:
        q, gts = it["query"], (it.get("gt") or [])
        try:
            res, _note = kb_rag.search_unified(conn, q, topk=topk,
                                               mode="fused", rerank=rerank)
        except Exception as e:
            print("  ⚠ 检索失败，按未命中计：%s｜%s" % (q[:28], str(e)[:60]))
            res = []
        ranked = [h["path"] for h in res]
        if not res:
            empty_res += 1
        if not gts:                     # E 桶负样本：单列观测，不进 R@5 分母
            n_neg += 1
            continue
        n_with_gt += 1
        mm = mtr.ranking_metrics(ranked, gts, ks=(1, topk))
        is_hit = bool(mm[topk]["hit"])
        hit += 1 if is_hit else 0
        mrr += mm[topk]["mrr"]
        b = per_bucket[it.get("bucket") or "(无)"]
        b["n"] += 1
        b["hit"] += 1 if is_hit else 0
    n1 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    conn.close()
    if n1 != n0:
        print("  ⚠ 评测期间 query_log %d → %d：本连接 readonly（INSERT 已被拒），"
              "变化来自并行窗口 / 定时任务，不是本脚本污染" % (n0, n1))
    if not readonly_ok:
        print("🔴 readonly 自证失败：INSERT 居然成功了，立刻排查 kb_index.connect")
    return {
        "n_total": len(use), "n_with_gt": n_with_gt, "n_neg": n_neg,
        "hit": hit,
        "recall_at5": hit / n_with_gt if n_with_gt else 0.0,
        "recall_at5_all_denom": hit / len(use) if use else 0.0,
        "mrr_at5": mrr / n_with_gt if n_with_gt else 0.0,
        "empty_results": empty_res,
        "empty_rate_pct": empty_res * 100 / len(use) if use else 0.0,
        "per_bucket": {k: dict(v) for k, v in sorted(per_bucket.items())},
        "rerank": rerank, "topk": topk,
        "readonly_enforced": readonly_ok,
        "query_log_before": n0, "query_log_after": n1,
    }


def judge(met, rec, items, baseline, inject_drop=0.0):
    """纯判定函数（--self-test 直接喂假数据，不跑检索）。"""
    msgs = []
    buckets = dict(sorted(collections.Counter(x.get("bucket") or "(无)" for x in items).items()))
    gt_empty = sum(1 for x in items if not x.get("gt"))
    # 尺子自身完整性（与基线无关，每轮都查）
    if len(items) != rec["n"]:
        msgs.append((6, "条数 %d ≠ manifest 记录 %d" % (len(items), rec["n"])))
    if gt_empty != buckets.get("E", 0):
        msgs.append((5, "gt 空 %d 条 ≠ E 桶 %d 条，尺子可能坏了" % (
            gt_empty, buckets.get("E", 0))))
    if met["empty_rate_pct"] > MAX_EMPTY_PCT:
        msgs.append((7, "空检率 %.1f%% 超过 %.1f%%" % (
            met["empty_rate_pct"], MAX_EMPTY_PCT)))
    if not met.get("readonly_enforced", True):
        msgs.append((8, "readonly 自证失败：评测连接可写，query_log 会被污染"))
    if baseline is None:
        return 0, ["首版基线：本次只存不判（不视为 PASS）"], buckets
    base = baseline["metrics"]
    delta_pp = (met["recall_at5"] - base["recall_at5"]) * 100 - inject_drop
    if delta_pp < -DROP_LIMIT_PP:
        msgs.append((3, "R@5 %.1f%% → %.1f%%（%+.1fpp%s）掉超 %.1fpp" % (
            base["recall_at5"] * 100, met["recall_at5"] * 100, delta_pp,
            "，含注入 -%.1fpp" % inject_drop if inject_drop else "", DROP_LIMIT_PP)))
    missing = sorted(set(baseline["buckets"]) - set(buckets))
    if missing:
        msgs.append((4, "缺桶 %s（相对基线 %s）" % (missing, baseline["version"])))
    return (min(m[0] for m in msgs) if msgs else 0,
            [m[1] for m in msgs], buckets)


def self_test():
    """逻辑级验证：测试只认 [PASS]/[FAIL]，不看「看起来还行」。"""
    ok = True
    items = [{"query": "q%d" % i, "gt": ["a"], "bucket": b}
             for i, b in enumerate(["B"] * 2 + ["C"] * 2 + ["D"] * 2 + ["F"] * 2)]
    items += [{"query": "neg", "gt": [], "bucket": "E"}]
    rec = {"version": "test", "n": len(items)}
    met = {"recall_at5": 0.50, "mrr_at5": 0.5, "empty_rate_pct": 0.0,
           "n_total": len(items), "n_with_gt": 8, "n_neg": 1, "hit": 4}
    base = {"version": "test", "metrics": {"recall_at5": 0.50},
            "buckets": {"B": 2, "C": 2, "D": 2, "E": 1, "F": 2}}
    rc, msgs, _ = judge(met, rec, items, base)
    print("[%s] 正常场景 rc 应为 0 → 实得 %d" % ("PASS" if rc == 0 else "FAIL", rc))
    ok &= rc == 0
    rc, msgs, _ = judge(met, rec, items, base, inject_drop=3.0)
    print("[%s] 故意掉分 3pp rc 应非 0 → 实得 %d｜%s" % (
        "PASS" if rc == 3 else "FAIL", rc, msgs[0] if msgs else ""))
    ok &= rc == 3
    bad = base.copy()
    bad = dict(bad, buckets=dict(base["buckets"], G=1))
    rc, msgs, _ = judge(met, rec, items, bad)
    print("[%s] 缺桶 rc 应为 4 → 实得 %d｜%s" % (
        "PASS" if rc == 4 else "FAIL", rc, msgs[0] if msgs else ""))
    ok &= rc == 4
    broken = [dict(x) for x in items[:-1]]
    rc, msgs, _ = judge(met, rec, broken, base)
    print("[%s] 尺子条数/bucket 坏掉 rc 应非 0 → 实得 %d" % (
        "PASS" if rc != 0 else "FAIL", rc))
    ok &= rc != 0
    rc, msgs, _ = judge(met, rec, items, None)
    print("[%s] 首版无基线 → rc 0 且明说「不判定」｜实得 %d｜%s" % (
        "PASS" if rc == 0 and any("不判" in m for m in msgs) else "FAIL", rc, msgs))
    ok &= rc == 0 and any("不判" in m for m in msgs)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="检索质量门禁（readonly）")
    ap.add_argument("--self-test", action="store_true", help="逻辑级验证判据 rc，不跑检索")
    ap.add_argument("--freeze-baseline", action="store_true", help="强制把本次结果写成新基线")
    ap.add_argument("--inject-drop", type=float, default=0.0,
                    help="判定前人为扣 N pp（故意造掉分场景，验证门禁会拦）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟用，不写基线）")
    ap.add_argument("--no-save", action="store_true", help="不写 gate_latest.json / 基线")
    ap.add_argument("--rerank", action="store_true", help="开 Cross-Encoder 精排（默认关）")
    ap.add_argument("--topk", type=int, default=5)
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    try:
        rec, items = load_set()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
        print("🔴 读评测集失败：%s" % e)
        return 2
    print("=== 检索质量门禁｜集 %s（%d 条）===" % (rec["version"], len(items)))
    met = run_eval(items, topk=args.topk, rerank=args.rerank, limit=args.limit)
    print("R@5(有gt) %.1f%%（%d/%d）｜R@5(全分母) %.1f%%｜MRR@5 %.4f｜空检 %d（%.1f%%）" % (
        met["recall_at5"] * 100, met["hit"], met["n_with_gt"],
        met["recall_at5_all_denom"] * 100, met["mrr_at5"],
        met["empty_results"], met["empty_rate_pct"]))
    print("分桶：%s" % "  ".join("%s %d/%d" % (k, v["hit"], v["n"])
                                for k, v in met["per_bucket"].items()))
    print("query_log %d → %d（本连接 readonly %s；若有变化＝并行窗口，非本脚本）｜rerank=%s" % (
        met["query_log_before"], met["query_log_after"],
        "生效 ✅" if met["readonly_enforced"] else "🔴 未生效",
        met["rerank"]))
    print("引用准确率：非检索题集指标，本门禁不算；请跑 answer_eval.py report")

    if args.limit:
        print("冒烟模式（前 %d 条）：只报指标，不判定、不写基线（前 N 条全是同桶，不能代表全集）"
              % args.limit)
        return 0

    baseline = None
    if os.path.isfile(BASELINE) and not args.freeze_baseline:
        try:
            baseline = json.load(open(BASELINE, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            baseline = None
    rc, msgs, buckets = judge(met, rec, items, baseline, args.inject_drop)
    verdict = ("BASELINE(不判定)" if baseline is None
               else ("PASS" if rc == 0 else "FAIL(rc=%d)" % rc))
    print("判定：%s｜%s" % (verdict, "；".join(msgs) if msgs else "全部判据通过"))

    if not args.no_save:
        payload = {"checked_at": datetime.now().isoformat(timespec="seconds"),
                   "version": rec["version"], "metrics": met, "buckets": buckets,
                   "rc": rc, "messages": msgs, "verdict": verdict}
        with open(LATEST, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("结果：%s" % LATEST)
        if args.freeze_baseline or baseline is None:
            with open(BASELINE, "w", encoding="utf-8") as f:
                json.dump({"version": rec["version"], "frozen_at": payload["checked_at"],
                           "metrics": met, "buckets": buckets,
                           "note": "首版基线；换集或改索引口径后需重新 --freeze-baseline"},
                          f, ensure_ascii=False, indent=2)
            print("基线：%s" % BASELINE)
    if args.inject_drop:
        print("⚠ --inject-drop 已注入 %.1fpp，本次 FAIL 是**故意造的验证场景**，不是真实退化" %
              args.inject_drop)
    return rc


if __name__ == "__main__":
    sys.exit(main())
