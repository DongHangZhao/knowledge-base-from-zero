#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
w9_ci.py — W9-D · 公开 CI 编排器（仓库侧，离线，零外部依赖）
============================================================

一句话：把 `run_tests.py --ci` + `eval_gate.py`（示例集→明确 SKIP）+
        W9 五件套自测 + `privacy_scan.py --git-only` 串成一条命令，
        每条**真跑并查 returncode**，汇总成可贴的 rc 与 JSON。

🔴 为什么需要编排器（而不是 workflow 里直接写 5 条 run）：
   · 「子命令失败是静默的」：GitHub Actions 的 `run: |` 多行默认不逐条拦，
     一条挂了后面照跑，看上去一片绿；必须用 subprocess 显式取 rc。
   · 卡片 D 要求「用仓库侧示例集，明确 SKIP 而不是假绿」：
     仓库侧**只有脱敏示例集、没有冻结主集 manifest**，
     `eval_gate.py` 直接跑必然 rc=2。编排器检测到这一点 ⇒ 记 **SKIP**，
     同时真跑 `eval_gate.py --self-test` 证明门禁逻辑没坏（rc=0）。
     SKIP 单列计数、badge_eligible 不被 SKIP 项冒充为 PASS。
   · 文件末尾 `sys.exit(main())`（技能卡 exit-code-gate）：rc 才真的存在。

三态：
   PASS  = 真跑 rc=0 且判据通过
   SKIP  = 设计上不具备条件（无生产主集 / 无生产基线）⇒ 明确打印原因
   FAIL  = 真跑 rc≠0 ⇒ 整条 CI rc≠0，**不许洗成 SKIP**

用法：
  python w9_eval/w9_ci.py --ci              # CI 模式（不写 JSON 到仓库）
  python w9_eval/w9_ci.py --self-test       # 验编排器自身三态逻辑
  python w9_eval/w9_ci.py --out report.json # 本地真跑并出报告

作者：落花飞雪· ｜ 许可：KBL-1.0
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(IDX))       # kb-from-zero 根

PY = sys.executable
RUN_TESTS = os.path.join(IDX, "run_tests.py")
EVAL_GATE = os.path.join(IDX, "eval_gate.py")
MANIFEST = os.path.join(IDX, "eval_sets", "manifest.json")
PRIVACY = os.path.join(REPO, "tools", "privacy_scan.py")

SELFTESTS = [
    ("eval_ragas_adapter.py --self-test", os.path.join(HERE, "eval_ragas_adapter.py")),
    ("eval_deepeval_gate.py --self-test", os.path.join(HERE, "eval_deepeval_gate.py")),
    ("w9_human_label_pack.py --self-test", os.path.join(HERE, "w9_human_label_pack.py")),
    ("w9_observability.py --self-test", os.path.join(HERE, "w9_observability.py")),
    ("w9_gt_traceability.py --self-test", os.path.join(HERE, "w9_gt_traceability.py")),
]


def run(name, cmd, cwd=None):
    """真跑子进程 + 显式取 returncode（不接管道，不吞 rc）。"""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        rc, out = 2, "执行异常：%s" % e
    tail = " | ".join([ln.strip() for ln in out.strip().splitlines()
                       if ln.strip() and "Loading weights" not in ln][-3:])
    print("  [%s] %-46s rc=%-3s %s" % (
        "PASS" if rc == 0 else "FAIL", name, rc, tail[:220]))
    return {"name": name, "cmd": " ".join(os.path.basename(x) for x in cmd),
            "rc": rc, "verdict": "PASS" if rc == 0 else "FAIL",
            "output_tail": tail[:600]}


def summarize(results, skips):
    fails = [r for r in results if r["verdict"] == "FAIL"]
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "repo": REPO,
        "mode": "offline-ci（零外部 API、零上传、零 push）",
        "n_pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "n_fail": len(fails),
        "n_skip": len(skips),
        "skips": skips,
        "results": results,
        "badge_eligible": len(fails) == 0,
        "rule": ("SKIP 单列、不算 PASS；FAIL 绝不洗成 SKIP。"
                 "badge 绿只代表「所有能真跑的项都跑过了」，不代表生产评测已通过"),
        "verdict": "FAIL" if fails else "PASS_WITH_SKIPS" if skips else "PASS",
    }


def self_test():
    """验编排器自身：真跑一个必然 rc=0 和一个必然 rc≠0 的最小命令。"""
    print("=== w9_ci --self-test（只验编排器，不跑全套）===")
    ok = run("python -c 0", [PY, "-c", "print('ok')"])
    bad = run("python -c 1", [PY, "-c", "import sys; sys.exit(1)"])
    s = summarize([ok, bad], [{"name": "dummy-skip", "reason": "自测占位"}])
    p = ok["verdict"] == "PASS" and bad["verdict"] == "FAIL" and s["verdict"] == "FAIL"
    print("  [%s] 正反例 rc 0/1 都被正确识别，FAIL 不被洗成 SKIP" % ("PASS" if p else "FAIL"))
    return 0 if p else 1


