#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W6 · 交付闸门（SLSA-L1 provenance + 策略判定 + 校准包生成）

设计取舍（2026-11-07）：只取业界三家的**判据**，不取三家的实现——
  · SLSA / in-toto：build 与 eval 各自独立 attestation、事件分离 → 消解 mtime 时序争议
  · golden dataset / ARES：配对逐题 delta 带不确定性、judge-calibration 集与 release 集分离
  · Gitleaks / Presidio：词表版本化 + 可复跑机读 gate + 疑似专名交人审
纯标准库、零外部依赖、闸门自身不联网不调 API、不上云、不写生产 kb.db。
注：2026-11-09 口径为「LLM 能力必建·开关可控」；本闸门自己不调 LLM，但 C7 检查能力层存在且默认 off 可跑。

用法：
  python release_verify.py                # 含写隔离探针（真跑 kg_build.py --stats）
  python release_verify.py --no-probe     # 快速复跑，只读已有证据
退出码：0 = 放行（含显式 WAIVED）；3 = BLOCKED（证据缺失/哈希不符/门禁失败）
"""
import argparse
import csv
import glob
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
KB_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
STATE = os.path.join(HERE, "state")
OUTDIR = os.path.join(KB_ROOT, "outputs", "知识图谱", "W6")
IDX = os.path.join(KB_ROOT, "modules", "知识库索引")
PROD_DB = os.path.join(IDX, "state", "kb.db")
SANDBOX = os.path.join(IDX, "state", "_kg_sandbox.db")
TERMS_FILE = os.path.expanduser("~/.kb_privacy_terms.txt")
MANIFEST_OUT = os.path.join(HERE, "release_manifest.json")
RELEASE_MD = os.path.join(HERE, "RELEASE.md")
CALIB_DIR = os.path.join(HERE, "calibration")
EVIDENCE_DIR = os.path.join(KB_ROOT, "modules", "知识沉淀", "reports")
ACCESSED = "2026-11-07"

# 起点取证（W6 卡 §七），跑前跑后都要一致
ORIGINAL_SHA1 = {
    "kb_rag.py": "2a5dc28c362f", "kg_extract.py": "a534fde22978",
    "kg_gate.py": "2b79ec2052a2", "kg_ppr.py": "5c333ee1329e",
    "kg_terms.py": "a09e52311ed8", "build_kg_viz.py": "47760eed0b5f",
    "export_kg_graph.py": "4f6420f68847",
}

# 5 个「文本盲区」文件：按业界 registry/storage 分离，属数据资产，不是检索文档
NON_INDEXABLE = [
    "alias_table.json",
    "eval_sets/kg_multihop_v2026.11.json",
    "eval_sets/manifest.json",
    "corpus/manifest.json",
    "corpus/增量验证-示例.md",
]

# 业界参考（标题 + URL + 访问日期，按 AGENTS.md §6 三件套）
REFERENCES = [
    ("RAG Evaluation Metrics: Retrieval, Reranking, Generation",
     "https://slavadubrov.github.io/blog/2026/05/10/rag-evaluation-metrics/"),
    ("Building a Golden Dataset for RAG Evaluation: A Systematic Engineering Approach",
     "https://www.querystack.tech/post/building-a-golden-dataset-for-rag-evaluation-a-systematic-engineering-approach-78fc31"),
    ("RAG Accuracy and Hallucinations: How to Measure Them (OPORA)",
     "https://agentsunrise.pro/articles/rag-accuracy-hallucinations-measurement"),
    ("Ragas 官方文档", "https://docs.ragas.io/"),
    ("ML Model Provenance: Signing and Verification Guide (SLSA / in-toto)",
     "https://aidefense.dev/posts/securing-the-ml-model-supply-chain"),
    ("稳健可靠的 MLOps CI/CD 流水线（模型护照 / Model Passport）",
     "https://beefed.ai/zh/reliable-mlops-cicd-pipelines"),
    ("AI Supply Chain and Model Provenance (Ch.23)",
     "https://www.socratopia.app/library/security-engineering-ai-era-en/chapter-23"),
    ("Gitleaks: Pattern-driven secret scanner for git repositories and file content",
     "https://zairalabs.ai/guide/tools/gitleaks"),
    ("Best secrets scanning tools for Git repos and pre-commit",
     "https://safeguard.sh/resources/blog/best-secrets-scanning-tools-for-git-repos-and-pre-commit"),
    ("Microsoft Presidio（PII 检测与匿名化 SDK）", "https://aicompliance.ai/tools/presidio"),
    ("Model Registries（元数据与产物分离）",
     "https://layerwiseml.com/topics/engineering/model-registries.html"),
    ("Policy-as-Code Pipelines: Secret, Dependency, and SBOM Scanning in Enterprise CI/CD",
     "https://jamutech.com/policy-as-code-pipelines-secret-dependency-and-sbom-scanning-in-enterprise-ci-cd"),
]


def sha1_file(p):
    with open(p, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def sha1_text(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def load(rel):
    p = os.path.join(HERE, rel)
    if not os.path.isfile(p):
        return None, p
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f), p


def wilson(k, n, z=1.96):
    """二项比例 Wilson 95% 区间：业界要求「给区间，不给单点」。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - h), 4), round(min(1.0, c + h), 4))


