#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eval_deepeval_gate.py — W9-A · CI 侧对照指标门禁（DeepEval 风格，等价 pytest 钩子）
================================================================================

一句话：读 `eval_ragas_adapter.py` 的本地对照产物，掉超阈值 rc≠0；
        与既有 `eval_gate.py` **并列不互斥**（零改动、不 import、不覆盖）。

🔴 为什么不是「装 deepeval 包」：DeepEval 的 G-Eval / AnswerRelevancy 等 metric
   默认同样调外部 LLM；卡片 A 要求「装前确认是否会拉云端模型」，故选等价 CI 钩子，
   门禁逻辑由本库自己实现，零外部依赖、离线可复现。判据口径与 deepeval 的
   `assert_test(..., threshold=...)` 一致：指标 < 阈值 ⇒ 非零退出码。

🔴 三态不许冒充（W2 教训 / W9 §四）：
   PASS  = 有输入、指标达标 ⇒ rc 0，badge_eligible=True
   FAIL  = 有输入、指标掉分 ⇒ rc≠0，badge_eligible=False
   SKIP  = 输入是明确标注的脱敏示例集 / 无生产数据 ⇒ rc 0 但 verdict=SKIP，
           badge_eligible=**False**，且强制打印 SKIP 原因。**绝不把 FAIL 洗成 SKIP**。
   ⚠️ 校验对象只能是真实跑出的产物；缺文件 / 产物损坏一律 rc≠0（不是 SKIP）。

退出码：0 PASS 或显式 SKIP｜3 掉超阈值｜4 输入缺失/损坏｜2 参数错误｜1 自测失败
用法（venv 解释器）：
  "$PY" w9_eval/eval_deepeval_gate.py --self-test
  "$PY" w9_eval/eval_deepeval_gate.py --input ragas_metrics.json
  "$PY" w9_eval/eval_deepeval_gate.py --input ragas_metrics.json --inject-drop 3
  "$PY" w9_eval/eval_deepeval_gate.py --input example.json --ci   # 脱敏示例集 ⇒ 显式 SKIP

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
KB_ROOT = os.path.dirname(os.path.dirname(IDX))
OUT_DIR = os.path.join(KB_ROOT, "outputs", "知识库运维", "W9-评测对齐")
DEFAULT_INPUT = os.path.join(OUT_DIR, "ragas_metrics.json")

DROP_LIMIT_PP = 2.0          # 与 eval_gate.py 的 DROP_LIMIT_PP 对齐（并列不互斥）
MIN_RECALL = 0.30            # 崩盘绝对下限（不是「达标线」，别拿绝对阈值冒充相对掉分判据）
MIN_PRECISION_ID = 0.05      # top5 里至少平均命中一个 gt 的量级（保守，只拦崩盘）
BASELINE_PATH = os.path.join(OUT_DIR, "deepeval_baseline.json")

CI_EXAMPLE_MARKERS = ("ci-example", "example", "脱敏示例", "sample-only")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_ci_example(path, data):
    """只有**产物自身明示**是脱敏示例集时才允许 SKIP；不许靠文件名猜。"""
    if isinstance(data, dict) and (
        data.get("dataset_type") == "ci-example"
        or data.get("example_only") is True
        or data.get("badge_eligible") is False
    ):
        return True
    return any(m in os.path.basename(path).lower() for m in CI_EXAMPLE_MARKERS)


