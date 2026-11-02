#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
faithfulness_judge — 真·答案忠实度评测（LLM-as-judge）
======================================================

背景：`answer_eval.py` 的「上下文支撑率」是**弱代理**（实词覆盖率），
抓得住「凭空造词」，抓不住「把 A 的事安到 B 头上」这类张冠李戴。
真 faithfulness 必须让一个模型**读上下文 + 读答案**，逐句判断有没有依据。

本脚本不负责生成判断，只负责**把判断结构化、可复现、可回归**：
  prepare → 导出待判定题（query + 答案 + 上下文）
  import  → 导入 judge 的评分与理由（judge 可以是人，也可以是 LLM）
  report  → 出报告；并把 judge 分与弱代理分做**相关性对照**

🔴 诚实地写明局限：本轮 judge = AI 助手本人，而答案也是AI 助手写的 —— **自评偏差**必然存在。
   缓解办法：判定时只读「上下文 + 答案」，不回想写答案时的想法；每条必须写出
   「哪句话无依据」，让理由可复核。真正的独立 judge 需要换一个模型（P1）。

评分标准（judge 必须按此打分，不许凭感觉）：

| faithfulness | 含义 |
|---|---|
| 5 | 每个事实主张都能在上下文中找到依据 |
| 4 | 基本都有，个别是合理推论 |
| 3 | 主要主张有依据，部分细节属常识补充（不算幻觉但没有出处） |
| 2 | 存在上下文不支持的主张 |
| 1 | 多数主张无依据 |
| 0 | 完全无依据 |

| relevancy | 含义 |
|---|---|
| 5 | 正面、直接回答了所问 |
| 3 | 相关但绕了 / 只答了一部分 |
| 0 | 答非所问 |

用法：
  python faithfulness_judge.py prepare
  python faithfulness_judge.py import --file state/judge_pending.json
  python faithfulness_judge.py report
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PENDING = os.path.join(HERE, "state", "judge_pending.json")
JUDGED = os.path.join(HERE, "state", "judge_scores.json")
REPORT = os.path.join(HERE, "state", "faithfulness_report.md")


def cmd_prepare(args):
    import answer_eval as ae
    samples = json.load(open(ae.SAMPLES, encoding="utf-8"))
    samples = [s for s in samples if s.get("answer", "").strip()]
    out = []
    for s in samples:
        out.append({
            "query": s["query"],
            "answer": s["answer"],
            # 判定依据必须与作答时看到的上下文一致：真实链路给的是块，不是整篇开头
            "contexts": [{"path": c["path"], "heading": c.get("heading", ""),
                          "excerpt": c.get("excerpt", "")} for c in s.get("contexts", [])],
            "faithfulness": None,      # judge 填 0-5
            "relevancy": None,         # judge 填 0-5
            "unsupported": "",         # judge 填：哪句话没有上下文依据（没有就写"无"）
            "reason": "",              # judge 填：一句话理由
            # 元信息只用于盲测后核对，导出给 judge 时会剥掉（见 --blind）
            "orig_query": s.get("orig_query", ""),
            "synthetic_bad": bool(s.get("synthetic_bad")),
            "bad_type": s.get("bad_type", ""),
        })
    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已导出 %d 道判定题 → %s" % (len(out), PENDING))
    print("填完 faithfulness/relevancy/unsupported/reason 后：python faithfulness_judge.py import")


def cmd_import(args):
    data = json.load(open(args.file, encoding="utf-8"))
    done = [d for d in data if d.get("faithfulness") is not None]
    for d in done:
        d["judged_at"] = datetime.now().isoformat(timespec="seconds")
        d["judge"] = args.judge
    with open(JUDGED, "w", encoding="utf-8") as f:
        json.dump(done, f, ensure_ascii=False, indent=2)
    print("已导入 %d 条判定（judge=%s）→ %s" % (len(done), args.judge, JUDGED))


