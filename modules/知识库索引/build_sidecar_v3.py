#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_sidecar_v3.py — 用领域词典建实体侧表（#41 第二版）

与 v1/v2 的区别：
    v1 `jieba.cut` 直抽        → 实体被切碎（集群/五大/系统），图谱实验假阴性
    v2 加词性 + df 过滤        → 中文占比上去了，但短语仍碎
    v3 **先建领域词典再分词**  → 「集群五大系统项目」整体保留

用法：
    # 只给评测集涉及的文档建（1pt 实验规模，几秒）
    python build_sidecar_v3.py --db <沙盘库> --scope eval

    # 全库建（要几分钟，用 --workers 1）
    python build_sidecar_v3.py --db <沙盘库> --scope all

    # 看质量报告但不写库
    python build_sidecar_v3.py --db <沙盘库> --scope eval --dry-run

产出表：
    chunk_entities(chunk_id, entity, ent_type)  主键 (chunk_id, entity)
    entity_stats(entity, df, ent_type)          实体文档频次（可视化要用）
    kg_meta(key, value)                         构建元信息（词典大小 / 时间 / 参数）

🔴 安全：拒绝在名为 kb.db 的生产库上执行（必须用沙盘副本）。
   生产库的实体表要由正式流程创建，不允许实验脚本写入。
