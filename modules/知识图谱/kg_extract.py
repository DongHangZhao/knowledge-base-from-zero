#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kg_extract — 图谱实验的实体抽取 + 侧表构建（#41，**可整块删除**的实验代码）

定位（先读这段，别顺手做成图谱层）：
    本模块只为一件事存在 —— 用 1pt 预算回答「加一层 1-hop 邻居召回，R@5 能不能 +3pp
    且 p95 延迟不超过 +150ms」。**不做图谱、不做推理、不做可视化**。
    结论出来后：通过 → 下期做插件层；不通过 → 删 `kg_*.py` + `_test_kg.py` +
    `kb_rag.py` 那几行 + `DROP TABLE chunk_entities`，一行不留。

🔴 三个"零"（默认状态下对主链路零影响）：
  1. **零加载**：`kb_rag` 里是**函数内** import 本模块，`KB_KG != 1` 时连 import 都不发生。
  2. **零建表**：`build_sidecar` 在 `KB_KG != 1` 时直接 return `{"status":"disabled"}`，
     **连 `chunk_entities` 都不会创建**（有测试钉死）。
  3. **零删除**：侧表只 `INSERT OR IGNORE`；重建要显式 `--rebuild`（且必须 KB_KG=1）。

实体口径（规则抽取，不引模型）：
    《…》标题后缀 ／「…」引号 ／ 反引号标识符 `foo()` ／ #tag ／ 英文 CapWords ／
    `alias_table.json` 的键（通用技术别名）。宁缺毋滥：抽不到就是抽不到，
    不用模糊匹配硬凑（假实体会把 1-hop 邻居带偏，实验结论就废了）。

用法：
    KB_KG=1 python kg_extract.py --dry-run          # 只报能抽多少，不写库
    KB_KG=1 python kg_extract.py --limit 100        # 只处理前 100 个未建侧表的 chunk
    KB_KG=1 python kg_extract.py --rebuild          # 清空侧表重建（显式操作）

作者：FallenPetal ｜ 许可：KBL-1.0（非商用，商用见 COMMERCIAL.md）
"""

import argparse
import json
import os
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))

#: 侧表名（带前缀，便于一眼看出是实验产物、可整表 DROP）
TABLE = "chunk_entities"
INDEX = "idx_ent"

# 《标题》：中文书名号里的内容最常见的"文档/方案级实体"
_RE_TITLE = re.compile(r"《([^》]{2,40})》")
# 「引号」：作者强调的概念
_RE_QUOTE = re.compile(r"「([^」]{2,40})」")
# 反引号标识符：`foo()` / `foo.bar` / `foo`
_RE_CODE = re.compile(r"`([^`\n]{2,60})`")
# #tag：只认中英文数字下划线，长度 2..30（避免把 #123 当标签）
_RE_TAG = re.compile(r"#([A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff-]{1,29})")
# 英文 CapWords：至少两个大写开头的词，或一个 >=4 字母的全大写缩写（RRF/SQLite/FTS5）
_RE_CAPWORD = re.compile(r"\b([A-Z][a-z]{2,}(?:[A-Z][a-z]+)*|[A-Z]{2,}[0-9]{0,2})\b")
# 实体至少含一个字母或汉字（过滤 `400` 这类纯数字噪声）
_RE_HAS_LETTER = re.compile(r"[A-Za-z\u4e00-\u9fff]")
# 代码实体必须是「标识符形状」：`foo` / `foo()` / `foo.bar` / `x_y` / `常量名`。
# 目的是丢掉反引号里夹着的公式碎片：`1/(k+rank)`、`2.0/1.5/0.5` 这类会命中每个
# 提到数字的块，造出大量假 1-hop 边（实测侧表里 143 条实体有 3 条是这种噪声）。
_RE_IDENT = re.compile(r"^[A-Za-z_\u4e00-\u9fff][\w.\-]{0,58}(\(\))?$")

_ALIAS_KEYS = None


def alias_keys():
    """`alias_table.json` 的键（通用技术别名）。文件缺失/损坏 → 空集合，不抛。"""
    global _ALIAS_KEYS
    if _ALIAS_KEYS is not None:
        return _ALIAS_KEYS
    path = os.path.join(HERE, "alias_table.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _ALIAS_KEYS = {str(k).strip().lower() for k in data if not str(k).startswith("_")}
    except Exception:
        _ALIAS_KEYS = set()
    return _ALIAS_KEYS


def extract_entities(text):
    """规则抽实体，返回 `[(entity, ent_type), ...]`（保序、去重、最多 200 条）。

    ent_type ∈ `title|quote|code|tag|capword|alias`。
    """
    s = text or ""
    out, seen = [], set()

    def push(name, typ):
        n = (name or "").strip()
        if len(n) < 2 or len(n) > 60:
            return
        # 🔴 纯数字/纯符号的"代码实体"是噪声（实测 `400` `1/(k+rank)` 这类会命中每个
        #    提到数字的块，造出大量假 1-hop 边）。要求至少含一个字母或汉字。
        if not _RE_HAS_LETTER.search(n):
            return
        # 代码实体还要是标识符形状（挡住反引号里的公式碎片）
        if typ == "code" and not _RE_IDENT.match(n):
            return
        key = (n, typ)
        if key in seen:
            return
        seen.add(key)
        out.append(key)

    for m in _RE_TITLE.finditer(s):
        push(m.group(1), "title")
    for m in _RE_QUOTE.finditer(s):
        push(m.group(1), "quote")
    for m in _RE_CODE.finditer(s):
        push(m.group(1), "code")
    for m in _RE_TAG.finditer(s):
        push(m.group(1), "tag")
    for m in _RE_CAPWORD.finditer(s):
        push(m.group(1), "capword")
    keys = alias_keys()
    if keys:
        low = s.lower()
        for k in sorted(keys):
            if k and k in low:
                push(k, "alias")
    return out[:200]


def ensure_sidecar(conn):
    """建侧表 + 索引（幂等）。**只在 KB_KG=1 的路径上被调用**。

    WITHOUT ROWID：这张表是纯映射（(chunk_id, entity) 即主键），没有自增 id 需求，
    省一次 rowid 查找；数据量 = chunk 数 × 每篇实体数，实测可控。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS %s("
        " chunk_id INTEGER NOT NULL, entity TEXT NOT NULL, ent_type TEXT,"
        " PRIMARY KEY(chunk_id, entity)) WITHOUT ROWID" % TABLE)
    conn.execute("CREATE INDEX IF NOT EXISTS %s ON %s(entity)" % (INDEX, TABLE))
    try:
        conn.commit()
    except sqlite3.Error:
        pass