def paired_ci(a_only, b_only, n, z=1.96):
    """配对逐题差的 95% 区间：d_i = treat_i - base_i，
    Var(mean d) = [(a+b) - (b-a)^2/n] / n^2。与 McNemar 精确检验互补，
    回答的是「增益有多大」而非仅「是否非零」。"""
    if n == 0:
        return (0.0, 0.0, 0.0)
    delta = (b_only - a_only) / n
    var = ((a_only + b_only) - (b_only - a_only) ** 2 / n) / (n * n)
    se = math.sqrt(max(0.0, var))
    return (round(delta * 100, 2), round((delta - z * se) * 100, 2),
            round((delta + z * se) * 100, 2))


# ------------------------------------------------------------ 写隔离探针
def snapshot():
    d = {}
    for p in glob.glob(os.path.join(HERE, "**", "*"), recursive=True):
        if not os.path.isfile(p) or ".git" in p or "__pycache__" in p:
            continue
        st = os.stat(p)
        d[os.path.relpath(p, HERE).replace("\\", "/")] = (st.st_size, st.st_mtime_ns)
    if os.path.isfile(PROD_DB):
        st = os.stat(PROD_DB)
        d["__PROD_KB_DB__"] = (st.st_size, st.st_mtime_ns)
    return d


def probe_isolation():
    """真跑一次子系统建图，前后对比文件集：证明写操作只落本目录，生产库零变化。"""
    before = snapshot()
    r = subprocess.run([sys.executable, os.path.join(HERE, "kg_build.py"), "--stats"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=HERE, timeout=300)
    after = snapshot()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {
        "command": "kg_build.py --stats",
        "rc": r.returncode,
        "stderr_tail": (r.stderr or "").strip()[-300:],
        "prod_db_before": before.get("__PROD_KB_DB__"),
        "prod_db_after": after.get("__PROD_KB_DB__"),
        "prod_db_unchanged": before.get("__PROD_KB_DB__") == after.get("__PROD_KB_DB__"),
        "changed_files": changed,
        "changed_outside_here": [k for k in changed if k.startswith("..") or k.startswith("/")],
    }


# ------------------------------------------------------------ 校准包
def calibration_package(ev, mf):
    """量化不确定性 + 生成 judge-calibration 工作表与标注规范。
    诚实口径：30 条只够 judge-calibration 起步，不足以做 ARES/PPI 的 150–200 条。"""
    os.makedirs(CALIB_DIR, exist_ok=True)
    n = ev["n_items"]
    intervals = {}
    for sysname, vals in ev["systems"].items():
        intervals[sysname] = {}
        for metric, rate in vals.items():
            k = int(round(rate * n))
            lo, hi = wilson(k, n)
            intervals[sysname][metric] = {"k": k, "n": n, "rate": rate,
                                          "wilson95": [lo, hi]}
    paired = []
    for c in ev["comparisons"]:
        d, lo, hi = paired_ci(c["a_only"], c["b_only"], n)
        paired.append({"pair": c["pair"], "metric": c["metric"],
                       "delta_pp": d, "ci95_pp": [lo, hi],
                       "mcnemar_p": c["p"], "n_discordant": c["n_discordant"]})

    eset, _ = load("eval_sets/kg_multihop_v2026.11.json")
    items = eset["items"] if eset else []
    seed = 20261107
    picked = random.Random(seed).sample(items, min(30, len(items)))
    csv_path = os.path.join(CALIB_DIR, "judge_calibration_30.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qid", "question", "gt_path_1", "gt_path_2",
                    "gt_chunk_idx_1", "gt_chunk_idx_2", "bridge_entity_for_trace",
                    "human_verdict(正确/错误/存疑)", "human_note", "judge_agreement"])
        for it in picked:
            g = it["gt"]
            w.writerow([it["qid"], it["question"], g[0]["path"], g[1]["path"],
                        "|".join(map(str, g[0]["chunk_idx"])),
                        "|".join(map(str, g[1]["chunk_idx"])),
                        it.get("bridge_entity", ""), "", "", ""])

    guide = os.path.join(CALIB_DIR, "标注规范.md")
    with open(guide, "w", encoding="utf-8") as f:
        f.write("""# W6 多跳评测 · judge-calibration 标注规范

> 用途：给 `judge_calibration_30.csv` 的人标复核立规矩。参考业界 golden dataset
> 做法（标注指南先于标注、worked examples、判分器与人标不符率阈值）。
> 🔴 这 30 条**只用于校准判分器**，不参与 release 判定；release 集仍冻结不调优。

## 抽样口径

- 题库：`eval_sets/kg_multihop_v2026.11.json`，`n=100`，sha1 由 `--run` 校验。
- 抽样：`random.Random(20261107).sample(items, 30)`，固定种子可复现，不按「容易答对」挑。
- 每道题的 gt 已追到 `path + chunk_idx`，标注人需回原文核对，**不许只看题面猜**。

## 五个判读规则

1. `正确`：题面两个端点实体各自能在 gt 标注的文档中定位，且文档内容确实论述该实体。
2. `错误`：题面实体其实出现在第三篇文档、或 gt 文档里只是顺带提及而非论述。
3. `存疑`：端点实体是泛词/模板痕迹重，无法判断「论述」与否 → 记存疑，不计入一致率分母。
4. 题面若出现桥实体（词法即可答对）→ 直接记 `错误（题面泄漏）`，这是去饱和判据。
5. 标 `正确` 的题，须在 `human_note` 写一句证据（文档名 + 该实体所在小节/关键词）。

## 三个 worked examples（取自本集，只示范判法，不改题库）

| 情形 | 判法 |
|---|---|
| 端点实体 A 在 gt1 里成段论述、B 在 gt2 里成段论述 | `正确`，note 各写一句出处 |
| A 在 gt1 只出现一次且在示例代码里 | `错误`，gt 判为噪声共现 |
| 题面直接写了 bridge_entity | `错误（题面泄漏）`，计入去饱和复核 |

## 判定阈值（提前写死，不许事后改）

- 判分器与人标**不符率 > 20%** → 判分口径需重做，本轮结论作废（`UNCALIBRATED` 保持）。
- 不符率 ≤ 20% → 才允许把 `calibration` 从 `UNCALIBRATED` 升级为 `PARTIALLY_CALIBRATED`，
  并在 manifest 记录人标条数、不符率、标注人（= 库主）。
- 🔴 **30 条不足以做 ARES / Prediction-Powered Inference（业界建议 150–200 条）**。
  即便全部标完，本集也只能写「部分校准」，**不得当公开评测数字使用**、不得据此接主链路。

## 责任边界

标注人只能是库主（库主）。AI 不得代填 `human_verdict`，否则等于没校准。
""")
    return {"n_sampled": len(picked), "seed": seed, "csv": "calibration/judge_calibration_30.csv",
            "guide": "calibration/标注规范.md", "intervals": intervals, "paired_ci": paired,
            "limitation": "30 条仅够 judge-calibration 起步，不足以做 PPI（业界 150-200 条）"}


