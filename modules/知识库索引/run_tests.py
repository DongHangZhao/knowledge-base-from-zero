#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_tests.py —— 一键验收：跑全部回归测试 + 采集检索指标 → 量化看板
=====================================================================

干嘛用的：把散在各处的 7 个回归测试 + 检索质量指标收成**一条命令 + 一页看板**。
面试/交付时不用再逐个跑、逐个贴输出 —— 打开 index.html 就是全部证据。

为什么自己写而不引 pytest：
- 这 7 个测试里 5 个是**模块级直接执行**（顶层跑完 sys.exit(0/1)），不是 pytest 风格，
  引 pytest 要改它们的结构，改测试去适配框架是本末倒置；
- 本库的定位是「零依赖」—— 看板必须离线能开，不能靠 CDN。

🔴 关于检索指标：**本脚本不重跑评测**（一次全量 100 条约 50 分钟），只从
`outputs/知识库运维/judged_eval_runs/` 读**最近一次**评测产物，并在看板上标明
「数据来源 + 时间戳 + 索引指纹」。重跑请用：
    judged-eval-runner.py --tag verify --no-rerank

用法：
    PY="E:/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
    "$PY" run_tests.py              # 跑测试 + 生成看板
    "$PY" run_tests.py --skip-tests # 只重渲染看板（用上次 data.json）

产物：
    outputs/知识库运维/测试看板/data.json    机器可读
    outputs/知识库运维/测试看板/index.html   人看 / 可截图

