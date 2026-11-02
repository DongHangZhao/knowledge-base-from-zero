# -*- coding: utf-8 -*-
"""T3 元数据过滤回归测试：--since / --path-prefix 边界 + rerank 在过滤后运行。

做法（与 _test_rotate.py 同套路：临时目录 + monkeypatch，不碰生产库）：
  1. 临时 KB_ROOT + 临时 DB（KB_DB_PATH 环境变量，import 前设好）
  2. 造 7 篇文档覆盖全部回填分支：
       wiki/技术 a/b/c/g（stat 分支；c 的 mtime 恰为 2026-10-01 00:00 边界值）
       wiki/产品 d        （前缀过滤应排除）
       raw/项目 e         （ingest state 优先于磁盘 mtime——磁盘故意设为 2020）
       raw/隐私 f         （隐私分支：文件在盘上也不 stat，mtime 走 indexed_at）
       wiki/技术 g 缺失文件（OSError 分支：indexed_at 兜底）
  3. 跑真实 kb_index.backfill_chunk_meta + kb_rag.search_unified，断言：
       回填全非 NULL 且各分支取值正确 / 前缀含部分匹配与空结果 /
       since 边界（恰为当天 00:00 应被包含）/ 组合过滤 /
       🔴 rerank 只在过滤后的集合上运行（spy 记录收到的池 + spy 排序决定最终顺序）

改 kb_index.backfill_chunk_meta / kb_rag.search_unified 的过滤逻辑后必跑。
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

TMP = tempfile.mkdtemp(prefix="kbmeta_")
TMP_ROOT = os.path.join(TMP, "kbroot")           # 假 KB_ROOT（backfill 的 stat 落这里）
TMP_DB = os.path.join(TMP, "state", "kb.db")
os.environ["KB_DB_PATH"] = TMP_DB                # 必须在 import kb_index 之前设
os.makedirs(os.path.dirname(TMP_DB), exist_ok=True)
sys.path.insert(0, HERE)

import kb_index        # noqa: E402
import kb_rag          # noqa: E402

QUERY = "知识库检索测试"        # 6 字 > 4：避开「短查询跳过 rerank」保护，精排必跑
fails = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        fails.append(name)


def E(s):
    """ISO  naive 本地时间 → epoch 秒（与回填/过滤同一时钟语义）。"""
    return int(datetime.datetime.fromisoformat(s).timestamp())


# 文档矩阵：(path, category, 磁盘 mtime 或 None=不建文件, state mtime ISO 或 None)
DOCS = [
    ("wiki/技术/a.md",  "wiki", "2026-10-05T12:00:00", None),
    ("wiki/技术/b.md",  "wiki", "2026-09-01T12:00:00", None),   # 旧文档：since 应排除
    ("wiki/技术/c.md",  "wiki", "2026-10-01T00:00:00", None),   # 边界：恰为当天 00:00
    ("wiki/产品/d.md",  "wiki", "2026-10-15T12:00:00", None),   # 前缀 wiki/技术 应排除
    ("raw/项目/e.md",   "raw",  "2020-01-01T00:00:00", "2026-10-20T10:00:00"),  # state 优先
    ("raw/隐私/f.md",   "raw",  "2020-01-01T00:00:00", None),   # 隐私：盘上也不 stat
    ("wiki/技术/g.md",  "wiki", None,                 None),   # 文件丢失：indexed_at 兜底
]
ORIG_ROOT = kb_index.KB_ROOT
ORIG_RERANK = kb_rag.bge_reranker.rerank
SEEN_POOLS = []          # spy 记录：rerank 每次收到的融合池（path 列表）

try:
    kb_index.KB_ROOT = TMP_ROOT
    conn = kb_index.connect()
    kb_index.ensure_schema(conn)
    for path, cat, disk_mtime, state_mtime in DOCS:
        body = "知识库检索测试——%s。正文重复 知识库、检索、测试 关键字，" \
               "保证 FTS5 / trigram / 三链路都能召回这篇文档。" % path
        kb_index.upsert_doc(conn, {"category": cat, "path": path,
                                   "title": os.path.splitext(os.path.basename(path))[0] + "标题",
                                   "summary": body[:120], "content": body,
                                   "hash": kb_index.sha1_of(body)})
        if disk_mtime:
            fp = os.path.join(TMP_ROOT, path.replace("/", os.sep))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(body)
            os.utime(fp, (E(disk_mtime), E(disk_mtime)))
    # ingest_raw_state：e 篇登记 2026-10-20（与磁盘 2020 故意不同，验证 state 优先）
    conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('ingest_raw_state',?)",
                 (json.dumps({"raw/项目/e.md": [1234, "2026-10-20T10:00:00"]},
                             ensure_ascii=False),))
    conn.commit()
    kb_rag.ensure_trigram(conn)      # substr 字级路要 chunks_trigram（与 build 同动作）
    conn.commit()

    print("=== 1. 回填：backfill_chunk_meta 全覆盖且分支取值正确 ===")
    st = kb_index.backfill_chunk_meta(conn)
    print("  统计：%s" % st)
    n_null = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE path IS NULL OR title IS NULL OR mtime IS NULL"
    ).fetchone()[0]
    check("回填后 chunks.path/title/mtime 无 NULL", n_null == 0, "剩余 %d 行" % n_null)
    check("回填统计自洽", st["chunks_null_left"] == 0 and st["orphan_chunks"] == 0)

    def doc_mtime(path):
        return conn.execute("SELECT mtime FROM docs WHERE path=?", (path,)).fetchone()[0]

    def chunk_row(path):
        return conn.execute(
            "SELECT c.path, c.title, c.mtime FROM chunks c "
            "WHERE c.doc_id=(SELECT id FROM docs WHERE path=?) LIMIT 1", (path,)).fetchone()

    check("wiki 走磁盘 stat（a=2026-10-05 12:00）", doc_mtime("wiki/技术/a.md") == E("2026-10-05T12:00:00"))
    check("since 边界值原样落库（c=2026-10-01 00:00:00）", doc_mtime("wiki/技术/c.md") == E("2026-10-01T00:00:00"))
    check("raw 走 ingest state 而非磁盘 mtime（e=2026-10-20 10:00）",
          doc_mtime("raw/项目/e.md") == E("2026-10-20T10:00:00"))
    # 隐私分支：文件在盘上（mtime 2020）也绝不 stat，只能拿到 indexed_at
    f_idx = conn.execute("SELECT indexed_at FROM docs WHERE path='raw/隐私/f.md'").fetchone()[0]
    check("隐私路径不 stat 原件（f=indexed_at，非 2020）",
          doc_mtime("raw/隐私/f.md") == E(f_idx) and doc_mtime("raw/隐私/f.md") != E("2020-01-01T00:00:00"),
          "f mtime=%d" % doc_mtime("raw/隐私/f.md"))
    # 文件丢失分支：非隐私 wiki 文档 stat 失败 → indexed_at
    g_idx = conn.execute("SELECT indexed_at FROM docs WHERE path='wiki/技术/g.md'").fetchone()[0]
    check("文件丢失走 indexed_at 兜底（g）", doc_mtime("wiki/技术/g.md") == E(g_idx))
    cp, ct, cm = chunk_row("wiki/技术/a.md")
    dp, dt = conn.execute("SELECT path,title FROM docs WHERE path='wiki/技术/a.md'").fetchone()
    check("chunks.path/title 与 docs 一致", (cp, ct) == (dp, dt) and cm == doc_mtime("wiki/技术/a.md"))
    check("幂等：重跑不报错且仍无 NULL", kb_index.backfill_chunk_meta(conn)["chunks_null_left"] == 0)

    # ---------- rerank spy：记录收到的池，并故意按「池倒序」打分 ----------
    def spy(q, items, topk=30):
        SEEN_POOLS.append([it.get("path") for it in items])
        rev = list(reversed(items))
        return [(it["doc_id"], 0.9 - 0.1 * i) for i, it in enumerate(rev)]

    kb_rag.bge_reranker.rerank = spy

    def run(**kw):
        return kb_rag.search_unified(conn, QUERY, topk=10, mode="fused", **kw)

    print("\n=== 2. path-prefix：前缀内全命中、前缀外全排除 ===")
    res, note = run(path_prefix="wiki/技术")
    paths = [r["path"] for r in res]
    print("  结果：%s" % paths)
    print("  note：%s" % note)
    check("wiki/技术 4 篇全返（含旧文档 b，无 since 时不过滤时间）",
          set(paths) == {"wiki/技术/a.md", "wiki/技术/b.md", "wiki/技术/c.md", "wiki/技术/g.md"})
    check("每条 path 都在 wiki/技术 下", all(p.startswith("wiki/技术") for p in paths))
    check("过滤提示进了 note", "元数据过滤" in note and "--path-prefix=wiki/技术" in note)
    check("🔴 rerank 收到的池全部合规（过滤后才精排）",
          SEEN_POOLS and all(p.startswith("wiki/技术") for p in SEEN_POOLS[-1]),
          "池=%s" % (SEEN_POOLS[-1] if SEEN_POOLS else None))
    check("🔴 rerank 在过滤后集合上跑且决定最终顺序（最终序=池倒序）",
          paths == list(reversed(SEEN_POOLS[-1])))
    check("rerank 字段透出", all(r.get("reranked") and r.get("rerank_score") is not None for r in res))

    print("\n=== 3. 部分前缀与空前结果 ===")
    res2, _ = run(path_prefix="wiki/技")     # 部分前缀：比 wiki/技术 少一层
    check("部分前缀 wiki/技 排除 wiki/产品",
          set(r["path"] for r in res2) == {"wiki/技术/a.md", "wiki/技术/b.md", "wiki/技术/c.md", "wiki/技术/g.md"})
    res3, _ = run(path_prefix="wiki")        # 更粗前缀：含产品
    check("粗前缀 wiki 含 wiki/产品/d.md",
          set(r["path"] for r in res3) == {"wiki/技术/a.md", "wiki/技术/b.md", "wiki/技术/c.md",
                                          "wiki/产品/d.md", "wiki/技术/g.md"})
    res4, note4 = run(path_prefix="wiki/技术x")   # 没有任何文档匹配
    check("空前缀语义：无匹配文档返空结果", res4 == [] and "融合池" in note4 and "→0" in note4,
          "note=%s" % note4)

    print("\n=== 4. since：边界包含、旧文档排除 ===")
    res5, note5 = run(since="2026-10-01")
    paths5 = [r["path"] for r in res5]
    print("  结果：%s" % paths5)
    ts = E("2026-10-01T00:00:00")
    bad = [p for p in paths5 if doc_mtime(p) < ts]
    check("每条结果 mtime 都 >= 2026-10-01 00:00", not bad, "违规：%s" % bad)
    check("边界文档 c（恰为当天 00:00）被包含", "wiki/技术/c.md" in paths5)
    check("旧文档 b（2026-09-01）被排除", "wiki/技术/b.md" not in paths5)
    check("新文档 e（raw，state 2026-10-20）被包含", "raw/项目/e.md" in paths5)
    check("since 提示进了 note", "--since=2026-10-01" in note5)

    print("\n=== 5. 组合过滤：path-prefix + since ===")
    res6, note6 = run(path_prefix="wiki/技术", since="2026-10-01")
    paths6 = [r["path"] for r in res6]
    print("  结果：%s" % paths6)
    check("组合后只剩 wiki/技术 下且够新的 3 篇",
          set(paths6) == {"wiki/技术/a.md", "wiki/技术/c.md", "wiki/技术/g.md"})
    check("🔴 组合过滤下 rerank 池仍全部合规",
          SEEN_POOLS and all(p.startswith("wiki/技术") for p in SEEN_POOLS[-1]))
    check("组合过滤 note 双条件都在", "--path-prefix=wiki/技术" in note6 and "--since=2026-10-01" in note6)

    print("\n=== 6. CLI 层：坏日期 fail-fast（rc=2）+ 端到端 JSON 过滤 ===")
    r = subprocess.run([PY, os.path.join(HERE, "kb_rag.py"), "search",
                        "--query", QUERY, "--since", "2026-13-99"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    check("--since 2026-13-99 用法错误 rc=2", r.returncode == 2,
          "rc=%d err=%s" % (r.returncode, (r.stderr or "").strip()[-80:]))
    check("报错信息指明 YYYY-MM-DD 格式", "YYYY-MM-DD" in (r.stderr or ""))
    r2 = subprocess.run([PY, os.path.join(HERE, "kb_rag.py"), "search",
                         "--query", QUERY, "--path-prefix", "wiki/技术", "--json", "--no-rerank"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=180,
                        env=dict(os.environ, KB_DB_PATH=TMP_DB))
    try:
        obj = json.loads(r2.stdout)
    except Exception:
        obj = {}
    cli_paths = [x["path"] for x in obj.get("results", [])]
    check("CLI --path-prefix --json 每条 path 都合规",
          bool(cli_paths) and all(p.startswith("wiki/技术") for p in cli_paths),
          "paths=%s" % cli_paths)
    check("CLI JSON 回显 filters", obj.get("filters", {}).get("path_prefix") == "wiki/技术")

    conn.close()
finally:
    kb_rag.bge_reranker.rerank = ORIG_RERANK
    kb_index.KB_ROOT = ORIG_ROOT
    shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 56)
if fails:
    print("❌ 失败：%s" % ", ".join(fails))
    sys.exit(1)
print("✅ T3 元数据过滤回归全过：回填三分支 / 前缀含部分匹配与空结果 / "
      "since 边界 / 组合 / rerank 在过滤后运行")
sys.exit(0)