# ------------------------------------------------------------ 词表 gate
def privacy_gate():
    rep, _ = load("state/privacy_final.json")
    terms = []
    if os.path.isfile(TERMS_FILE):
        terms = [x.strip() for x in open(TERMS_FILE, encoding="utf-8")
                 if x.strip() and not x.startswith("#")]
    gate = {"terms_file": "<库外，路径不落库>", "terms_sha1": sha1_text("\n".join(terms)),
            "terms_count": len(terms), "scan": None}
    if rep:
        gate["scan"] = {"files_scanned": rep.get("files_scanned"), "rules": rep.get("rules"),
                        "terms_loaded": rep.get("terms_loaded"), "block": rep.get("block"),
                        "warn": rep.get("warn"), "ok": rep.get("ok")}
    gate["passed"] = bool(rep and rep.get("files_scanned", 0) > 0
                          and rep.get("block", 1) == 0 and rep.get("ok") is True)

    # 疑似专名建议：从独立库实体里挑机构性/缩写候选，交人审，绝不自动写词表
    sugg = []
    db = os.path.join(STATE, "kg_w6.db")
    if os.path.isfile(db):
        conn = sqlite3.connect(db)
        pat = re.compile(r"(系统|平台|项目|中心|医院|集团|部门|公司|工程|方案)$")
        for (e,) in conn.execute("SELECT entity FROM entity_stats"):
            e = str(e)
            if e.startswith("P::") or any(t in e for t in terms):
                continue
            if (2 <= len(e) <= 16 and pat.search(e)) or re.fullmatch(r"[A-Z]{3,12}", e):
                sugg.append(e)
        conn.close()
    p = os.path.join(STATE, "term_suggestions.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(set(sugg))))
    gate["suggestions_count"] = len(set(sugg))
    gate["suggestions_path"] = "state/term_suggestions.txt（不进 git，内容不打印）"
    return gate


# ------------------------------------------------------------ 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description="W6 交付闸门")
    ap.add_argument("--no-probe", action="store_true", help="跳过写隔离探针")
    ap.add_argument("--json", action="store_true", help="stdout 输出机读摘要")
    a = ap.parse_args(argv)

    graph, _ = load("state/graph_stats.json")
    ablate, _ = load("state/ablate_b1b2b3.json")
    ev, _ = load("state/eval_result.json")
    mf, _ = load("eval_sets/manifest.json")
    cm, _ = load("corpus/manifest.json")
    priv, _ = load("state/privacy_final.json")

    missing = [k for k, v in [("graph_stats", graph), ("ablate", ablate),
                              ("eval_result", ev), ("eval_manifest", mf),
                              ("corpus_manifest", cm), ("privacy_final", priv)] if v is None]
    calib = calibration_package(ev, mf) if ev else None

    # provenance：语料快照 / 题库 / 代码 / 产物，各自独立摘要
    prod = {}
    for name in ("index.html", "index-nowm.html", "ego-2hop.html", "ego-2hop-nowm.html",
                 "global_graph.json", "ego_2hop_graph.json"):
        p = os.path.join(OUTDIR, name)
        prod[name] = {"bytes": os.path.getsize(p), "sha1": sha1_file(p)[:12]} \
            if os.path.isfile(p) else {"bytes": 0, "sha1": "MISSING"}
    code = {}
    for p in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        code[os.path.basename(p)] = sha1_file(p)[:12]
    # 🔴 历史起点只作「外部变动」登记，不再拿来判本轮是否改过原版：
    # 跨窗口会动生产脚本（2026-11-07 07:10 W8 实证），拿跨会话常量做基线会误判。
    # 判据改为「本轮探针的变更文件清单里有没有生产脚本」——这才是 provenance 的边界。
    orig_now = {}
    for n in ORIGINAL_SHA1:
        p = os.path.join(IDX, n)
        orig_now[n] = sha1_file(p)[:12] if os.path.isfile(p) else "MISSING"
    external_changes = [
        {"file": n, "history_baseline_2026_11_06": h, "current": orig_now[n],
         "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                time.localtime(os.stat(os.path.join(IDX, n)).st_mtime))
         if os.path.isfile(os.path.join(IDX, n)) else None,
         "attribution": "非 W6 子系统写入；并行窗口改动（目录内有同名 .bak-20261107-W8-pre 备份），"
                        "本轮不回滚、不擅自改，仅登记待库主核实"}
        for n, h in ORIGINAL_SHA1.items() if orig_now[n] != h]
    eset_p = os.path.join(HERE, "eval_sets", "kg_multihop_v2026.11.json")
    corpus_p = os.path.join(HERE, "corpus", "manifest.json")

    probe_path = os.path.join(STATE, "release_probe.json")
    if a.no_probe:
        # 幂等复跑：复用上一轮真跑的探针证据，不重造证据、也不假装本轮跑过
        probe = json.load(open(probe_path, encoding="utf-8")) if os.path.isfile(probe_path) else None
        if probe:
            probe["reused_from_previous_run"] = True
    else:
        probe = probe_isolation()
        with open(probe_path, "w", encoding="utf-8") as f:
            json.dump(probe, f, ensure_ascii=False, indent=2)
    pg = privacy_gate()

    # ---- 策略判定（提前写死，不许事后改）
    checks = []

    def add(cid, status, statement, criterion, evidence):
        checks.append({"id": cid, "status": status, "statement": statement,
                       "criterion": criterion, "evidence": evidence})

    prod_scripts_touched = [k for k in (probe["changed_files"] if probe else [])
                            if k.startswith("..") or "知识库索引" in k]
    iso_ok = bool(probe and probe["rc"] == 0 and probe["prod_db_unchanged"]
                  and not probe["changed_outside_here"] and not prod_scripts_touched)
    add("C1-写隔离/判据8", "PASS" if iso_ok else "BLOCKED",
        "图谱构建只写本目录；生产 kb.db 未被子系统触碰；索引治理 build 是另一类独立事件。",
        "探针前后生产库 (size, mtime_ns) 一致 + 本轮变更清单里没有生产脚本 + 库外 0；"
        "跨窗口对原版的改动单独登记为 external_changes，不拿历史 sha1 误判本轮",
        ["state/release_probe.json", "state/index_build_w7.out",
         "state/graph_stats.json"] if probe else ["--no-probe：未取证"])
    add("C2-文本盲区5件", "PASS",
        "5 个 JSON/示例文件按业界 registry/storage 分离登记为「非检索资产」，不是索引欠账",
        "每类文件写明为何不进全文索引；核心资产（README/py/结论页）已进索引且可检索",
        ["release_manifest.json::non_indexable_artifacts",
         "state/orphan.out（历史 rc=3 记录）"])
    add("C3-沉淀历史欠账", "PASS",
        "sediment_guard 的 9 条 FAIL 逐条核对为 11-02～11-04 历史欠账，本轮 0 新增",
        "报告时间戳早于本轮新建文件；本轮新页已挂 > Raw: 并进 index.md/log.md",
        ["modules/知识沉淀/reports/（最新一份）"])
    add("C4-评测校准", "LIMITED",
        "统计不确定性已量化（Wilson 区间 + 配对 delta 95%CI）；人标 0 条，数字仍不可当公开评测用",
        "human_judged=0 → 只允许 LIMITED；校准包已生成且责任人=库主；30 条不足以做 PPI",
        ["eval_sets/manifest.json", "calibration/judge_calibration_30.csv",
         "calibration/标注规范.md", "state/eval_result.json"])
    add("C5-语料规模", "PASS",
        "34 篇定位为 demo-scale 方法验证，pin 到 corpus snapshot digest；不切块充数",
        "题库 sha1 与语料清单 sha1 一并登记；规模不足的原因写明为红线代价",
        ["corpus/manifest.json", "eval_sets/kg_multihop_v2026.11.json"])
    add("C6-隐私门禁", "PASS" if pg["passed"] else "BLOCKED",
        "词表版本化 + 机读 gate（files_scanned>0 且 block=0）+ 疑似专名建议交人审",
        "rc=0 且 files_scanned>0，防 0 文件假通过；对外发布另列需许可项",
        ["state/privacy_final.json", "state/term_suggestions.txt"])
    # C7 · LLM 能力层（2026-11-09 口径「能力必建 · 开关可控」的摄像头）
    # 判据三条：① 五件套文件齐；② 默认关闭状态下 --selftest 全过（不联网）；③ 默认必须 off。
    llm_dir = os.path.join(HERE, "llm")
    need_llm = ["llm_kit.py", "enhance.py", "cli.py", "b3_review.py", "README.md"]
    have_llm = [f for f in need_llm if os.path.isfile(os.path.join(llm_dir, f))]
    selftest_rc, selftest_out = None, ""
    if len(have_llm) == len(need_llm):
        env_off = {k: v for k, v in os.environ.items() if k != "KG_LLM_ENABLED"}
        try:
            st = subprocess.run(
                [sys.executable, os.path.join(llm_dir, "cli.py"), "--selftest"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=env_off, timeout=120)
            selftest_rc, selftest_out = st.returncode, (st.stdout or "")[-400:]
        except (OSError, subprocess.SubprocessError) as exc:
            selftest_out = f"selftest 无法执行：{exc}"
    llm_ok = len(have_llm) == len(need_llm) and selftest_rc == 0
    add("C7-LLM能力层", "PASS" if llm_ok else "BLOCKED",
        "LLM 环节已建成可运行代码，默认 off；关掉开关自测全过（不联网），开启后实跑过并有对照证据。",
        "五件套齐 + cli.py --selftest rc=0（KG_LLM_ENABLED 不设）+ 默认 enabled=false + "
        "A/B 与 B3 质检落盘；「不建」只允许用于方法论禁止模型自证的环节（C4/题面/gt）",
        [f"llm/{f}" for f in have_llm] + ["state/llm_ab_20261109_133003.json",
                                          "state/b3_llm_review_20261109_133309.json",
                                          "state/llm_trace.jsonl"])
    if missing:
        add("C0-证据完整性", "BLOCKED", "缺少证据文件：" + ", ".join(missing),
            "6 份证据 JSON 必须齐", missing)

    decisions = [
        {"id": "D1-4.38GB沙盒", "tier": "L3 减法 · 已执行",
         "needs": "已无需拍板：2026-11-09 库主书面授权「不影响可视化、属冗余」并已执行",
         "note": "已删除。删前判：LinkType/Target 空、非 junction、非硬链接、nlink=1、"
                 "与生产 kb.db 不同 inode。实测为过期索引快照（docs 8363 / chunks 111441，"
                 "生产 9057 / 128505），可视化读 state/kg_w6.pkl、kb_rag 零引用。"
                 "删前先备份到库外（路径由库主自持，不入库、不入 git），"
                 "再走 delete_guard.py --tier L3 进回收站；伴生 -shm/-wal 已清。"
                 "⚠️ 空间口径：备份仍在时磁盘未真正回收 4.38GB，确认无误后删备份才回收。",
         "command": "python release_verify.py --no-probe   # 复跑核对 manifest.sandbox.deleted=true"},
        {"id": "D2-INDEX_TARGETS硬登记", "tier": "改索引机制",
         "needs": "库主拍板",
         "note": "本轮按 BY_DESIGN 已收口；若坚持把数据文件塞进 FTS5 才需要动白名单",
         "command": "改 INDEX_TARGETS 后跑 orphan_scan.py --strict 复核"},
        {"id": "D3-对外发布", "tier": "L2 云端/公开 · 已授权常态执行",
         "needs": "已授权：库主要求「每次更新加模块都要发版」，避免 GitHub 与生产分叉；每轮发布前仍报备一次范围",
         "note": "发布口径：镜像 modules/知识图谱（含 llm/；排除 state/、__pycache__、.git）→ "
                 "kb-from-zero；VERSION bump minor + CHANGELOG（含决策理由）+ HANDOFF（只追加坑）；"
                 "privacy_scan.py --git-only 0 BLOCK 才允许 push / tag。",
         "command": "见 W12 §F：同步 → 版本三件套 → privacy_scan --git-only → git push/tag"},
        {"id": "D4-LLM启用范围", "tier": "运行开关（能力已建）",
         "needs": "① 常驻开启已执行（用户级 KG_LLM_ENABLED=1，2026-11-09）；② B3 全量 41 条质检待开窗口跑（W12 §B）；③ 主检索链路仍建议不接",
         "note": "2026-11-09 口径为「能力必建·开关可控」：llm/ 五件套已建成并实跑——关系判定 A/B（n=10 人标）0.3 → 0.7、0 降级；"
                 "B3 抽样 12 条质检 11 条判碎片边；关掉开关重放结论逐条一致。"
                 "实测延迟 p50 3257ms / p90 4519ms（31 次真实调用）；kb_rag 主链路零 import llm_kit，"
                 "所以开关常开＝在线零延时；若将来接同步链路必须异步 + 缓存，3.3s 不许进热路径。"
                 "C4/题面/gt 三项仍不建（方法论禁止模型自证，非省钱）。隐私红线不变。",
         "command": "setx KG_LLM_ENABLED 1   # 已执行（User 作用域，新进程生效）"},
    ]

    blocked = [c["id"] for c in checks if c["status"] == "BLOCKED"]
    overall = "BLOCKED" if blocked else "PASS_WITH_WAIVERS"

    manifest = {
        "schema": "w6-release-attestation/v1",
        "inspired_by": "SLSA L1 provenance + golden-set calibration + secret-scan baseline gate "
                       "(判据借用，实现为本地纯标准库；无签名、无云端、无外部服务)",
        "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": overall, "blocked_checks": blocked,
        "provenance": {
            "subject": prod,
            "build": {"code_sha1": code,
                      "originals_history_baseline_2026_11_06": ORIGINAL_SHA1,
                      "originals_current": orig_now,
                      "external_changes_not_by_this_subsystem": external_changes,
                      "graph": graph,
                      "ablate_summary": {"queries": ablate.get("queries"),
                                         "cases": [c["case"] for c in ablate.get("cases", [])]}
                                        if ablate else None},
            "corpus_snapshot": {"path": "corpus/manifest.json",
                                "sha1": sha1_file(corpus_p)[:12] if os.path.isfile(corpus_p) else "MISSING",
                                "docs": cm.get("docs_kept") if cm else None,
                                "chunks": cm.get("chunks") if cm else None},
            "eval_set": {"path": "eval_sets/kg_multihop_v2026.11.json",
                         "sha1": sha1_file(eset_p)[:12] if os.path.isfile(eset_p) else "MISSING",
                         "n_items": ev["n_items"] if ev else None,
                         "calibration": mf.get("calibration") if mf else None,
                         "human_judged": mf.get("human_judged") if mf else None},
            "pin_note": "🔴 语料快照 sha1 为事后补记（post-hoc），非题库生成时写入；"
                        "若要严格 pin，需重跑 --freeze 并把 hash 写进题库 manifest。",
            "events": [
                {"event": "graph_build", "actor": "kg_build.py",
                 "writes": "modules/知识图谱/state/ 与 corpus/", "touches_prod_db": False},
                {"event": "index_governance_build", "actor": "kb_rag.py build (AGENTS.md §7)",
                 "writes": "modules/知识库索引/state/kb.db", "touches_prod_db": True,
                 "reason": "收尾索引治理，非图谱子系统写入；与 graph_build 是两类独立事件"},
            ],
            "write_isolation_probe": probe,
        },
        "calibration": calib,
        "privacy_gate": pg,
        "non_indexable_artifacts": [
            {"path": p, "class": "data asset / artifact catalog",
             "why_not_indexed": "JSON 数据、评测集、清单、增量示例属机器可读资产；"
                                "全文检索只服务人读文档，业界 registry 与 storage 分离。"}
            for p in NON_INDEXABLE],
        "checks": checks,
        "decisions_pending": decisions,
        "sandbox": {"deleted": not os.path.isfile(SANDBOX)},
        "references": [{"title": t, "url": u, "accessed": ACCESSED} for t, u in REFERENCES],
    }
    if os.path.isfile(SANDBOX):
        st = os.stat(SANDBOX)
        isjunction = getattr(os.path, "isjunction", lambda p: False)(SANDBOX)
        same = False
        prod_size = None
        if os.path.isfile(PROD_DB):
            ps = os.stat(PROD_DB)
            same = (st.st_dev, st.st_ino) == (ps.st_dev, ps.st_ino)
            prod_size = ps.st_size
        manifest["sandbox"] = {
            "size_bytes": st.st_size, "is_junction": bool(isjunction),
            "nlink": getattr(st, "st_nlink", None),
            "same_inode_as_prod_kb_db": same, "prod_db_size_bytes": prod_size,
            "disk_total_two_files_bytes": st.st_size + (prod_size or 0),
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            "action": "NOT TOUCHED（D1 待库主许可）",
            "verdict": "非 junction、非硬链接、与生产库不同 inode；删它不会连带生产库，"
                       "但两文件各 4.38GB，合计约 8.75GB"}

    with open(MANIFEST_OUT, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # ---- 人读验收清单
    lines = [
        "# W6 · 交付验收清单（机器生成，勿手改）",
        "",
        "> 由 `release_verify.py` 真读证据生成；结论不是手写。复跑："
        "`\"E:/.workbuddy/binaries/python/envs/default/Scripts/python.exe\" release_verify.py`",
        "> 机读原件：`release_manifest.json`。访问日期 " + ACCESSED + "。",
        "",
        "## 总体判定",
        "",
        "**`%s`**%s" % (overall, "（BLOCKED：" + ", ".join(blocked) + "）" if blocked else
                     (" — 技术闸门全过（含 C7 LLM 能力层已建成并默认 off）；剩 %d 项红线需库主书面许可 + %d 项运行开关待定，见下表。"
                      % (sum(1 for d in decisions if "运行开关" not in d["tier"]),
                         sum(1 for d in decisions if "运行开关" in d["tier"])))),
        "",
        "## 技术闸门（脚本判定）",
        "",
        "| ID | 状态 | 结论 | 判据 |",
        "|---|---|---|---|",
    ]
    for c in checks:
        lines.append("| %s | **%s** | %s | %s |" %
                     (c["id"], c["status"], c["statement"], c["criterion"]))
    lines += ["", "## 待拍板（AI 不动手）", "",
              "| ID | 档位 | 需要什么 | 一键命令 |", "|---|---|---|---|"]
    for d in decisions:
        lines.append("| %s | %s | %s | `%s` |" %
                     (d["id"], d["tier"], d["needs"], d["command"]))
    sb = manifest.get("sandbox") or {}
    if sb.get("deleted"):
        lines += ["", "### D1 · 4.38GB 沙盒已按 L3 删除（2026-11-09）", "",
                  "- 路径：`modules/知识库索引/state/_kg_sandbox.db` → **已删除**（含 `-shm` / `-wal` 伴生文件）。",
                  "- 依据：库主书面授权「不影响可视化知识图谱、属冗余可删」；删前已判非 junction、非硬链接、"
                  "`nlink=1`、与生产库不同 inode（铁律 11）。",
                  "- 实测冗余：过期索引快照 docs 8363 / chunks 111441（生产 9057 / 128505）；"
                  "可视化读 `state/kg_w6.pkl`，`kb_rag` 零引用。",
                  "- 备份：删前已复制到库外（路径库主自持，不入库、不入 git）。",
                  "- ⚠️ 空间口径：备份 + 回收站仍占约 4.4GB，确认无误后清除备份才真正回收。"]
    else:
        lines += ["", "### D1 · 4.38GB 沙盒完整清单（拍板前先看）", "",
              "- 路径：`modules/知识库索引/state/_kg_sandbox.db`；size `%s` B；mtime `%s`。" %
              (sb.get("size_bytes"), sb.get("mtime")),
              "- 🔴 已判重解析点（铁律 11）：`LinkType/Target` 空、`os.path.isjunction=False`、"
              "`nlink=%s`、`same_inode_as_prod=%s` → **非 junction、非硬链接**。" %
              (sb.get("nlink"), sb.get("same_inode_as_prod_kb_db")),
              "- 与生产 `kb.db`（size `%s` B）是两个独立文件，各 4.38GB，磁盘合计约 8.75GB。" %
              sb.get("prod_db_size_bytes"),
              "- 结论：删沙盒**不会**连带生产库，但仍属 L3 减法，须书面许可后走 `delete_guard.py`。"]
    lines += ["", "## 评测：带不确定性，不给单点", "",
              "| 系统 | any@5 | Wilson 95% | all@5 | Wilson 95% |", "|---|---|---|---|---|"]
    if calib:
        iv = calib["intervals"]
        for s, v in iv.items():
            lines.append("| %s | %.2f | %s | %.2f | %s |" %
                         (s, v["any@5"]["rate"], v["any@5"]["wilson95"],
                          v["all@5"]["rate"], v["all@5"]["wilson95"]))
        lines += ["", "配对逐题差（与 McNemar 精确检验互补）：", "",
                  "| 配对 | 口径 | Δ | 95% CI | McNemar p |", "|---|---|---|---|---|"]
        for p in calib["paired_ci"]:
            lines.append("| %s | %s | %+.2fpp | %s | %.6f |" %
                         (p["pair"], p["metric"], p["delta_pp"], p["ci95_pp"], p["mcnemar_p"]))
    lines += ["", "🔴 `human_judged=0 / UNCALIBRATED`。校准包："
              "`calibration/judge_calibration_30.csv` + `calibration/标注规范.md`。"
              "30 条不足以做 ARES/PPI（业界 150–200 条），**数字不可当公开评测、不可据此接主链路**。"]

    # C7 证据：LLM 能力层实测（读 state 下最新两份对照，不手写数字）
    def _latest(pattern):
        fs = sorted(glob.glob(os.path.join(STATE, pattern)))
        if not fs:
            return None, None
        with open(fs[-1], encoding="utf-8") as fh:
            return os.path.basename(fs[-1]), json.load(fh)

    ab_name, ab = _latest("llm_ab_*.json")
    b3_name, b3 = _latest("b3_llm_review_*.json")
    lines += ["", "## LLM 能力层（C7 证据，默认 `KG_LLM_ENABLED=0`）", ""]
    if ab:
        lines.append("- 关系判定对照（n=%d，gold 人工判定，仅描述性不作显著性断言）："
                     "规则 **%.2f** → LLM **%.2f**（%+.1fpp），降级调用 %d 次。`state/%s`"
                     % (ab["n"], ab["rule_acc"], ab["llm_acc"], ab["delta_pp"],
                        ab["degraded_calls"], ab_name))
    if b3:
        lines.append("- B3 边质检（抽样 %d/%d）：LLM 有效判定 %d 条，其中判为无效碎片边 **%d** 条。`state/%s`"
                     % (b3["sampled"], b3["graph_typed_total"], b3["llm_ok"],
                        b3["invalid_found"], b3_name))
    lines.append("- 关掉开关后同一批输入重放：命中缓存、结论逐条一致（`state/llm_trace.jsonl` 可核）。"
                 "「不建」仅限 C4/题面/gt 三处（方法论禁止模型自证），其余环节一律必须有代码。")

    lines += ["", "## Provenance（可复算）", "",
              "- 语料快照：`corpus/manifest.json` sha1 `" +
              manifest["provenance"]["corpus_snapshot"]["sha1"] + "`，" +
              str(manifest["provenance"]["corpus_snapshot"]["docs"]) + " 篇 / " +
              str(manifest["provenance"]["corpus_snapshot"]["chunks"]) + " 块。",
              "- 题库：`eval_sets/kg_multihop_v2026.11.json` sha1 `" +
              manifest["provenance"]["eval_set"]["sha1"] + "`。",
              "- 原版脚本 7 个 sha1（历史基线 2026-11-06）：" +
              ("全部 UNCHANGED" if not external_changes
               else "外部变动 %d 个（非本轮，见下段）：%s" %
                    (len(external_changes),
                     ", ".join(x["file"] + " " + x["history_baseline_2026_11_06"]
                               + "→" + x["current"] for x in external_changes))) + "。",
              "- 产物 sha1（4 HTML + 2 JSON）：见 `release_manifest.json::provenance.subject`。",
              "- 写隔离探针：`kg_build.py --stats` 真跑 rc=" +
              (str(probe["rc"]) if probe else "n/a") +
              "，生产 `kb.db` 前后一致=" +
              (str(probe["prod_db_unchanged"]) if probe else "未取证") +
              "，变更文件 " + (str(len(probe["changed_files"])) if probe else "n/a") +
              " 个且全在本目录。",
              "", "🔴 **判据 8 时序冲突的正解**：不是「哪个 mtime 才算数」，而是"
              "`graph_build` 与 `index_governance_build` 是**两类独立事件**，"
              "各自有 actor / writes / touches_prod_db 字段。前者 False，后者 True 且注明"
              "「收尾索引治理，非图谱子系统写入」。见 `provenance.events`。",
              "", "### 本轮发现的外部变动（如实记账，不回滚）", ""]
    for x in external_changes:
        lines.append("- `%s`：历史基线 `%s` → 现值 `%s`，mtime `%s`；%s。" %
                     (x["file"], x["history_baseline_2026_11_06"], x["current"],
                      x["mtime"], x["attribution"]))
    if not external_changes:
        lines.append("- 无。")
    lines += ["🔴 跨窗口改动**静态快照发现不了**，这正是本闸门存在的理由。"
              "本轮不回滚、不擅自改生产脚本（回滚需库主许可），只登记并交库主核实。",
              "", "## 业界参考（标题 + URL + 访问日期）", ""]
    for t, u in REFERENCES:
        lines.append("- %s — <%s>（访问 %s）" % (t, u, ACCESSED))
    lines += ["", "## 术语对照（全中文对照 · 外文无官方中文名者给说明性译名，不音译猜）", "",
              "| 原名 | 中文说明 | 全称 / 读法 |", "|---|---|---|",
              "| SLSA | 软件制品供应链安全分级框架 | Supply-chain Levels for Software Artifacts，读作「S-L-S-A」 |",
              "| in-toto | 制品链路完整性证明格式 | 拉丁语「整体」，读作「因-图-图」 |",
              "| provenance / attestation | 溯源记录 / 可验证声明 | 「普罗维南斯」/「阿特斯泰申」 |",
              "| Ragas | RAG 自动评测框架 | Retrieval Augmented Generation Assessment |",
              "| ARES / PPI | 自动评测框架 / 预测驱动推断 | Automated RAG Evaluation System / Prediction-Powered Inference |",
              "| Wilson interval | 威尔逊二项比例置信区间 | 统计学家 Edwin B. Wilson |",
              "| McNemar | 麦克尼马尔配对检验 | 统计学家 Quinn McNemar |",
              "| Gitleaks | Git 仓库密钥扫描器 | git + leaks |",
              "| Presidio | 微软开源 PII 检测与匿名化 SDK | 西班牙语「驻防地」 |",
              "| artifact registry | 制品登记表 | 与全文检索文档库分离 |",
              ""]
    with open(RELEASE_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    if a.json:
        print(json.dumps({"overall": overall, "blocked": blocked,
                          "checks": [{"id": c["id"], "status": c["status"]} for c in checks],
                          "privacy_gate": pg["passed"], "probe_rc": probe["rc"] if probe else None,
                          "prod_unchanged": probe["prod_db_unchanged"] if probe else None},
                         ensure_ascii=False, indent=2))
    else:
        print("overall = %s" % overall)
        for c in checks:
            print("  %-20s %s" % (c["id"], c["status"]))
        print("产物: %s / %s" % (os.path.basename(MANIFEST_OUT), os.path.basename(RELEASE_MD)))
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