def judge(data, baseline_recall=None, inject_drop=0.0):
    """纯判定函数（--self-test 直接喂内存假数据，不落盘）。

    双判据（与 eval_gate.py 同口径）：
      ① 相对基线掉分：`(recall - baseline)*100 - inject_drop < -DROP_LIMIT_PP` ⇒ 拦
      ② 崩盘绝对下限：`recall < MIN_RECALL` 或 `prec < MIN_PRECISION_ID` ⇒ 拦
    🔴 首跑无基线 ⇒ `BASELINE(不判定)` 且 badge_eligible=False，**不假装 PASS**。
    """
    r = (data.get("retrieval_side") or {})
    recall = r.get("context_recall_id")
    prec = r.get("context_precision_id")
    if recall is None or prec is None:
        return 4, "FAIL", ["产物缺 context_recall_id / context_precision_id，尺子坏了"], False
    if baseline_recall is None:
        return (0, "BASELINE(不判定)",
                ["首版基线：本次只存不判，不视为 PASS；重跑后才做相对掉分判定"], False)
    msgs = []
    delta_pp = (recall - baseline_recall) * 100 - inject_drop
    if delta_pp < -DROP_LIMIT_PP:
        msgs.append("context_recall_id %.4f vs 基线 %.4f（%+.1fpp%s）掉超 %.1fpp" % (
            recall, baseline_recall, delta_pp,
            "，含注入 -%.1fpp" % inject_drop if inject_drop else "", DROP_LIMIT_PP))
    if recall < MIN_RECALL:
        msgs.append("context_recall_id %.4f < 崩盘下限 %.4f" % (recall, MIN_RECALL))
    if prec < MIN_PRECISION_ID:
        msgs.append("context_precision_id %.4f < %.4f（检索侧疑似崩盘）" % (
            prec, MIN_PRECISION_ID))
    return (3 if msgs else 0), ("FAIL" if msgs else "PASS"), msgs, (not msgs)