def main():
    ap = argparse.ArgumentParser(description="W9-D 公开 CI 编排器")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--ci", action="store_true", help="CI 模式：不写 JSON 报告到仓库")
    ap.add_argument("--out", default=None, help="本地真跑时写 JSON 报告")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    print("=== W9-D 公开 CI 编排器｜仓库 %s ===" % REPO)
    print("解释器：%s｜离线，无 torch / 无外部 API" % PY)
    results, skips = [], []

    # ① 既有离线回归套件（真跑，rc 必须 0）
    results.append(run("run_tests.py --ci", [PY, RUN_TESTS, "--ci"], cwd=IDX))

    # ② eval_gate：仓库侧无冻结主集 ⇒ 明确 SKIP；但门禁逻辑本身必须真跑自测
    if not os.path.isfile(MANIFEST):
        skips.append({
            "name": "eval_gate.py（检索质量门禁本体）",
            "reason": ("仓库侧只有脱敏示例集 multihop_30.example.json，"
                       "没有冻结主集 eval_sets/manifest.json 与生产索引库；"
                       "🔴 卡片 D 明令「别把生产 184 条含真实业务 query 推上去」。"
                       "直接跑必然 rc=2，故记 SKIP 而不是假绿"),
            "not_pass": True,
        })
        print("  [SKIP] eval_gate.py 本体｜无冻结主集 manifest（明确 SKIP，非 PASS）")
    else:
        results.append(run("eval_gate.py", [PY, EVAL_GATE], cwd=IDX))
    results.append(run("eval_gate.py --self-test", [PY, EVAL_GATE, "--self-test"], cwd=IDX))

    # ③ W9 五件套自测（真跑，纯内存不落地）
    for name, path in SELFTESTS:
        results.append(run(name, [PY, path, "--self-test"], cwd=HERE))

    # ④ CI 对照门禁：CI 侧没有生产 ragas_metrics 产物 ⇒ 显式 SKIP。
    # 🔴 不改 eval_deepeval_gate.py 本身的 rc=4 判据（输入缺失就该拦），
    #    也不把 rc=4 吞成 PASS —— 编排器在**跑之前**判定「不具备条件」并记 SKIP。
    ci_metric = os.path.join(os.path.dirname(HERE), "..", "..", "outputs",
                             "知识库运维", "W9-评测对齐", "ragas_metrics.json")
    if not os.path.isfile(ci_metric):
        skips.append({
            "name": "eval_deepeval_gate.py --ci（相对掉分判定）",
            "reason": ("公开 CI 不含生产 ragas_metrics.json 与生产基线（按卡片 D 只推脱敏示例）；"
                       "不具备判定条件 ⇒ SKIP。🔴 未把任何 FAIL 洗成 SKIP："
                       "脚本输入缺失时仍 rc=4 硬拦，本编排器只是不发起这一步"),
            "not_pass": True,
        })
        print("  [SKIP] eval_deepeval_gate.py --ci｜无生产产物与基线（明确 SKIP，非 PASS）")
    else:
        results.append(run("eval_deepeval_gate.py --ci",
                           [PY, os.path.join(HERE, "eval_deepeval_gate.py"), "--ci"],
                           cwd=HERE))
        skips.append({
            "name": "eval_deepeval_gate.py 相对掉分判定",
            "reason": "公开 CI 无生产基线，脚本返回 verdict=SKIP、badge_eligible=False",
            "not_pass": True,
        })

    # ⑤ 隐私终审（开源出口，--git-only）
    if os.path.isfile(PRIVACY):
        results.append(run("privacy_scan.py --git-only",
                           [PY, PRIVACY, "--root", REPO, "--git-only", "--no-assets"],
                           cwd=REPO))

    summary = summarize(results, skips)
    print("\n汇总：PASS %d / FAIL %d / SKIP %d｜verdict=%s｜badge_eligible=%s" % (
        summary["n_pass"], summary["n_fail"], summary["n_skip"],
        summary["verdict"], summary["badge_eligible"]))
    for s in skips:
        print("  SKIP → %s：%s" % (s["name"], s["reason"][:110]))
    if args.out:
        out = args.out if os.path.isabs(args.out) else os.path.join(REPO, args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)   # --out 目录可能不存在
        with open(out, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print("报告：%s" % out)
    return 0 if summary["badge_eligible"] else 1


if __name__ == "__main__":
    sys.exit(main())