def _sidecar_exists(conn):
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    except sqlite3.Error:
        return False
    return bool(row)


def build_sidecar(conn, limit=0, dry_run=False):
    """给「侧表里还没有的 chunk」抽实体并写侧表（幂等：只处理未建的 + INSERT OR IGNORE）。

    Returns:
        dict：`{status: ok|disabled, chunks, entities, inserted, dry_run}`
              `status=="disabled"` 时**连表都不会建**（KB_KG != 1）。
    """
    if os.environ.get("KB_KG") != "1":
        return {"status": "disabled", "chunks": 0, "entities": 0, "inserted": 0,
                "dry_run": bool(dry_run),
                "reason": "KB_KG != 1（图谱实验默认关闭，且不建任何表）"}

    todo_sql = ("SELECT id, content FROM chunks WHERE id NOT IN "
                "(SELECT chunk_id FROM %s) ORDER BY id" % TABLE)
    if _sidecar_exists(conn):
        rows = conn.execute(todo_sql).fetchall()
    elif dry_run:
        # dry-run 走只读连接，**不能建表**（SQLite 会报 attempt to write a readonly database）；
        # 表不存在 = 一个都没建过 ⇒ 全量都是待处理
        rows = conn.execute("SELECT id, content FROM chunks ORDER BY id").fetchall()
    else:
        ensure_sidecar(conn)     # 首次：先建表，否则下面的 NOT IN 子查询会 no such table
        rows = conn.execute(todo_sql).fetchall()
    if limit and limit > 0:
        rows = rows[:limit]

    n_ent, n_ins = 0, 0
    for cid, content in rows:
        ents = extract_entities(content)
        n_ent += len(ents)
        if dry_run or not ents:
            continue
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO %s(chunk_id, entity, ent_type) VALUES(?,?,?)" % TABLE,
            [(cid, e, t) for e, t in ents])
        n_ins += conn.total_changes - before
    if not dry_run:
        conn.commit()
    return {"status": "ok", "chunks": len(rows), "entities": n_ent,
            "inserted": n_ins, "dry_run": bool(dry_run)}


def rebuild(conn):
    """清空侧表重建（**仅显式 --rebuild**；实验代码里唯一允许 DELETE 的地方）。"""
    if os.environ.get("KB_KG") != "1":
        return {"status": "disabled"}
    if _sidecar_exists(conn):
        conn.execute("DELETE FROM %s" % TABLE)
        conn.commit()
    return build_sidecar(conn)


def main(argv=None):
    ap = argparse.ArgumentParser(description="图谱实验：实体抽取 + 侧表构建（KB_KG=1 才生效）")
    ap.add_argument("--dry-run", action="store_true", help="只报能抽多少，不写库")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个未建侧表的 chunk")
    ap.add_argument("--rebuild", action="store_true", help="清空侧表重建（显式操作）")
    args = ap.parse_args(argv)

    sys.path.insert(0, HERE)
    import kb_index  # 懒 import：本模块顶层保持零依赖

    conn = kb_index.connect(readonly=bool(args.dry_run))
    try:
        if os.environ.get("KB_KG") != "1":
            print("[disabled] KB_KG != 1：图谱实验关闭（不建表、不写库）")
            return 0
        st = rebuild(conn) if args.rebuild else build_sidecar(conn, limit=args.limit,
                                                             dry_run=args.dry_run)
        print("[kg] status=%s chunks=%d 抽到实体=%d 写入=%d%s"
              % (st["status"], st["chunks"], st["entities"], st["inserted"],
                 "（dry-run，未写库）" if st.get("dry_run") else ""))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
