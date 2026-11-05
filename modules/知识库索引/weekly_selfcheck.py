#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_selfcheck — 知识库每周自测（每周一 09:00 自动跑）
=========================================================

目标只有两个：**减少幻觉、提高准确率**。翻译成可执行动作就是六件事：

  [1/6] 索引健康   —— 文档数、本周新增/失效、内容是否落后于磁盘（读到过期内容是幻觉温床）
  [2/6] 数据库健康 —— page_size / 碎片率 / integrity，有问题让 db_admin 修
  [3/6] 检索质量   —— 30/100 评测集跑四方式 Recall@5 + MRR
  [4/6] 幻觉风险   —— 空检率（检索为空却硬答）、失效内链、index.md 幽灵条目
  [5/6] 回归门禁   —— 与上周基线对比，掉点超过阈值就 FAIL（exit 1），不是只记不看
  [5.5/6] 主集门禁 —— 调 eval_gate.py 跑版本化评测集（eval_sets/），rc 非 0 一并拦；缺尺子明确 SKIP
  [6/6] 自动优化   —— ANALYZE + FTS5 optimize + checkpoint，让库越跑越健康而不是越跑越烂

产出：
  reports/<日期>-selfcheck.md   人读版周报
  state/selfcheck_latest.json   机器读（供 dashboard / 其他脚本）
  state/selfcheck.db            历史基线表，周周可比

用法：
  python weekly_selfcheck.py                 完整自测 + 更新基线
  python weekly_selfcheck.py --no-update     只测不更新基线
  python weekly_selfcheck.py --gate 5.0      门禁阈值放宽到 5 个点（默认 3）
  python weekly_selfcheck.py --skip-links    跳过失效内链扫描（大库慢时用）
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
KB_IDX = os.path.join(os.path.dirname(HERE), "知识库索引")
sys.path.insert(0, KB_IDX)

from kb_index import connect, DB_PATH, search_fts, search_tfidf, search_hybrid, search_fts_chunks, search_fts_fused, KB_ROOT  # noqa: E402
import vec_tables  # noqa: E402  #37：四表一致性（与 build 补写同一口径）

CHECK_DB = os.path.join(HERE, "state", "selfcheck.db")
REPORT_DIR = os.path.join(HERE, "reports")
JSON_OUT = os.path.join(HERE, "state", "selfcheck_latest.json")
TOPK = 5
MODES = [("fts", "FTS5 整篇"), ("fts_chunk", "FTS5 分块"), ("fts_fused", "FTS5 粒度融合"),
         ("tfidf", "TF-IDF 稀疏"), ("hybrid", "混合等权 RRF"), ("hybrid_w2", "混合加权 RRF 2:1"),
         ("vec", "LSA 稠密向量"), ("fused_vec", "关键词⊕LSA 融合（默认）")]
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FALLBACK_PATH_RE = re.compile(r"`([\w一-鿿/\-\.]+\.(?:md|csv|json|png|jpg|pdf|docx|xlsx|py))`")
CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


# ============ [1] 索引健康 ============
def check_index(conn_kb):
    import hashlib
    doc_count = conn_kb.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    stale = missing = 0
    for path, h, content in conn_kb.execute(
        "SELECT path, content_hash, content FROM docs"
    ):
        full = os.path.join(KB_ROOT, path)
        if not os.path.exists(full):
            missing += 1
            continue
        # 🔴 2026-10-26 修：与 build 实际索引的「正文内容」(content 列) 比对，
        # 而不是原始文件字节。raw 抽取型文件（docx/pdf/txt）原文件字节的 hash
        # 永远 ≠ 正文 hash，旧逻辑会把它们全部误判为「内容落后磁盘」，
        # 造成 3307 篇假阳性 + 回归门禁误 FAIL。build 已保证
        # content_hash = sha1(content)，故本检查退化为「库内自洽」校验，
        # 磁盘→索引的新鲜度由 build（在自测前已跑，rc=0）负责。
        if hashlib.sha1((content or "").encode("utf-8")).hexdigest() != h:
            stale += 1
    row = conn_kb.execute("SELECT value FROM index_state WHERE key='last_build'").fetchone()
    # 🔴 #37（2026-11-03）：四表一致性。**只追加字段、不改既有键**——
    # 历史 JSON 消费者（看板）只认识 doc_count/stale_docs/... 那几项。
    # 周自检只记录不判 FAIL：个人档机器没有 GPU 时 m3 天然不全，那是允许状态
    # （判红会让「周自检 FAIL」变成噪音，真故障反而被淹没）。
    try:
        vec = vec_tables.diff_report(conn_kb)
    except Exception as e:
        vec = {"ok": None, "error": "%s: %s" % (type(e).__name__, str(e)[:80])}
    return {"doc_count": doc_count, "stale_docs": stale, "missing_docs": missing,
            "last_build": row[0] if row else "未知", "vec_tables": vec}