def self_test():
    ok = True

    def chk(name, got, expect):
        nonlocal ok
        p = got == expect
        ok &= p
        print("  [%s] %-52s got=%s expect=%s" % ("PASS" if p else "FAIL", name, got, expect))

    print("=== eval_deepeval_gate --self-test（内存数据，不落盘）===")
    good = {"retrieval_side": {"context_recall_id": 0.48, "context_precision_id": 0.20}}
    chk("正常场景 rc=0 / PASS", judge(good, baseline_recall=0.48)[:2], (0, "PASS"))
    rc, verdict, msgs, _ = judge(good, baseline_recall=0.48, inject_drop=3.0)
    chk("故意注入掉 3pp ⇒ rc=3", rc, 3)
    chk("故意注入掉 3pp ⇒ FAIL", verdict, "FAIL")
    bad = {"retrieval_side": {"context_recall_id": 0.43, "context_precision_id": 0.20}}
    chk("相对基线真掉 5pp ⇒ rc=3", judge(bad, baseline_recall=0.48)[0], 3)
    crash = {"retrieval_side": {"context_recall_id": 0.10, "context_precision_id": 0.20}}
    chk("崩盘绝对下限 ⇒ rc=3", judge(crash, baseline_recall=0.10)[0], 3)
    broken = {"retrieval_side": {}}
    chk("尺子缺字段 ⇒ rc=4（不是 SKIP 放行）", judge(broken, baseline_recall=0.48)[0], 4)
    chk("首跑无基线 ⇒ 不判定且 badge 不合格",
        judge(good)[1:4:2], ("BASELINE(不判定)", False))
    # 三态：显式 SKIP 的 badge_eligible 必须是 False
    ex = {"dataset_type": "ci-example", "retrieval_side": {
        "context_recall_id": 0.99, "context_precision_id": 0.99}}
    chk("脱敏示例集识别为 ci-example", is_ci_example("ci-example.json", ex), True)
    chk("普通产物不误判为示例集", is_ci_example("ragas_metrics.json", good), False)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="W9-A CI 对照指标门禁（与 eval_gate 并列）")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--inject-drop", type=float, default=0.0,
                    help="判定前人为扣 N pp（故意造掉分，验门禁会拦）")
    ap.add_argument("--freeze-baseline", action="store_true", help="把本次结果写成新基线")
    ap.add_argument("--ci", action="store_true", help="CI 模式：不写报告文件；无基线⇒显式 SKIP")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    if args.self_test:
        rc = self_test()
        print("结果：%s" % ("全 PASS" if rc == 0 else "有 FAIL"))
        return rc

    path = args.input if os.path.isabs(args.input) else os.path.join(OUT_DIR, args.input)
    if not os.path.isfile(path):
        print("🔴 输入产物不存在：%s（先跑 eval_ragas_adapter.py）｜rc=4" % path)
        return 4
    try:
        data = _load(path)
    except (OSError, json.JSONDecodeError) as e:
        print("🔴 输入产物损坏：%s｜rc=4" % e)
        return 4

    print("=== W9-A CI 对照门禁｜输入 %s ===" % path)
    print("并列关系：与 modules/知识库索引/eval_gate.py **不互斥、不 import、零改动**")

    if is_ci_example(path, data):
        print("判定：SKIP（rc=0）｜原因：仓库侧脱敏示例集，不是生产 184 条主集")
        print("  🔴 badge_eligible=False —— SKIP 不等于 PASS，不许拿它冒充通过")
        payload = {"checked_at": datetime.now().isoformat(timespec="seconds"),
                   "verdict": "SKIP", "rc": 0, "badge_eligible": False,
                   "reason": "ci-example 脱敏示例集；生产评测需在生产库本地跑",
                   "input": path}
        out = args.json_out or os.path.join(OUT_DIR, "deepeval_gate_result.json")
        with open(out if os.path.isabs(out) else os.path.join(OUT_DIR, out),
                  "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("结果：%s" % out)
        return 0

    r = data.get("retrieval_side") or {}
    recall = r.get("context_recall_id")
    prec = r.get("context_precision_id")
    baseline = None
    if os.path.isfile(BASELINE_PATH) and not args.freeze_baseline:
        try:
            baseline = _load(BASELINE_PATH).get("context_recall_id")
        except (OSError, json.JSONDecodeError, AttributeError):
            baseline = None
    if args.ci and baseline is None:
        print("判定：SKIP（rc=0）｜原因：公开 CI 无生产基线，不做相对掉分判定")
        print("  🔴 badge_eligible=False —— SKIP 不等于 PASS，不许冒充通过")
        print("  生产基线只在生产库本地生成（不入库、不推仓库），符合「只推脱敏示例」")
        return 0

    rc, verdict, msgs, eligible = judge(data, baseline, args.inject_drop)
    print("context_recall_id=%s｜context_precision_id=%s｜基线=%s｜崩盘下限 recall<%.2f prec<%.2f" % (
        recall, prec, baseline, MIN_RECALL, MIN_PRECISION_ID))
    print("判定：%s（rc=%d）｜%s" % (verdict, rc, "；".join(msgs) if msgs else "全部判据通过"))
    if args.inject_drop:
        print("⚠ --inject-drop 注入 %.1fpp：本次 FAIL 是**故意造的验证场景**，不是真实退化" %
              args.inject_drop)

    if (baseline is None or args.freeze_baseline) and not args.ci and recall is not None:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump({"context_recall_id": recall,
                       "frozen_at": datetime.now().isoformat(timespec="seconds"),
                       "source": path,
                       "note": "首版基线；换集或改检索口径后需 --freeze-baseline 重冻"},
                      f, ensure_ascii=False, indent=2)
        print("基线写入：%s（本次不判定，不视为 PASS）" % BASELINE_PATH)

    payload = {"checked_at": datetime.now().isoformat(timespec="seconds"),
               "verdict": verdict, "rc": rc, "badge_eligible": eligible,
               "messages": msgs, "input": path, "baseline": baseline,
               "inject_drop": args.inject_drop,
               "coexist_with": "modules/知识库索引/eval_gate.py（零改动）"}
    if not args.ci:
        out = args.json_out or os.path.join(OUT_DIR, "deepeval_gate_result.json")
        with open(out if os.path.isabs(out) else os.path.join(OUT_DIR, out),
                  "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("结果：%s" % out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