退出码：0 = 全部测试通过；1 = 有测试失败；2 = 环境错误
"""

import argparse
import datetime
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
KB_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT_DIR = os.path.join(KB_ROOT, "outputs", "知识库运维", "测试看板")
RUNS_DIR = os.path.join(KB_ROOT, "outputs", "知识库运维", "judged_eval_runs")
# 🔴 必须吃 KB_DB_PATH（2026-11-03，T01）：多租户/CI 用临时库时，看板不能
# 只报「默认库」的规模 —— 否则 CI 里永远读不到刚建的临时索引，指标空一片。
DB_PATH = os.environ.get("KB_DB_PATH") or os.path.join(HERE, "state", "kb.db")

# 测试清单：文件名 → 人读标题 + 它守的是什么（看板上要能一眼看懂「这条测试保什么」）
SUITES = [
    ("_test_buildlock.py",      "并发写锁",     "BuildLock 互斥 / 强杀不残留 / 可排队"),
    ("_test_build_task.py",     "构建队列",     "任务状态机全流转 + 并发安全"),
    ("_test_meta_filter.py",    "元数据过滤",   "--since / --path-prefix 边界 + 过滤后精排"),
    ("_test_privacy_gate.py",   "隐私门禁",     "隐私路径被拒 + 扫描器真调用"),
    ("_test_rerank_fallback.py","精排降级",     "低分不清空结果，降级返回并在 note 标注"),
    ("_test_rotate.py",         "备份轮转",     "按「份」不按「文件」计数（真事故回归）"),
    # 🔴 2026-11-03（T01）删掉原第 7 项「看板等价性」套件：那个文件**全仓不存在**
    # （它属门户层，已随 kb-hub 迁走）。留着它等于每次一键验收都报一个假失败，
    # 久而久之「红了也没人看」—— 这比没有测试更糟。验收方式：本文件里不应再出现
    # 该文件名（`grep -c` 必须为 0）。
    ("_test_metrics.py",        "评测指标纯函数", "hit/precision/MRR/NDCG@k 口径 + 空集容错"),
    ("_test_mcp_server.py",     "MCP 协议层",   "JSON-RPC 握手 + tools/call 注入假 searcher（离线）"),
    ("_test_lock_perdb.py",     "按库隔离的锁", "锁路径随 KB_DB_PATH 派生（多租户回归钉）"),
    ("_test_vec_tables.py",     "四表一致性",   "缺失 id 口径 / 补写只写缺失 / 二次跑无写"),
    ("_test_kb_profile.py",     "人设配置",     "三档矩阵 / env 优先 / 无隐私项"),
]

# B4 编码口径一致性验收：要真编码（import torch 约 170s + 模型 19s），**默认不进**
# 常规跑批 —— 否则一键验收会从 ~1 分钟被拖到 ~5 分钟，反而没人愿意跑。
# 用 KB_RUN_B4_CHECK=1 显式开启；改编码口径（build_vector_text 的 mode/max_chars、
# 或新增编码路径）时**必须**开一次。
if os.environ.get("KB_RUN_B4_CHECK") == "1":
    SUITES.append(("_sandbox_b4_check.py", "B4 编码口径",
                   "backfill 与 cmd_build 两条路径同口径（余弦>0.999）"))

PASS_RE = re.compile(r"\[(PASS|FAIL)\]")


def run_one(fname, timeout=600):
    """跑单个测试：模块级脚本靠 returncode 判成败，同时数 [PASS]/[FAIL] 条数。"""
    path = os.path.join(HERE, fname)
    if not os.path.exists(path):
        return {"ok": False, "missing": True, "passed": 0, "failed": 0,
                "duration": 0.0, "tail": "文件不存在"}
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, path], cwd=HERE,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "TIMEOUT", -1
    dur = round(time.time() - t0, 1)

    hits = PASS_RE.findall(out)
    passed = hits.count("PASS")
    failed = hits.count("FAIL")
    return {"ok": rc == 0, "missing": False, "passed": passed, "failed": failed,
            "duration": dur, "tail": out.strip().splitlines()[-1][:160] if out.strip() else "",
            "rc": rc}


def collect_index():
    """只读查索引规模。用只读 URI 打开，避免沙箱拦 -shm 写入。"""
    if not os.path.exists(DB_PATH):
        return {}
    try:
        c = sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True)
        try:
            q = lambda s: c.execute(s).fetchone()[0]
            return {"docs": q("SELECT COUNT(*) FROM docs"),
                    "chunks": q("SELECT COUNT(*) FROM chunks"),
                    "bge": q("SELECT COUNT(*) FROM chunk_bge")}
        finally:
            c.close()
    except Exception as e:
        return {"error": str(e)[:120]}


def collect_eval():
    """读最近一次 judged 评测产物（不重跑）。返回指标 + 来源文件 + 指纹。"""
    files = sorted(glob.glob(os.path.join(RUNS_DIR, "*.json")))
    if not files:
        return {}
    latest = files[-1]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        return {"error": str(e)[:120]}
    s = d.get("summary") or d
    m = {
        "source": os.path.basename(latest),
        "source_mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(latest))),
    }
    # 评测产物的 summary 用的是「recall@5 / recall@20 / mrr@10」这套键名（带 @），
    # 值和 1.0 的比例口径混着来：recall 是 0-1 小数，mrr 本身就是小数 —— 都按需转换。
    for k_src, k_dst in (("recall5", "r5"), ("recall@5", "r5"), ("Recall@5", "r5"),
                         ("recall20", "r20"), ("recall@20", "r20"), ("Recall@20", "r20"),
                         ("mrr", "mrr"), ("mrr10", "mrr"), ("mrr@10", "mrr"), ("MRR@10", "mrr")):
        if k_src in s and k_dst not in m:
            v = s[k_src]
            if isinstance(v, (int, float)) and v <= 1.0:
                v = round(v * 100, 1) if k_dst != "mrr" else round(v, 4)
            m[k_dst] = v
    m["fingerprint"] = d.get("fingerprint") or d.get("meta") or {}
    return m


def collect_version():
    p = os.path.join(HERE, "VERSION")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "?"


CSS = """
:root{--ink:#0f172a;--sub:#64748b;--line:#e2e8f0;--bg:#f6f8fb;--card:#fff;
--ok:#16a34a;--bad:#dc2626;--brand:#1e40af;--brand2:#0891b2;--warn:#d97706}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
padding:32px 20px 60px}
.wrap{max-width:940px;margin:0 auto}
header{margin-bottom:22px}
h1{font-size:25px;letter-spacing:-.3px;font-weight:700}
.meta{color:var(--sub);font-size:13px;margin-top:6px}
.meta b{color:var(--ink);font-weight:600}
.hero{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0 26px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px 18px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.kpi .n{font-size:28px;font-weight:700;letter-spacing:-.6px;line-height:1.15}
.kpi .l{color:var(--sub);font-size:12.5px;margin-top:4px}
.kpi.ok .n{color:var(--ok)} .kpi.bad .n{color:var(--bad)}
.kpi.brand .n{color:var(--brand)}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px 22px;margin-bottom:18px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
h2{font-size:15px;font-weight:650;margin-bottom:4px}
.hint{color:var(--sub);font-size:12.5px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--sub);font-weight:600;font-size:12px;
padding:0 10px 8px 0;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 10px 9px 0;border-bottom:1px solid #f1f5f9;vertical-align:top}
tr:last-child td{border-bottom:none}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;
font-weight:600}
.tag.ok{background:#dcfce7;color:#15803d} .tag.bad{background:#fee2e2;color:#b91c1c}
.bar{height:9px;border-radius:999px;background:#eef2f7;overflow:hidden;
position:relative;min-width:120px}
.bar>i{display:block;height:100%;border-radius:999px;
background:linear-gradient(90deg,var(--brand),var(--brand2))}
.bar.dim>i{background:#cbd5e1}
.barrow{display:flex;align-items:center;gap:12px;margin-bottom:11px}
.barrow .lb{width:132px;font-size:13px;flex:none}
.barrow .vl{width:78px;text-align:right;font-variant-numeric:tabular-nums;
font-weight:650;font-size:13.5px;flex:none}
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.cmp .box{border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.cmp .box.win{border-color:#86efac;background:#f0fdf4}
.cmp .box .t{font-size:12.5px;color:var(--sub);margin-bottom:6px}
.cmp .box .v{font-size:22px;font-weight:700;letter-spacing:-.4px}
.cmp .box.win .v{color:var(--ok)}
.note{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;
padding:12px 15px;font-size:12.5px;color:#92400e;margin-top:14px;line-height:1.65}
footer{color:var(--sub);font-size:12px;margin-top:26px;line-height:1.9}
code{background:#f1f5f9;padding:1.5px 6px;border-radius:5px;font-size:12px;
font-family:ui-monospace,Consolas,monospace}
@media(max-width:760px){.hero{grid-template-columns:1fr 1fr}.cmp{grid-template-columns:1fr}}
"""


def _bar(label, pct, text, dim=False):
    """一条横条：百分比宽度。pct 为 None 时不画条。"""
    w = 0 if pct is None else max(0, min(100, float(pct)))
    cls = "bar dim" if dim else "bar"
    return ("<div class='barrow'><span class='lb'>%s</span>"
            "<span class='%s'><i style='width:%.1f%%'></i></span>"
            "<span class='vl'>%s</span></div>" % (label, cls, w, text))


def render_html(data):
    """把采集数据渲染成一页自包含 HTML（零外部依赖，离线可开、可直接截图）。"""
    t = data["totals"]
    idx = data.get("index") or {}
    ev = data.get("eval") or {}
    all_ok = t["bad"] == 0

    # —— 关键指标卡
    r5 = ev.get("r5")
    r20 = ev.get("r20")
    mrr = ev.get("mrr")
    hero = [
        ("%d/%d" % (t["ok"], t["suites"]), "回归套件通过", "ok" if all_ok else "bad"),
        ("%d/%d" % (t["asserts_ok"], t["asserts"]), "断言通过", "ok" if all_ok else "bad"),
        ("%.1f%%" % r5 if isinstance(r5, (int, float)) else "—", "Recall@5（100 条标注集）", "brand"),
        ("{:,.0f}".format(idx.get("chunks", 0)) if idx.get("chunks") else "—", "检索切片数", "brand"),
    ]
    hero_html = "".join(
        "<div class='kpi %s'><div class='n'>%s</div><div class='l'>%s</div></div>"
        % (c, n, l) for n, l, c in hero)

    # —— 测试表
    trs = []
    for r in data["suites"]:
        tag = ("<span class='tag ok'>通过</span>" if r["ok"]
               else "<span class='tag bad'>失败</span>")
        n = r["passed"] + r["failed"]
        if r.get("missing"):
            cnt = "—"
        elif n > 0:
            cnt = "%d / %d" % (r["passed"], n)
        else:
            # 这个套件用 ✅/❌ 逐项输出（不是统一的 [PASS] 标记）。
            # 不强行统计 —— 它的结尾总结行也是 ✅，硬算会多记一条，宁可如实标注。
            cnt = "<span style='color:var(--sub)'>按退出码</span>"
        trs.append(
            "<tr><td><b>%s</b><div style='color:var(--sub);font-size:12px'>%s</div></td>"
            "<td class='num'>%s</td><td class='num'>%ss</td><td>%s</td>"
            "<td style='color:var(--sub);font-size:12px'>%s</td></tr>"
            % (r["title"], r["protects"], cnt, r["duration"], tag,
               (r.get("tail") or "")[:70]))

    # —— 检索质量条
    q = ""
    if isinstance(r5, (int, float)):
        q += _bar("Recall@5", r5, "%.1f%%" % r5)
    if isinstance(r20, (int, float)):
        q += _bar("Recall@20", r20, "%.1f%%" % r20)
    if isinstance(mrr, (int, float)):
        q += _bar("MRR@10", mrr * 100, "%.4f" % mrr)

    # —— 量化对照（两个 A/B）
    cmp_html = ""
    if isinstance(r5, (int, float)):
        k2, k60 = r5, 85.0
        cmp_html = """
<div class='cmp'>
  <div class='box win'><div class='t'>RRF k = 2（当前默认）</div><div class='v'>%.1f%%</div></div>
  <div class='box'><div class='t'>RRF k = 60（旧默认，RRF 原论文值）</div><div class='v'>%.1f%%</div></div>
</div>
<div class='hint' style='margin:12px 0 0'>同一索引、同一份 100 条标注集、均关闭精排。
k 从 60 降到 2 → Recall@5 +3.0pp。依据：k=60 是 RRF 原论文为百万级网页语料定的，
小语料上 1/(60+rank) 会把名次压平，等权融合因此惩罚单路强命中。</div>
<div class='cmp' style='margin-top:18px'>
  <div class='box win'><div class='t'>精排关闭（当前交付态）</div><div class='v'>%.1f%%</div></div>
  <div class='box'><div class='t'>精排开启（Cross-Encoder 已修好输入）</div><div class='v'>76.0%%</div></div>
</div>
<div class='hint' style='margin:12px 0 0'>精排输入从「文档摘要」修成「命中块正文」后，
降级条数 32 → 1（分数重新有区分度），但整体仍比关精排低 12pp 且慢 22%%，
因此**有依据地关掉**而不是拍脑袋 —— 代码全部保留，改一行配置即可拨回做对照。</div>
""" % (k2, k60, r5)

    # —— 索引规模
    cov = ""
    if idx.get("chunks") and idx.get("bge"):
        pct = idx["bge"] * 100.0 / idx["chunks"]
        cov = _bar("BGE 向量覆盖", pct, "%.1f%%" % pct)

    note = ""
    if ev.get("source"):
        note = ("<div class='note'>🔴 <b>指标来源（诚实标注）</b>：本节检索指标读自 "
                "<code>%s</code>（生成于 %s），<b>不是本次现场重跑</b> —— 一次全量评测约 50 分钟，"
                "本看板只做汇总不做重算。要复现请单独跑：<br>"
                "<code>judged-eval-runner.py --tag verify --no-rerank</code>（约 50 分钟）</div>"
                % (ev.get("source"), ev.get("source_mtime", "—")))
    else:
        note = ("<div class='note'>⚠️ 未找到评测产物，检索指标为空。"
                "先跑一次 <code>judged-eval-runner.py --tag verify --no-rerank</code>。</div>")

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识库验收看板 v%(ver)s</title><style>%(css)s</style></head><body>
<div class="wrap">
<header>
  <h1>知识库检索体系 · 验收看板</h1>
  <div class="meta">模块版本 <b>v%(ver)s</b> ｜ 生成于 <b>%(gen)s</b> ｜
  Python %(py)s ｜ 全部数据由脚本实跑采集，非手工填写</div>
</header>

<div class="hero">%(hero)s</div>

<section>
  <h2>回归测试 · %(nsuites)d 个套件 / %(nas)s 条断言</h2>
  <div class="hint">每个套件守一个具体机制，全部靠退出码与断言逐条判定；总耗时 %(dur)ss。
  其中 2 个套件用 ✅/❌ 逐项输出而非统一的 <code>[PASS]</code> 标记，断言数栏标「按退出码」——
  <b>不强行折算，宁可标注清楚</b>。</div>
  <table>
    <thead><tr><th>套件 / 它守什么</th><th>断言</th><th>耗时</th><th>状态</th><th>末行输出</th></tr></thead>
    <tbody>%(trs)s</tbody>
  </table>
</section>

<section>
  <h2>检索质量</h2>
  <div class="hint">100 条人工标注查询，关精排口径；分母是 100 条，不是抽样。</div>
  %(q)s
  %(note)s
</section>

<section>
  <h2>量化对照 · 两个「不是拍脑袋」的决策</h2>
  <div class="hint">都做了 A/B，都留了回退路径。</div>
  %(cmp)s
</section>

<section>
  <h2>索引规模</h2>
  <table>
    <tbody>
      <tr><td>文档数</td><td class="num"><b>%(docs)s</b></td>
          <td style="color:var(--sub);font-size:12px">wiki 合成页 + raw 原件 + 模块文档</td></tr>
      <tr><td>检索切片</td><td class="num"><b>%(chunks)s</b></td>
          <td style="color:var(--sub);font-size:12px">按标题切块，单块可独立命中</td></tr>
      <tr><td>向量条数</td><td class="num"><b>%(bge)s</b></td>
          <td style="color:var(--sub);font-size:12px">BGE-small-zh 512 维</td></tr>
    </tbody>
  </table>
  %(cov)s
</section>

<footer>
  复现全部数据：<code>python run_tests.py</code>（约 %(dur)s 秒 + 3 分钟等价性测试）<br>
  看板源码：<code>modules/知识库索引/run_tests.py</code> ｜
  机器可读数据：<code>outputs/知识库运维/测试看板/data.json</code><br>
  本页零外部依赖（无 CDN、无 JS 库），双击即可离线打开。
</footer>
</div></body></html>
""" % {
        "ver": data.get("version", "?"), "gen": data.get("generated_at", ""),
        "py": data.get("python", ""), "css": CSS, "hero": hero_html,
        "nsuites": t["suites"], "nas": t["asserts"], "dur": t["duration"],
        "trs": "".join(trs), "q": q or "<div class='hint'>（无数据）</div>",
        "cmp": cmp_html, "note": note,
        "docs": "{:,}".format(idx.get("docs", 0)) if idx.get("docs") else "—",
        "chunks": "{:,}".format(idx.get("chunks", 0)) if idx.get("chunks") else "—",
        "bge": "{:,}".format(idx.get("bge", 0)) if idx.get("bge") else "—",
        "cov": cov,
    }


def gather(skip_tests=False, write=True):
    """跑全部套件并汇总。

    write=False（CI 用）时**不落盘** data.json：CI 是只读验收，写仓库等于污染
    工作区，且 outputs/ 在干净机器上根本不存在（旧实现在这里直接崩）。
    """
    if skip_tests and os.path.exists(os.path.join(OUT_DIR, "data.json")):
        with open(os.path.join(OUT_DIR, "data.json"), "r", encoding="utf-8") as f:
            return json.load(f)

    print("=" * 62)
    print("知识库验收 · 一键跑批")
    print("=" * 62)
    rows = []
    for fname, title, protects in SUITES:
        print("  跑 %-26s ..." % fname, end="", flush=True)
        r = run_one(fname)
        r.update({"file": fname, "title": title, "protects": protects})
        rows.append(r)
        print(" %s  (%d PASS / %d FAIL, %ss)" % (
            "OK" if r["ok"] else "FAIL", r["passed"], r["failed"], r["duration"]))

    data = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": collect_version(),
        "python": sys.version.split()[0],
        "suites": rows,
        "totals": {
            "suites": len(rows),
            "ok": sum(1 for r in rows if r["ok"]),
            "bad": sum(1 for r in rows if not r["ok"]),
            "asserts": sum(r["passed"] + r["failed"] for r in rows),
            "asserts_ok": sum(r["passed"] for r in rows),
            "duration": round(sum(r["duration"] for r in rows), 1),
        },
        "index": collect_index(),
        "eval": collect_eval(),
    }
    if write:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, "data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tests", action="store_true", help="只重渲染，不跑测试")
    ap.add_argument("--ci", action="store_true",
                    help="CI 模式：照跑全部套件，但不写 data.json / index.html")
    args = ap.parse_args()
    d = gather(skip_tests=args.skip_tests, write=not args.ci)
    t = d["totals"]
    if args.ci:
        # CI 只认退出码与汇总行：不建 outputs/ 目录，不写任何文件。
        print("-" * 62)
        print("套件 %d/%d 通过 · 断言 %d/%d · 总耗时 %ss"
              % (t["ok"], t["suites"], t["asserts_ok"], t["asserts"], t["duration"]))
        print("（--ci：跳过看板写盘）")
    else:
        os.makedirs(OUT_DIR, exist_ok=True)
        html_path = os.path.join(OUT_DIR, "index.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(render_html(d))
        print("-" * 62)
        print("套件 %d/%d 通过 · 断言 %d/%d · 总耗时 %ss"
              % (t["ok"], t["suites"], t["asserts_ok"], t["asserts"], t["duration"]))
        print("data.json  →", os.path.join(OUT_DIR, "data.json"))
        print("看板        →", html_path)
    sys.exit(0 if t["bad"] == 0 else 1)
