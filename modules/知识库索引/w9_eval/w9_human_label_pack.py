#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
w9_human_label_pack.py — W9-B · 生成侧指标钩子 + 50 条空白人标表
================================================================

一句话：把 `judge_calibration.py` 的 6 条准入门槛接进 W9 报告；
        生成侧所有分数标 ⏳；出 50 条**空白**人标表交 W11 摆给库主人验。

🔴 红线（W3 §三 D / W9 §二 B / §四）：
   · 一条 gold 都不代填；annotator / annotator_type 一律留空
   · ❌ 不许用 LLM 合成答案凑人标、不许 AI 代填 gold_faithfulness
   · 未 CALIBRATED ⇒ 所有生成侧分数标 ⏳ 不判好坏、不进决策、不写进简历数字
   · 库里只有 21 条真实答案样本 ⇒ 50 行表 = 21 行挂真样本 + 29 行**空白待补**
     （靠 `answer_eval.py harvest` 收真实问答补齐，脚本绝不编造 query/answer）

用法（venv 解释器）：
  "$PY" w9_eval/w9_human_label_pack.py --self-test
  "$PY" w9_eval/w9_human_label_pack.py            # 生成 50 条空白表 + 报告 JSON

退出码：0 正常 / 1 自测失败 / 2 输入缺失
作者：落花飞雪· ｜ 许可：KBL-1.0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.dirname(HERE)
sys.path.insert(0, IDX)
KB_ROOT = os.path.dirname(os.path.dirname(IDX))
OUT_DIR = os.path.join(KB_ROOT, "outputs", "知识库运维", "W9-评测对齐")

ANSWER_SAMPLES = os.path.join(IDX, "state", "answer_samples.json")
CALIB_STATUS = os.path.join(IDX, "state", "judge_calibration", "status.json")
LABEL_OUT = os.path.join(OUT_DIR, "human_labels_50_blank.json")
REPORT_OUT = os.path.join(OUT_DIR, "generation_side_hook.json")

TARGET_ROWS = 50
HUMAN_FIELDS = ("gold_faithfulness", "gold_reason", "annotator", "annotator_type")