# ============ [2] 数据库健康 ============
def check_db(conn_kb):
    page = conn_kb.execute("PRAGMA page_size").fetchone()[0]
    pages = conn_kb.execute("PRAGMA page_count").fetchone()[0]
    free = conn_kb.execute("PRAGMA freelist_count").fetchone()[0]
    frag = (free / pages * 100) if pages else 0
    integ = conn_kb.execute("PRAGMA integrity_check").fetchall()
    return {"page_size": page, "frag_pct": round(frag, 1),
            "integrity": "ok" if (len(integ) == 1 and integ[0][0] == "ok") else "FAIL",
            "size_mb": round(os.path.getsize(DB_PATH) / 1048576, 2)}


# ============ [3] 检索质量 ============
def run_eval(conn_kb, items):
    try:
        import vector_index as _vi
        fn_map = {"fts": search_fts, "fts_chunk": search_fts_chunks, "fts_fused": search_fts_fused,
                  "tfidf": search_tfidf, "hybrid": search_hybrid,
                  "vec": _vi.search_vec, "fused_vec": _vi.search_fused_vec,
                  "hybrid_w2": lambda c, q, k: search_hybrid(c, q, k, w_fts=2.0, w_tfidf=1.0)}
    except ImportError:
        fn_map = {"fts": search_fts, "fts_chunk": search_fts_chunks, "fts_fused": search_fts_fused,
                  "tfidf": search_tfidf, "hybrid": search_hybrid,
                  "hybrid_w2": lambda c, q, k: search_hybrid(c, q, k, w_fts=2.0, w_tfidf=1.0)}
    res = {}
    for mode, _ in MODES:
        fn = fn_map[mode]
        hit = rr = 0.0
        for it in items:
            hits = fn(conn_kb, it["query"], TOPK)
            rank = next((i for i, h in enumerate(hits, 1) if h["path"] == it["gt"]), 0)
            if rank:
                hit += 1
                rr += 1.0 / rank
        res[mode] = {"recall": hit / len(items) if items else 0,
                     "mrr": rr / len(items) if items else 0}
    return res


# ============ [4] 幻觉风险 ============
def check_empty(conn_kb, items):
    """空检率：检索一条都捞不出来时，LLM 最容易开始编故事。"""
    empty = [it["query"] for it in items if not search_hybrid(conn_kb, it["query"], 1)]
    return empty, (len(empty) / len(items) * 100) if items else 0


_NAME_INDEX = None


def _name_index():
    """全库「文件名小写 → 绝对路径列表」，一次建好供所有失效内链共用。

    🔴 为什么要建索引（2026-10-26 实测）：原实现每条死链都跑一次
    `glob(KB_ROOT/"**", base, recursive=True)` + `isfile`，**单次 4.1–4.8 秒**；
    死链有 N 条就把整棵库走 N 遍（2026-10-15 那轮修了 4 条＝白等 16 秒）。
    `os.scandir` 全库建索引约 1 秒，之后每条死链只是字典查询。

    🔴 两个必须刻意对齐的口径（不对齐＝悄悄改语义，等价性验证会抓出来）：
    1. **键一律小写**。Windows 文件系统不区分大小写，旧的 `glob('README.md')` 能命中
       磁盘上的 `readme.md`（npm 包里就是小写）；若按键名精确匹配，这类会从「唯一命中」
       退化成「库内无同名文件」。
    2. **跳过以 `.` 开头的目录**。`glob` 默认 `include_hidden=False` 不进隐藏目录；
       若这里进去了，`.workbuddy/skills/*/README.md` 之类会突然成为「唯一命中」，
       而 `fix_links` 对唯一命中是**自动改链接**——等于把死链自动指向 .workbuddy。
    """
    global _NAME_INDEX
    if _NAME_INDEX is not None:
        return _NAME_INDEX
    idx = {}
    stack = [KB_ROOT]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not e.name.startswith("."):
                                stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            idx.setdefault(e.name.lower(), []).append(e.path)
                    except OSError:
                        continue
        except OSError:
            continue
    _NAME_INDEX = idx
    return idx