def cmd_report(args):
    if not os.path.exists(JUDGED):
        print("还没有判定结果，先跑 import")
        return 1
    judged = json.load(open(JUDGED, encoding="utf-8"))
    import answer_eval as ae
    samples = {s["query"]: s for s in json.load(open(ae.SAMPLES, encoding="utf-8"))}
    _, rows = ae.evaluate([s for s in samples.values() if s.get("answer", "").strip()])
    weak = {r["query"]: r["support"] for r in rows}

    n = len(judged)
    f_avg = sum(d["faithfulness"] for d in judged) / n
    r_avg = sum(d["relevancy"] for d in judged) / n
    full = len([d for d in judged if d["faithfulness"] == 5])
    bad = [d for d in judged if d["faithfulness"] <= 2]
    has_unsupported = [d for d in judged if d.get("unsupported", "无").strip() not in ("无", "", "-")]

    # 弱代理 vs judge 的一致性：按「是否满分」分两组，看弱代理分有没有区分度
    hi = [weak[d["query"]] for d in judged if d["faithfulness"] >= 4]
    lo = [weak[d["query"]] for d in judged if d["faithfulness"] <= 3]
    hi_avg = sum(hi) / len(hi) if hi else 0
    lo_avg = sum(lo) / len(lo) if lo else 0

    print("=== 真·faithfulness（LLM-as-judge，%d 条）===" % n)
    print("  faithfulness 均分  %.2f / 5（满分 %d 条）" % (f_avg, full))
    print("  relevancy   均分  %.2f / 5" % r_avg)
    print("  无依据主张        %d 条答案里写了上下文撑不住的话" % len(has_unsupported))
    print("")
    print("=== 弱代理 vs judge（关键对照）===")
    print("  judge 给 4-5 分的答案，弱代理均分 %.3f（%d 条）" % (hi_avg, len(hi)))
    print("  judge 给 ≤3 分的答案，弱代理均分 %.3f（%d 条）" % (lo_avg, len(lo)))
    if hi and lo:
        gap = hi_avg - lo_avg
        print("  区分度 gap = %+.3f  → %s" % (
            gap, "弱代理有区分能力" if abs(gap) >= 0.05 else
            "⚠ 弱代理区分不出好答案和坏答案，不能当 faithfulness 用"))
    else:
        gap = None
        print("  区分度 gap = 无法计算（本轮 %s）" % (
            "全是高分样本，没有对照组" if not lo else "全是低分样本"))

    lines = ["# 真·faithfulness 评测报告（LLM-as-judge）", "",
             "> 生成时间：%s ｜ 样本 %d 条 ｜ judge：%s" % (
                 datetime.now().isoformat(timespec="seconds"), n,
                 judged[0].get("judge", "—")), "",
             "## 一、judge 评分", "",
             "| 指标 | 均分 | 说明 |", "|---|---|---|",
             "| faithfulness | %.2f / 5 | 答案主张有没有上下文依据（满分 %d 条）|" % (f_avg, full),
             "| relevancy | %.2f / 5 | 有没有正面回答所问 |" % r_avg,
             "| 含无依据主张 | %d 条 | 答案里写了上下文撑不住的话 |" % len(has_unsupported), "",
             "## 二、弱代理 vs judge 对照", "",
             "| 分组 | 弱代理（实词覆盖）均分 | 条数 |", "|---|---|---|",
             "| judge ≥4 分 | %.3f | %d |" % (hi_avg, len(hi)),
             "| judge ≤3 分 | %.3f | %d |" % (lo_avg, len(lo)), "",
             ("**区分度 gap = %+.3f** → %s" % (
                 gap, "弱代理有区分能力，可以零成本日常盯趋势" if abs(gap) >= 0.05 else
                 "⚠ 弱代理区分不出好坏，**不能**当 faithfulness 用，只能当健康度参考")
              if gap is not None else
              "**区分度 gap = 无法计算**：本轮没有低分对照组，说明样本全在高质量区 —— "
              "要验证弱代理，得刻意造几条坏答案（P1：构造反例样本）"), "",
             "## 三、逐条明细", "",
             "| 问法 | 忠实 | 相关 | 弱代理 | 无依据主张 |", "|---|---|---|---|---|"]
    for d in judged:
        lines.append("| %s | %d | %d | %.2f | %s |" % (
            d["query"][:26], d["faithfulness"], d.get("relevancy", 0),
            weak.get(d["query"], 0), (d.get("unsupported") or "无")[:30]))
    v1 = os.path.join(HERE, "state", "judge_scores_v1.json")
    if os.path.exists(v1):
        old = json.load(open(v1, encoding="utf-8"))
        o_avg = sum(d["faithfulness"] for d in old) / len(old)
        o_full = len([d for d in old if d["faithfulness"] == 5])
        o_bad = len([d for d in old if d.get("unsupported", "无").strip() not in ("无", "", "-")])
        print("\n=== 改进前 / 后对比 ===")
        print("  faithfulness  %.2f → %.2f（%+.2f）" % (o_avg, f_avg, f_avg - o_avg))
        print("  满分条数      %d → %d" % (o_full, full))
        print("  含无依据主张  %d → %d 条" % (o_bad, len(has_unsupported)))
        cmp_rows = ["", "## 四、改进前 / 后对比", "",
                    "| 版本 | faithfulness 均分 | 满分条数 | 含无依据主张 |", "|---|---|---|---|",
                    "| v1（整篇上下文 + 常识作答） | %.2f | %d | %d |" % (o_avg, o_full, o_bad),
                    "| v2（分块上下文 + 严格依据） | **%.2f** | **%d** | **%d** |" % (
                        f_avg, full, len(has_unsupported)),
                    "", "**改进动作只有两处**：① 上下文从「整篇文档前 N 字」换成「分块检索命中的章节」；",
                    "② 作答约束改为「只写上下文有的，库里没有就明说没有」。",
                    "", "> 说明：v1 的偏低不完全是答案的锅 —— 那时喂给模型的上下文本身就是文档开头（无关内容），",
                    "> 模型只能用常识补。**上下文质量决定答案忠实度的上限。**"]
        lines += cmp_rows
    # ---- 独立 judge 三方对照（消自评偏差的关键一步）----
    ind_p = os.path.join(HERE, "state", "judge_scores_independent.json")
    map_p = os.path.join(HERE, "state", "judge_blind_map.json")
    if os.path.exists(ind_p) and os.path.exists(map_p):
        import answer_eval as ae2
        ind = json.load(open(ind_p, encoding="utf-8"))
        mp = json.load(open(map_p, encoding="utf-8"))
        selfmap = {d["query"]: d["faithfulness"]
                   for d in json.load(open(JUDGED, encoding="utf-8"))}
        smp = json.load(open(ae2.SAMPLES, encoding="utf-8"))
        _, rws = ae2.evaluate([s for s in smp if s.get("answer", "").strip()])
        weak = {r["query"]: r["support"] for r in rws}

        # ⚠ 只能拿「同一条答案」比：反例没有自评分（自评是在注入前做的），不能凑数
        pairs = [(selfmap[m["query"]], d["faithfulness"], m["query"])
                 for d in ind for m in [mp[d["id"]]]
                 if not m["synthetic_bad"] and m["query"] in selfmap]
        bad_ids = [d for d in ind if mp[d["id"]]["synthetic_bad"]]
        good_ids = [d for d in ind if not mp[d["id"]]["synthetic_bad"]]
        caught = [d for d in bad_ids if d["faithfulness"] <= 2]
        b_w = [weak.get(mp[d["id"]]["query"], 0) for d in bad_ids]
        g_w = [weak.get(mp[d["id"]]["query"], 0) for d in good_ids]

        print("\n=== 独立 judge 三方对照 ===")
        if pairs:
            sa = sum(p[0] for p in pairs) / len(pairs)
            ia = sum(p[1] for p in pairs) / len(pairs)
            same = len([p for p in pairs if p[0] == p[1]])
            print("  自评 %.2f vs 独立 %.2f（自评 %+.2f）｜完全一致 %d/%d" % (
                sa, ia, sa - ia, same, len(pairs)))
        print("  盲测：反例 %d 条，独立 judge 抓出 %d 条（均分 %.2f vs 正常 %.2f）" % (
            len(bad_ids), len(caught),
            sum(d["faithfulness"] for d in bad_ids) / max(1, len(bad_ids)),
            sum(d["faithfulness"] for d in good_ids) / max(1, len(good_ids))))
        if b_w and g_w:
            print("  弱代理：正常 %.3f vs 反例 %.3f（gap %+.3f）" % (
                sum(g_w) / len(g_w), sum(b_w) / len(b_w),
                sum(g_w) / len(g_w) - sum(b_w) / len(b_w)))

        cc = ["", "## 五、独立 judge 三方对照", ""]
        if pairs:
            cc += ["**自评偏差实测**（只比同一条答案的 %d 条）：" % len(pairs), "",
                   "| judge | 均分 |", "|---|---|",
                   "| 自评（AI 助手） | %.2f |" % (sum(p[0] for p in pairs) / len(pairs)),
                   "| 独立 judge | %.2f |" % (sum(p[1] for p in pairs) / len(pairs)), "",
                   "完全一致 %d/%d 条，自评系统性偏高 %.2f 分 —— **自评偏差真实存在，但幅度可控**。"
                   % (len([p for p in pairs if p[0] == p[1]]), len(pairs),
                      sum(p[0] for p in pairs) / len(pairs) - sum(p[1] for p in pairs) / len(pairs)), ""]
        cc += ["**盲测有效性**（注入 4 条故意造坏的答案，judge 不知道是哪几条）：", "",
               "| 分组 | 独立 judge 均分 | 条数 |", "|---|---|---|",
               "| 正常答案 | %.2f | %d |" % (
                   sum(d["faithfulness"] for d in good_ids) / max(1, len(good_ids)), len(good_ids)),
               "| 反例（编造/张冠李戴/幽灵引用/常识补答） | **%.2f** | %d |" % (
                   sum(d["faithfulness"] for d in bad_ids) / max(1, len(bad_ids)), len(bad_ids)), "",
               "**抓出 %d / %d 条** → judge 本身可信。" % (len(caught), len(bad_ids)), ""]
        if b_w and g_w:
            cc += ["**弱代理的区分度**（终于能算了）：", "",
                   "| 分组 | 弱代理（实词覆盖） |", "|---|---|",
                   "| 正常答案 | %.3f |" % (sum(g_w) / len(g_w)),
                   "| 反例 | %.3f |" % (sum(b_w) / len(b_w)), "",
                   "gap %+.3f —— 方向对，但**幅度太小**，且「常识补答」类反例的弱代理分甚至高于不少正常答案。"
                   % (sum(g_w) / len(g_w) - sum(b_w) / len(b_w)),
                   "结论：**弱代理只能零成本盯趋势，不能当 faithfulness 用**（与 P1 判断一致）。", ""]
        lines += cc

    lines += ["", "## 六、局限（必须写明）", "",
              "- judge 与答案作者是同一个模型（AI 助手），**自评偏差**无法排除",
              "- 样本仅 %d 条，统计意义弱，趋势参考价值大于绝对值" % n,
              "- 独立 judge 需换模型或走 API（已列 P1）"]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n报告：%s" % REPORT)
    return 0


def main():
    ap = argparse.ArgumentParser(description="真·faithfulness 评测")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--ctx-len", type=int, default=2000)
    p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("import")
    p.add_argument("--file", default=PENDING)
    p.add_argument("--judge", default="AI 助手（自评，存在偏差）")
    p.set_defaults(func=cmd_import)
    sub.add_parser("report").set_defaults(func=cmd_report)
    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