"""

import argparse
import json
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--scope", choices=["eval", "all"], default="eval")
    ap.add_argument("--eval-dir", default=None,
                    help="评测集目录（默认取 --db 同目录）")
    ap.add_argument("--eval-sets", nargs="*", default=None,
                    help="指定评测集文件名；默认 kb_eval_*.json 全收")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lo", type=int, default=2, help="df 下限（默认 2）")
    ap.add_argument("--hi-ratio", type=float, default=0.35, help="df 上限比例")
    args = ap.parse_args(argv)

    db = os.path.abspath(args.db)
    if os.path.basename(db) == "kb.db":
        print("[ERR] 拒绝在生产库上建实验侧表，请用沙盘副本", file=sys.stderr)
        return 2
    if not os.path.exists(db):
        print("[ERR] 库不存在：%s" % db, file=sys.stderr)
        return 3

    import kg_terms

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA busy_timeout=600000")

    # ---- 1. 领域词典 ----
    t0 = time.time()
    dic = kg_terms.build_domain_dict(conn)
    n_inject = kg_terms.inject(dic)
    print("[v3] 领域词典 %d 条，注入 jieba %d 条（%.1fs）"
          % (len(dic), n_inject, time.time() - t0))

    # ---- 2. 选文档范围 ----
    targets = None
    if args.scope == "eval":
        S = args.eval_dir or os.path.dirname(db)
        names = args.eval_sets
        if not names:
            import glob
            names = [os.path.basename(p) for p in
                     glob.glob(os.path.join(S, "kb_eval*.json"))]
        targets = set()
        for nm in names:
            p = os.path.join(S, nm)
            if not os.path.exists(p):
                continue
            try:
                data = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            for it in (data if isinstance(data, list) else []):
                gts = it.get("gt")
                for g in (gts if isinstance(gts, list) else [gts]):
                    if g:
                        targets.add(g)
        print("[v3] 评测范围：%d 个 gt 路径" % len(targets))
        if not targets:
            print("[ERR] 没收集到 gt 路径，检查 --eval-dir", file=sys.stderr)
            return 4

    if targets is None:
        rows = conn.execute("SELECT id, doc_id, path, content FROM chunks").fetchall()
    else:
        tl = list(targets)
        rows = []
        for i in range(0, len(tl), 400):
            ch = tl[i:i + 400]
            ph = ",".join("?" * len(ch))
            rows.extend(conn.execute(
                "SELECT id, doc_id, path, content FROM chunks WHERE path IN (%s)"
                % ph, tuple(ch)).fetchall())
    n_docs = len({d for _c, d, _p, _t in rows})
    print("[v3] 命中 chunk %d 个 / 文档 %d 篇" % (len(rows), n_docs))
    if not rows:
        print("[ERR] 0 个 chunk 命中", file=sys.stderr)
        return 5

    # ---- 3. 抽实体 ----
    t0 = time.time()
    pairs = []
    for cid, did, _path, text in rows:
        pairs.append((cid, did, kg_terms.extract(text or "", dic)))
    el = time.time() - t0
    print("[v3] 抽取完成 %.1fs（%.0f chunk/s）" % (el, len(rows) / el if el else 0))

    # ---- 4. df 过滤 ----
    keep, df, hi = kg_terms.filter_by_df(pairs, n_docs, args.lo, args.hi_ratio)
    st = kg_terms.report_stats(pairs, keep, n_docs)
    print("[v3] 实体 %d → 保留 %d（df %d..%d）"
          % (st["unique_terms_raw"], len(keep), args.lo, hi))
    print("[v3] 质量：中文实体 %.1f%% ｜ 4 字以上短语 %.1f%% ｜ 词典命中唯一词 %d"
          % (st["chinese_ratio"] * 100, st["phrase4_ratio"] * 100, st["dict_hits"]))

    if args.dry_run:
        print("\n=== 抽样 12 个保留实体 ===")
        sample = sorted(keep, key=lambda t: -len(df[t]))[:12]
        for t in sample:
            print("  df=%-4d len=%-2d %s" % (len(df[t]), len(t), t))
        print("\n[dry-run] 未写库")
        conn.close()
        return 0

    # ---- 5. 写库 ----
    conn.execute("""CREATE TABLE IF NOT EXISTS chunk_entities(
        chunk_id INTEGER NOT NULL,
        entity   TEXT    NOT NULL,
        ent_type TEXT    NOT NULL,
        PRIMARY KEY(chunk_id, entity)) WITHOUT ROWID""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ce_entity ON chunk_entities(entity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ce_chunk ON chunk_entities(chunk_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS entity_stats(
        entity   TEXT NOT NULL PRIMARY KEY,
        df       INTEGER NOT NULL,
        ent_type TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS kg_meta(
        key TEXT NOT NULL PRIMARY KEY,
        value TEXT NOT NULL)""")
    conn.execute("DELETE FROM chunk_entities")
    conn.execute("DELETE FROM entity_stats")
    conn.execute("DELETE FROM kg_meta")

    payload = []
    tmap = {}
    for cid, _d, ents in pairs:
        for t, ty in ents:
            if t in keep:
                payload.append((cid, t, ty))
                tmap.setdefault(t, ty)
    conn.executemany(
        "INSERT OR IGNORE INTO chunk_entities(chunk_id,entity,ent_type) VALUES(?,?,?)",
        payload)
    conn.executemany(
        "INSERT OR REPLACE INTO entity_stats(entity,df,ent_type) VALUES(?,?,?)",
        [(t, len(df[t]), tmap.get(t, "noun")) for t in keep])
    meta = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "version": "v3-dict",
        "scope": args.scope,
        "dict_size": str(len(dic)),
        "docs": str(n_docs),
        "chunks": str(len(rows)),
        "df_lo": str(args.lo), "df_hi": str(hi),
        "chinese_ratio": "%.4f" % st["chinese_ratio"],
        "phrase4_ratio": "%.4f" % st["phrase4_ratio"],
        "entities": str(len(keep)),
        "mentions": str(len(payload)),
    }
    conn.executemany("INSERT INTO kg_meta(key,value) VALUES(?,?)", list(meta.items()))
    conn.commit()

    n_ce = conn.execute("SELECT COUNT(*) FROM chunk_entities").fetchone()[0]
    n_en = conn.execute("SELECT COUNT(*) FROM entity_stats").fetchone()[0]
    print("[v3] 已写入 chunk_entities %d 行 ｜ entity_stats %d 个实体" % (n_ce, n_en))

    # ---- 6. 关键短语验证（这是本版存在的理由）----
    print("\n=== 关键短语是否能整体抽出 ===")
    probes = ["集群五大系统", "集群五大系统项目", "全院统一预约平台",
              "智能血液透析信息系统", "可复用模块", "知识库索引"]
    for p in probes:
        hits = [t for t in keep if t == p]
        part = [t for t in keep if p in t and t != p][:3]
        print("  %-22s 整体保留=%s  包含它的更长词=%s"
              % (p, "✅" if hits else "❌", part if part else "—"))

    conn.close()
    print("\n[v3] 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