def suggest(target, from_file):
    """失效路径的正确 candidates：按文件名在库里找，唯一命中就给建议路径。"""
    base = os.path.basename(target)
    if not base:
        return "（库内无同名文件，需人工判断）"
    idx = _name_index()
    # 🔴 通配符分支不能省：旧实现走 glob，天然支持 `20_*.md` 这种写法
    #    （记忆日志里确实这么记过，如 "modules/bug记录/20_*.md"）。
    #    直接查字典会把这类从「唯一命中」降级成「库内无同名文件」——那是收窄判据。
    if any(ch in base for ch in "*?["):
        import fnmatch
        pat = base.lower()
        hits = sorted(p for k in idx if fnmatch.fnmatchcase(k, pat) for p in idx[k])
    else:
        hits = sorted(idx.get(base.lower(), []))
    if len(hits) == 1:
        rel = os.path.relpath(hits[0], os.path.dirname(os.path.join(KB_ROOT, from_file)))
        return rel.replace("\\", "/")
    if len(hits) > 1:
        return "候选 %d 个：%s" % (len(hits), "、".join(
            os.path.relpath(h, KB_ROOT).replace("\\", "/") for h in hits[:3]))
    return "（库内无同名文件，需人工判断）"


def fix_links(dead, dry_run=True):
    """自动修死链 —— 只修「库内唯一同名文件」的，绝不猜。
    安全兜底：改前把原文备份进 state/linkfix_backup/，一行替换一条，全量留痕。
    """
    backup_dir = os.path.join(HERE, "state", "linkfix_backup")
    os.makedirs(backup_dir, exist_ok=True)
    fixed, skipped = [], []
    for d in dead:
        sug = d.get("suggest", "")
        if sug.startswith("候选") or sug.startswith("（"):
            skipped.append(d)
            continue
        src = os.path.join(KB_ROOT, d["from"])
        if not os.path.exists(src):
            skipped.append(d)
            continue
        with open(src, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if d["target"] not in text:
            skipped.append(d)
            continue
        new = text.replace("(%s)" % d["target"], "(%s)" % sug)
        if dry_run:
            fixed.append((d, len(text) != len(new) or True))
            continue
        bak = os.path.join(backup_dir, "%s_%s" % (
            datetime.now().strftime("%Y%m%d_%H%M%S"),
            d["from"].replace("/", "__")))
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        with open(bak, "w", encoding="utf-8") as f:
            f.write(text)
        with open(src, "w", encoding="utf-8") as f:
            f.write(new)
        fixed.append(d)
    return fixed, skipped


def exempt_reason(from_path, target):
    """豁免判定：**返回 (是否豁免, 理由)**，理由会公开写进周报，不藏着。

    🔴 豁免不等于放过。原则：
      · 只豁免「时间上的历史快照」——它是有价值的历史记录，不是垃圾
      · 不豁免「内容上的错误」——路径写错、文件真丢了，该修还得修
      · 每条豁免都要能在报告里看到，作者随时能推翻

    当前唯一豁免场景（2026-10-15 作者确认）：
      每日运势同一天被重跑过多次，旧批次文档引用的图只存在于**当时那一批**的产物里，
      后续重跑不会补回来。作者原话：「运势图不用清理以前的，就算重跑了，
      也不是同一天同一时刻的」——即这些是**各自时点的快照，本就该分开存**。
    """
    if not target.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return False, ""
    fp = from_path.replace("\\", "/")
    if "/wiki/生活/" not in ("/" + fp):
        return False, ""
    base = os.path.basename(fp)
    if not any(k in base for k in ("详解", "多术数交叉验证", "每日运势")):
        return False, ""
    return True, "每日运势同日重跑产生的历史批次快照（作者 2026-10-15 确认保留，不清理）"


def check_dead_links(limit=400):
    """失效内链：md 里写了但磁盘上不存在的路径 —— 引用不存在的文档是典型幻觉产物。"""
    dead = []
    n_files = 0
    wiki_root = os.path.join(KB_ROOT, "wiki")
    seen_targets = set()
    for dirpath, _, files in os.walk(wiki_root):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            n_files += 1
            if n_files > limit:
                break
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            # 先剥代码块与行内代码：文档里写的「示例链接」不是真引用，算它死链是误报
            text = INLINE_CODE_RE.sub("", CODE_FENCE_RE.sub("", text))
            for m in LINK_RE.finditer(text):
                target = m.group(1).strip().split("#")[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                target = target.split()[0] if " " in target else target
                full = os.path.normpath(os.path.join(dirpath, target))
                if not os.path.exists(full) and target not in seen_targets:
                    seen_targets.add(target)
                    rel_from = os.path.relpath(p, KB_ROOT).replace("\\", "/")
                    ok, why = exempt_reason(rel_from, target)
                    dead.append({"from": rel_from, "target": target,
                                 "suggest": suggest(target, rel_from),
                                 "exempt": ok, "exempt_reason": why})
    return dead, n_files


def split_exempt(dead):
    """把失效内链分成「要修的」和「已豁免的」，豁免部分照实报数不隐藏。"""
    real = [d for d in dead if not d.get("exempt")]
    waived = [d for d in dead if d.get("exempt")]
    return real, waived


def check_index_md():
    """wiki/index.md 是 LLM 找文件的第一站，里面有幽灵条目就等于给了错的地图。"""
    idx_path = os.path.join(KB_ROOT, "wiki", "index.md")
    if not os.path.exists(idx_path):
        return [], "（无 index.md）"
    ghosts = []
    with open(idx_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    total = 0
    for m in LINK_RE.finditer(text):
        t = m.group(1).strip().split("#")[0].strip()
        if not t or t.startswith(("http://", "https://")):
            continue
        total += 1
        full = os.path.normpath(os.path.join(KB_ROOT, "wiki", t))
        if not os.path.exists(full) and not os.path.exists(os.path.join(KB_ROOT, t)):
            ghosts.append(t)
    return ghosts, "扫描 %d 条登记" % total


# ============ [6] 自动优化 ============
def check_traceability():
    """引用可追溯：网上来源有没有标 Source，库内引用能不能落到真实文件。

    门禁设计原则：**只把「机器能判定且应立刻修」的项设为门禁。**
    库内引用指向不存在的文件 → 立刻能修 → 进门禁；
    缺来源标注 → 得人工翻原始资料才知道出处 → 只做观测项，持续可见即可，
    否则常年 FAIL 会让门禁失去意义。
    """
    try:
        import traceability as tr
    except ImportError:
        return None
    rows, _ = tr.scan()
    dead_links, dead_raw = tr.check_targets(rows)
    m = tr.compute(rows, dead_links, dead_raw)
    m["dead_link_list"] = dead_links
    m["missing_source_docs"] = [r["path"] for r in rows if r["has_url"] and not r["has_source"]]
    return m


def check_answer_layer():
    """答案层评测：检索捞到了 ≠ 答案说对了。样本来自平时真实问答的沉淀。"""
    try:
        import answer_eval as ae
    except ImportError:
        return None
    if not os.path.exists(ae.SAMPLES):
        return None
    samples = [s for s in json.load(open(ae.SAMPLES, encoding="utf-8"))
               if s.get("answer", "").strip()]
    if not samples:
        return None
    m, _ = ae.evaluate(samples)
    # 附上 judge 版忠实度（如果有）—— 弱代理只能盯趋势，judge 才是真分数
    jp = os.path.join(HERE, "state", "judge_scores.json")
    if os.path.exists(jp):
        try:
            js = json.load(open(jp, encoding="utf-8"))
            m["judge_faithfulness"] = sum(d["faithfulness"] for d in js) / len(js)
            m["judge_full"] = len([d for d in js if d["faithfulness"] == 5])
            m["judge_n"] = len(js)
        except Exception:
            pass
    return m


def auto_optimize(conn_kb):
    steps = []
    t0 = time.time()
    conn_kb.execute("ANALYZE")
    try:
        conn_kb.execute("INSERT INTO docs_fts(docs_fts) VALUES('optimize')")
        steps.append("FTS5 倒排段合并")
    except sqlite3.OperationalError as e:
        steps.append("FTS5 optimize 失败：%s" % e)
    conn_kb.execute("PRAGMA optimize")
    conn_kb.commit()
    conn_kb.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    steps.append("ANALYZE + checkpoint（%.2fs）" % (time.time() - t0))
    return steps


def ensure_baseline(conn_chk):
    conn_chk.execute("""CREATE TABLE IF NOT EXISTS baseline(
        week TEXT PRIMARY KEY, doc_count INTEGER, notes TEXT,
        r_fts REAL, r_tfidf REAL, r_hybrid REAL, r_hybrid_w2 REAL,
        m_fts REAL, m_hybrid_w2 REAL, created_at TEXT)""")
    cols = {r[1] for r in conn_chk.execute("PRAGMA table_info(baseline)")}
    for col, ddl in [("empty_rate", "REAL"), ("stale_docs", "INTEGER"),
                     ("dead_links", "INTEGER"), ("db_frag", "REAL"),
                     ("db_size_mb", "REAL"), ("status", "TEXT")]:
        if col not in cols:
            conn_chk.execute("ALTER TABLE baseline ADD COLUMN %s %s" % (col, ddl))


def cmd_run(args):
    os.makedirs(os.path.dirname(CHECK_DB), exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    t0 = time.time()
    conn_kb = connect()
    conn_chk = sqlite3.connect(CHECK_DB)
    ensure_baseline(conn_chk)

    print("=== 知识库周自测 %s ===" % datetime.now().strftime("%Y-W%V"))

    idx = check_index(conn_kb)
    print("\n[1/6] 索引健康：%d 篇｜内容落后磁盘 %d 篇｜源文件丢失 %d 篇｜上次构建 %s" % (
        idx["doc_count"], idx["stale_docs"], idx["missing_docs"], idx["last_build"]))
    vec = idx.get("vec_tables") or {}
    if vec.get("ok"):
        print("[1/6] 四表一致性：✅ 无缺口（chunks=%d chunk_bge=%d vec_m3=%d trigram=%d）" % (
            vec["counts"]["chunks"], vec["counts"]["chunk_bge"],
            vec["counts"]["vec_m3"], vec["counts"]["chunks_trigram"]))
    elif vec.get("tables_ok") is False:
        # 表都没建（全新库）：谈"缺口数"是误导（lag 全是 0），如实说未齐
        print("[1/6] 四表一致性：⚠️ 四表未齐（缺 %s），跳过判定" % (
            "、".join(vec.get("absent_tables") or [])))
    elif vec.get("error"):
        print("[1/6] 四表一致性：⚠️ 无法校验（%s）" % vec["error"])
    else:
        print("[1/6] 四表一致性：⚠️ 缺 bge=%d m3=%d trigram=%d"
              "（只告警不 FAIL：个人档无 GPU 时 m3 不全属正常）" % (
                  vec.get("lag_bge", -1), vec.get("lag_m3", -1), vec.get("lag_tri", -1)))

    db = check_db(conn_kb)
    print("[2/6] 数据库：%.2f MB｜page_size %d｜碎片 %.1f%%｜完整性 %s" % (
        db["size_mb"], db["page_size"], db["frag_pct"], db["integrity"]))

    results = {}
    all_items = []
    for name, fname in [("30", "kb_eval_30.json"), ("100", "kb_eval_100.json")]:
        path = os.path.join(KB_IDX, "state", fname)
        if not os.path.exists(path):
            print("[缺] 评测集不存在：%s" % path)
            continue
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
        all_items.extend(items)
        results[name] = run_eval(conn_kb, items)
        print("\n[3/6] 检索质量 —— 评测集 %s 条：" % name)
        for mode, label in MODES:
            print("        %-16s Recall@5 %.1f%%  MRR %.4f" % (
                label, results[name][mode]["recall"] * 100, results[name][mode]["mrr"]))

    empty_qs, empty_rate = check_empty(conn_kb, all_items)
    dead, n_files = ([], 0) if args.skip_links else check_dead_links()
    ghosts, index_note = check_index_md()
    trace = check_traceability()
    print("\n[4/6] 幻觉风险：")
    print("        空检率 %.1f%%（%d/%d 条 query 检索为空）" % (empty_rate, len(empty_qs), len(all_items)))
    real_dead, waived_dead = split_exempt(dead)
    print("        失效内链 %d 条待修（扫了 %d 篇 wiki）%s" % (
        len(real_dead), n_files,
        "｜已豁免 %d 条（历史快照，见周报）" % len(waived_dead) if waived_dead else ""))
    print("        index.md 幽灵条目 %d 条 —— %s" % (len(ghosts), index_note))
    if trace:
        print("        引用可追溯：网上来源溯源覆盖 %.1f%%（缺 %d 篇）｜库内引用可解析 %.1f%%（失效 %d）" % (
            trace["url_trace_coverage"], len(trace["missing_source_docs"]),
            trace["internal_link_resolve_rate"], len(trace["dead_link_list"])))
    ans = check_answer_layer()
    if ans:
        extra = ""
        if ans.get("judge_faithfulness") is not None:
            extra = "｜judge 忠实度 %.2f/5（满分 %d/%d）" % (
                ans["judge_faithfulness"], ans["judge_full"], ans["judge_n"])
        print("        答案层（%d 条样本）：引用准确率 %.1f%%｜幽灵路径 %.1f%%｜上下文支撑 %.3f%s" % (
            ans["n"], ans["citation_accuracy"], ans["ghost_path_rate"],
            ans["context_support"], extra))
    for q in empty_qs[:5]:
        print("          · 空检：「%s」" % q)
    for d in dead[:5]:
        print("          · 死链：%s → %s" % (d["from"], d["target"]))

    if dead and args.fix_links:
        fixed, skipped = fix_links(dead, dry_run=args.dry_run)
        print("\n[4.5] 死链修复%s：修复 %d 条，跳过 %d 条（需人工）" % (
            "（dry-run）" if args.dry_run else "", len(fixed), len(skipped)))
        for d in skipped:
            print("        · 跳过：%s → %s ｜ %s" % (
                d["from"], d["target"], d.get("suggest", "")))

    # ---- [5] 回归门禁 ----
    week = datetime.now().strftime("%Y-W%V")
    prev = conn_chk.execute(
        "SELECT r_fts, r_hybrid_w2, doc_count, empty_rate, dead_links "
        "FROM baseline ORDER BY created_at DESC LIMIT 1").fetchone()
    gate_msgs, status = [], "PASS"
    main = "100" if "100" in results else next(iter(results), None)
    if prev and main:
        cur_fts = results[main]["fts"]["recall"] * 100
        cur_hyb = results[main]["hybrid_w2"]["recall"] * 100
        d_fts, d_hyb = cur_fts - prev[0] * 100, cur_hyb - prev[1] * 100
        print("\n[5/6] 回归门禁（阈值 ±%.1f 点，基线 %s）：" % (args.gate, week))
        print("        FTS5      %.1f%% → %.1f%%  %+.1f" % (prev[0] * 100, cur_fts, d_fts))
        print("        混合加权  %.1f%% → %.1f%%  %+.1f" % (prev[1] * 100, cur_hyb, d_hyb))
        print("        文档数    %d → %d" % (prev[2], idx["doc_count"]))
        for label, d in [("FTS5", d_fts), ("混合加权", d_hyb)]:
            if d < -args.gate:
                gate_msgs.append("%s 掉 %.1f 点，超过 %.1f 点阈值" % (label, -d, args.gate))
        if empty_rate > args.max_empty:
            gate_msgs.append("空检率 %.1f%% 超过 %.1f%% 上限" % (empty_rate, args.max_empty))
        if idx["stale_docs"] > 0:
            gate_msgs.append("%d 篇文档内容已变未重建索引" % idx["stale_docs"])
        if real_dead:
            gate_msgs.append("%d 条失效内链待修" % len(real_dead))
        status = "FAIL" if gate_msgs else "PASS"
        print("        结论：%s %s" % (status, ("｜" + "；".join(gate_msgs)) if gate_msgs else ""))
    else:
        print("\n[5/6] 无历史基线，本次作为首版基线（下次起做对比）")
        status = "BASELINE"   # 🔴 不假装 PASS：没有对照就没有「通过」这回事

    # ---- [5.5] 版本化主集门禁（W3 2026-11-04；2026-11-06 双向合并）----
    # 为什么单独调：本脚本跑的 30/100 是旧饱和集，只考「查表」，判不了
    # 「改索引 / 切块 / RRF 权重后到底有没有变好」。新门禁读版本化主集
    # eval_sets/manifest.json，R@5 掉 >2pp 或缺桶就 rc 非 0。
    # 🔴 eval_gate 自己用 readonly 连接，不会污染 query_log。
    # 🔴 缺工具/缺主集时**明确 SKIP**：不假装 PASS，也不把「没尺子」误判成 FAIL。
    #    调用失败只告警、不改 status：工具挂了不该让周自测永久红（verify_day 教训）。
    gate_rc, gate_err = None, ""
    _gate_script = os.path.join(KB_IDX, "eval_gate.py")
    _manifest = os.path.join(KB_IDX, "eval_sets", "manifest.json")
    if not (os.path.isfile(_gate_script) and os.path.isfile(_manifest)):
        print("\n[5.5/6] 版本化主集门禁：⏳ SKIP（缺 eval_gate.py / eval_sets/manifest.json）")
    else:
        try:
            import subprocess
            _r = subprocess.run(
                [sys.executable, _gate_script],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=900)
            gate_rc = _r.returncode
            print("\n[5.5/6] 版本化主集门禁：rc=%d" % gate_rc)
            for _ln in (_r.stdout or "").strip().splitlines():
                if _ln.startswith(("R@5", "分桶", "判定", "query_log")):
                    print("        " + _ln)
            if gate_rc:
                gate_msgs.append("eval_gate rc=%d（见 eval_sets/gate_latest.json）" % gate_rc)
                status = "FAIL"
        except Exception as e:
            gate_err = str(e)[:120]
            print("        ⚠ eval_gate 调用失败（不改结论，仅告警）：%s" % gate_err)

    print("\n[6/6] 自动优化：")
    for s in auto_optimize(conn_kb):
        print("        ✓ %s" % s)

    # ---- 落盘 ----
    payload = {"week": week, "checked_at": datetime.now().isoformat(timespec="seconds"),
               "index": idx, "db": db, "results": results,
               "empty_rate": empty_rate, "empty_queries": empty_qs,
               "dead_links": dead, "ghost_entries": ghosts,
               "status": status, "gate_messages": gate_msgs,
               "eval_gate_rc": gate_rc, "eval_gate_error": gate_err,
               "baseline_prev": {"fts": prev[0] if prev else None,
                                 "hybrid_w2": prev[1] if prev else None,
                                 "week": "上一版"}}
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if not args.no_update and main:
        r = results[main]
        conn_chk.execute(
            "INSERT OR REPLACE INTO baseline(week,doc_count,notes,r_fts,r_tfidf,r_hybrid,"
            "r_hybrid_w2,m_fts,m_hybrid_w2,created_at,empty_rate,stale_docs,dead_links,"
            "db_frag,db_size_mb,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (week, idx["doc_count"], "每周一自动自测", r["fts"]["recall"], r["tfidf"]["recall"],
             r["hybrid"]["recall"], r["hybrid_w2"]["recall"], r["fts"]["mrr"],
             r["hybrid_w2"]["mrr"], datetime.now().isoformat(timespec="seconds"),
             empty_rate, idx["stale_docs"], len(real_dead), db["frag_pct"], db["size_mb"], status))
        conn_chk.commit()
        print("\n基线已更新：%s" % week)

    # ---- 周报 ----
    rd = []
    rd.append("# 知识库周自测 %s\n" % datetime.now().strftime("%Y-%m-%d (%V 周)"))
    rd.append("**结论：%s**｜耗时 %.1fs\n" % (status, time.time() - t0))
    rd.append("## 一、索引与数据库\n")
    rd.append("| 指标 | 数值 | 说明 |\n|---|---|---|")
    rd.append("| 文档数 | %d | wiki 合成层 + modules README + SKILL |" % idx["doc_count"])
    rd.append("| 内容落后磁盘 | %d | >0 说明该跑 build |" % idx["stale_docs"])
    rd.append("| 源文件丢失 | %d | 索引里有但磁盘没了 |" % idx["missing_docs"])
    rd.append("| 库大小 | %.2f MB | page_size %d |" % (db["size_mb"], db["page_size"]))
    rd.append("| 碎片率 | %.1f%% | >20%% 建议 vacuum |" % db["frag_pct"])
    rd.append("| 完整性 | %s | integrity_check |" % db["integrity"])
    _v = idx.get("vec_tables") or {}
    if _v.get("ok"):
        rd.append("| 四表一致性 | ✅ 无缺口 | chunks/chunk_bge/vec_m3/chunks_trigram 同一时刻一致 |")
    elif _v.get("tables_ok") is False:
        rd.append("| 四表一致性 | ⚠️ 四表未齐 | 缺 %s（表还没建出来，不做判定） |"
                  % "、".join(_v.get("absent_tables") or []))
    elif _v.get("error"):
        rd.append("| 四表一致性 | ⚠️ 无法校验 | %s |" % _v["error"])
    else:
        rd.append("| 四表一致性 | ⚠️ 缺 bge %d / m3 %d / trigram %d | 只告警不 FAIL（个人档无 GPU 时 m3 不全属正常）；"
                  "企业档（KB_BACKFILL_M3=1）见 `kb_rag.py doctor` |" % (
                      _v.get("lag_bge", -1), _v.get("lag_m3", -1), _v.get("lag_tri", -1)))

    rd.append("")
    for name in results:
        rd.append("## 检索质量（评测集 %s 条）\n" % name)
        rd.append("| 检索方式 | Recall@5 | MRR@5 |\n|---|---|---|")
        for mode, label in MODES:
            rd.append("| %s | %.1f%% | %.4f |" % (
                label, results[name][mode]["recall"] * 100, results[name][mode]["mrr"]))
        rd.append("")
    rd.append("## 幻觉风险\n")
    rd.append("| 风险项 | 数量 | 为什么重要 |\n|---|---|---|")
    rd.append("| 空检 query | %d（%.1f%%） | 检索为空还硬答 = 编故事 |" % (len(empty_qs), empty_rate))
    rd.append("| 失效内链 | %d 待修 / %d 豁免 | md 引用了不存在的文件 |" % (
        len(real_dead), len(waived_dead)))
    rd.append("| index.md 幽灵条目 | %d | LLM 按错误地图找文件 |" % len(ghosts))
    rd.append("| 过期内容 | %d | 读到旧版 = 事实性错误 |" % idx["stale_docs"])
    if trace:
        rd.append("| 网上来源溯源覆盖 | %.1f%% | 含外部链接的文章里 **%d 篇没标来源** |" % (
            trace["url_trace_coverage"], len(trace["missing_source_docs"])))
        rd.append("| 库内引用可解析 | %.1f%% | %d 条引用指向不存在的文件 |" % (
            trace["internal_link_resolve_rate"], len(trace["dead_link_list"])))
    if ans:
        rd.append("| 答案引用准确率 | %.1f%% | 答案引用的路径是否都来自上下文 |" % ans["citation_accuracy"])
        rd.append("| 答案幽灵路径 | %.1f%% | 🔴 引用不存在的路径 = 幻觉 |" % ans["ghost_path_rate"])
        rd.append("| 答案上下文支撑 | %.3f | faithfulness 弱代理（%d 条样本） |" % (
            ans["context_support"], ans["n"]))
    rd.append("")
    if empty_qs:
        rd.append("**空检 query 全表**：%s\n" % "、".join("「%s」" % q for q in empty_qs))
    if trace and trace["missing_source_docs"]:
        rd.append("### 待补来源标注（%d 篇｜🔴 必须人工翻原始资料补，脚本不许代填）\n" % len(
            trace["missing_source_docs"]))
        rd.append("补法：文首加一行 `> Sources: <资料标题>, <YYYY-MM-DD>`\n")
        for p in trace["missing_source_docs"]:
            rd.append("- `%s`" % p)
        rd.append("")
    if waived_dead:
        rd.append("")
        rd.append("**已豁免 %d 条（不改、不删，理由公开在此，作者可随时推翻）**\n" % len(waived_dead))
        rd.append("| 来源文档 | 失效目标 | 豁免理由 |")
        rd.append("|---|---|---|")
        for d in waived_dead:
            rd.append("| `%s` | `%s` | %s |" % (
                d["from"], d["target"], d.get("exempt_reason", "—")))
        rd.append("")
        rd.append("> 豁免 ≠ 放过：只豁免「时间上的历史快照」，不豁免「内容上的错误」。")
    if dead:
        rd.append("")
        rd.append("**失效内链（要修的，含自动建议路径，确认后可批量改）**：\n")
        rd.append("| 来源 md | 失效目标 | 建议修正为 |\n|---|---|---|")
        for d in dead[:30]:
            rd.append("| `%s` | `%s` | `%s` |" % (d["from"], d["target"], d.get("suggest", "—")))
        rd.append("")
    if ghosts:
        rd.append("**index.md 幽灵条目**：%s\n" % "、".join("`%s`" % g for g in ghosts[:20]))
    if gate_msgs:
        rd.append("## ⚠ 回归门禁 FAIL\n")
        for g in gate_msgs:
            rd.append("- %s" % g)
        rd.append("")
    rp = os.path.join(REPORT_DIR, "%s-selfcheck.md" % datetime.now().strftime("%Y-%m-%d"))
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(rd))
    print("\n周报：%s\nJSON ：%s" % (rp, JSON_OUT))
    conn_kb.close()
    conn_chk.close()
    return 1 if status == "FAIL" else 0


def main():
    ap = argparse.ArgumentParser(description="知识库每周自测")
    ap.add_argument("--no-update", action="store_true", help="只测不更新基线")
    ap.add_argument("--gate", type=float, default=3.0, help="Recall 掉点阈值（点）")
    ap.add_argument("--max-empty", type=float, default=5.0, help="空检率上限（%%）")
    ap.add_argument("--skip-links", action="store_true", help="跳过失效内链扫描")
    ap.add_argument("--fix-links", action="store_true",
                    help="自动修只有唯一候选的死链（原文件先备份到 state/linkfix_backup/）")
    ap.add_argument("--dry-run", action="store_true", help="配合 --fix-links，只预览不改")
    sys.exit(cmd_run(ap.parse_args()))


if __name__ == "__main__":
    main()