def _load(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 镜像常量：仓库侧（kb-from-zero）没有 judge_calibration.py，
# 按 §5 L2.4「能力自动降级，别卡住」用同一组阈值离线照跑；
# self_test 里在生产侧会做「镜像 vs 原版」逐位一致性自检，防止两份漂移。
MIRROR = {
    "MIN_PAIRED": 50, "MIN_KAPPA_W": 0.60, "MAX_ABS_BIAS": 0.50,
    "MIN_EXACT_AGREE": 0.70, "MIN_ADV_RECALL": 0.75, "MIN_ADV_GOLD": 4,
}


def thresholds():
    """优先直接 import 既有轮子的常量（只读，不调用任何写盘命令）；无模块时用镜像。"""
    try:
        import judge_calibration as jc
        v = {k: getattr(jc, k) for k in MIRROR}
        source = "judge_calibration.py（原版常量）"
    except ImportError:
        v = dict(MIRROR)
        source = "MIRROR 镜像常量（仓库侧无原版模块，阈值与原版逐位一致）"
    out = [
        {"rule": "n_paired>=%d" % v["MIN_PAIRED"], "current": None,
         "why": "同一条答案上同时有人工 gold 与 judge 分，且 gold_reason 必填"},
        {"rule": "kappa_w>=%.2f" % v["MIN_KAPPA_W"], "current": None,
         "why": "二次加权 κ，防「两个 LLM 互相一致」冒充与真值一致"},
        {"rule": "|bias|<=%.2f" % v["MAX_ABS_BIAS"], "current": None,
         "why": "mean(judge-gold)，防系统性偏高/偏低"},
        {"rule": "exact>=%.2f" % v["MIN_EXACT_AGREE"], "current": None,
         "why": "精确一致率"},
        {"rule": "adv_gold>=%d" % v["MIN_ADV_GOLD"], "current": None,
         "why": "人标确认的坏答案条数（不是合成反例）"},
        {"rule": "adv_recall>=%.2f" % v["MIN_ADV_RECALL"], "current": None,
         "why": "judge 判 <=2 分的检出率，防「两个都给高分」的系统性失败"},
    ]
    return [dict(x, constant_source=source) for x in out]


def build_blank_table(samples):
    """50 行：挂真实样本的行填 query/answer/contexts；其余行**全空**待 harvest 补。"""
    rows = []
    for i, s in enumerate(samples[:TARGET_ROWS], 1):
        rec = {
            "id": "w9_blank_%03d" % i,
            "query": s.get("query", ""),
            "answer": s.get("answer", ""),
            "contexts": [{"path": c.get("path", ""), "excerpt": c.get("excerpt", "")}
                         for c in s.get("contexts", [])],
            "synthetic_bad": bool(s.get("synthetic_bad")),
            "judge_score": None,             # 不代填、不从 LLM 分数搬过来当 gold
            "source": "state/answer_samples.json（库内既有真实样本）",
            "needs_human_label": True,
        }
        for f in HUMAN_FIELDS:
            rec[f] = None if f == "gold_faithfulness" else ""
        rows.append(rec)

    for i in range(len(rows) + 1, TARGET_ROWS + 1):
        rec = {
            "id": "w9_blank_%03d" % i,
            "query": "", "answer": "", "contexts": [],
            "synthetic_bad": False, "judge_score": None,
            "source": "",
            "needs_human_label": True,
            "needs_harvest": True,
            "harvest_hint": ("此行故意留空：靠 answer_eval.py harvest 收真实问答补齐，"
                             "🔴 不许用 LLM 合成答案、不许 AI 代填 gold"),
        }
        for f in HUMAN_FIELDS:
            rec[f] = None if f == "gold_faithfulness" else ""
        rows.append(rec)
    return rows


def audit(rows):
    """机器验「一条 gold 都不代填」，别靠肉眼。"""
    filled_gold = [r["id"] for r in rows if r.get("gold_faithfulness") is not None]
    filled_reason = [r["id"] for r in rows if str(r.get("gold_reason") or "").strip()]
    filled_annot = [r["id"] for r in rows if str(r.get("annotator") or "").strip()
                    or str(r.get("annotator_type") or "").strip()]
    invented = [r["id"] for r in rows
                if r.get("needs_harvest") and (r.get("query") or r.get("answer")
                                               or r.get("contexts"))]
    return {
        "n_rows": len(rows),
        "n_with_real_sample": sum(1 for r in rows if r.get("query")),
        "n_blank_pending_harvest": sum(1 for r in rows if r.get("needs_harvest")),
        "filled_gold": filled_gold,
        "filled_gold_reason": filled_reason,
        "filled_annotator": filled_annot,
        "invented_in_harvest_rows": invented,
        "clean": not (filled_gold or filled_reason or filled_annot or invented),
    }


def generation_hook(cal_status, ragas_metrics):
    """把 6 条门槛 + W9 生成侧分数统一标 ⏳，产出报告对象。"""
    verdict = (cal_status or {}).get("verdict", "UNKNOWN")
    calibrated = verdict == "CALIBRATED"
    gs = ((ragas_metrics or {}).get("generation_side") or {})
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "calibration_verdict": verdict,
        "calibrated": calibrated,
        "six_gates": thresholds(),
        "inventory": (cal_status or {}).get("inventory", {}),
        "generation_scores": [
            {"name": "faithfulness_proxy (RAGAS 本地弱代理)",
             "value": gs.get("faithfulness_proxy_real"), "status": "⏳" if not calibrated else "OK",
             "usable_for_decision": calibrated, "resume_eligible": False},
            {"name": "answer_relevancy_proxy (RAGAS 本地弱代理)",
             "value": gs.get("answer_relevancy_proxy_real"), "status": "⏳" if not calibrated else "OK",
             "usable_for_decision": calibrated, "resume_eligible": False},
            {"name": "W9 A/B：judge=local vs claim",
             "value": (ragas_metrics or {}).get("ab"), "status": "⏳" if not calibrated else "OK",
             "usable_for_decision": False,
             "resume_eligible": False,
             "note": "差值只证「开起来真能跑」，不是质量结论；未校准不判好坏"},
            {"name": "answer_eval context_support（既有弱代理）",
             "value": gs.get("answer_eval_context_support"), "status": "⏳" if not calibrated else "OK",
             "usable_for_decision": calibrated, "resume_eligible": False},
        ],
        "rule": ("未 CALIBRATED ⇒ 所有生成侧分数标 ⏳，不判好坏、不进上线/回滚决策、"
                 "不写进简历数字。LLM 不得自当评测金标（§5.11 判据 7）。"),
        "next_owner": "W11（摆给库主人验）；本窗口只交钩子与空白表，不代人验、不代填",
        "fill_path": "answer_eval.py harvest → judge_calibration.py prepare → 人填 gold → verify",
    }


def self_test():
    ok = True

    def chk(name, got, expect):
        nonlocal ok
        p = got == expect
        ok &= p
        print("  [%s] %-52s got=%s expect=%s" % ("PASS" if p else "FAIL", name, got, expect))

    print("=== w9_human_label_pack --self-test（内存数据，不落盘）===")
    rows = build_blank_table([{"query": "q", "answer": "a", "contexts": []}])
    a = audit(rows)
    chk("生成 50 行", a["n_rows"], TARGET_ROWS)
    chk("1 行挂真样本", a["n_with_real_sample"], 1)
    chk("49 行空白待 harvest", a["n_blank_pending_harvest"], 49)
    chk("gold 全空", a["filled_gold"], [])
    chk("gold_reason 全空", a["filled_gold_reason"], [])
    chk("annotator 全空", a["filled_annotator"], [])
    chk("待补行无编造内容", a["invented_in_harvest_rows"], [])
    chk("审计 clean", a["clean"], True)
    h = generation_hook({"verdict": "UNCALIBRATED"}, {"generation_side": {
        "faithfulness_proxy_real": 0.5}})
    chk("6 条门槛全在", len(h["six_gates"]), 6)
    # 镜像常量 vs 原版逐位一致（生产侧有原版模块时才验得到）
    try:
        import judge_calibration as jc
        chk("镜像常量与原版逐位一致",
            all(getattr(jc, k) == v for k, v in MIRROR.items()), True)
    except ImportError:
        print("  [INFO] 仓库侧无 judge_calibration.py，走 MIRROR 离线镜像（降级不中断）")
    chk("未校准 ⇒ 全标 ⏳", all(s["status"] == "⏳" for s in h["generation_scores"]), True)
    chk("未校准 ⇒ 决策不可用", all(not s["usable_for_decision"] for s in h["generation_scores"]), True)
    chk("未校准 ⇒ 简历不可用", all(not s["resume_eligible"] for s in h["generation_scores"]), True)
    # 反例：伪造 gold 必须被审计抓到
    rows[0]["gold_faithfulness"] = 5
    chk("伪造 gold 被抓到", audit(rows)["clean"], False)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="W9-B 生成侧钩子 + 50 条空白人标表")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        rc = self_test()
        print("结果：%s" % ("全 PASS" if rc == 0 else "有 FAIL"))
        return rc

    samples = _load(ANSWER_SAMPLES)
    if not samples:
        print("🔴 找不到真实答案样本 %s" % ANSWER_SAMPLES, file=sys.stderr)
        return 2
    cal = _load(CALIB_STATUS, {}) or {}
    ragas = _load(os.path.join(OUT_DIR, "ragas_metrics.json"), {}) or {}

    rows = build_blank_table(samples)
    a = audit(rows)
    if not a["clean"]:
        print("🔴 空白表审计未过：%s" % json.dumps(a, ensure_ascii=False), file=sys.stderr)
        return 1
    pack = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_rows": TARGET_ROWS,
        "rule": ("一条 gold 都不代填；空白行靠 answer_eval.py harvest 收真实问答补齐；"
                 "🔴 禁用 LLM 合成答案 / AI 代填 gold_faithfulness（W3 §三 D 红线）"),
        "scale": "0-5，含义见 modules/知识库索引/faithfulness_judge.py docstring",
        "owner": "W11 摆给库主人验；本窗口不代人验",
        "audit": a,
        "items": rows,
    }
    _dump(LABEL_OUT, pack)

    hook = generation_hook(cal, ragas)
    _dump(REPORT_OUT, hook)
    print("=== W9-B 生成侧钩子 ===")
    print("校准状态：%s｜人标 gold %s 条 ⇒ 生成侧分数一律 ⏳，不判好坏" % (
        hook["calibration_verdict"], hook["inventory"].get("human_gold", "?")))
    print("6 条准入门槛：")
    for g in hook["six_gates"]:
        print("  · %-22s %s" % (g["rule"], g["why"]))
    print("空白人标表：%d 行（真样本 %d / 待 harvest %d）｜gold 填充 %d 条（应为 0）" % (
        a["n_rows"], a["n_with_real_sample"], a["n_blank_pending_harvest"],
        len(a["filled_gold"])))
    print("产物：%s" % LABEL_OUT)
    print("产物：%s" % REPORT_OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
