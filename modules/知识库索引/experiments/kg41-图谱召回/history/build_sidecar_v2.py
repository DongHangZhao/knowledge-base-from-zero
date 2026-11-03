#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_sidecar_v2 — #41 图谱实验的实体侧表构建（沙盘专用，不入仓库）

为什么另写一份而不是直接改 kg_extract：
    kg_extract 的规则抽取是为「技术文档的标识符」设计的（`反引号` / CapWords /
    #tag / 《》。实测在生产库抽 800 chunk：1686 个实体里中文只占 7.8%，且
    「把手上的事做完，就是赢。」「族谱上钉」这种整句碎词被当实体；英文标识符占 92%。
    这套东西跑 1-hop 实验必然得出「图谱无效」—— 但无效的是抽取器，不是图谱。

本脚本只做一件事：证明「抽出像样的中文实体后，1-hop 到底有没有信号」。

改进点（相对 kg_extract）：
  1. **补 jieba 词性路**：n / nz / ns / nt / nl / eng / vn / an / j，长度 >= 2。
     实测中文实体占比 7.8% -> 50.6%。
  2. **文档频次过滤**（两遍中的第一遍）：df > MAX_DF_RATIO × 文档数的实体剔除。
     「系统」「管理」「技术」这类泛词会命中几乎每个 chunk，1-hop 就退化成全库召回，
     等于没做实验。这一步是让实体有区分度的关键。
  3. **中英停用词**：jieba 会把 in / the / to / and / of 标成 eng，必须挡掉。

跑法（必须在沙盘库上）：
    python build_sidecar_v2.py --db state/_kg_sandbox.db --workers 16

作者：FallenPetal ｜ 用途：一次性实验，结论出来后与 kg_*.py 一起删
"""

import argparse
import os
import re
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

TABLE = "chunk_entities"
INDEX = "idx_ent"

# 规则路（与 kg_extract 一致，保留：这些符号标记的实体质量最高）
_RE_TITLE = re.compile(r"《([^》]{2,40})》")
_RE_QUOTE = re.compile(r"「([^」]{2,40})」")
_RE_CODE = re.compile(r"`([^`\n]{2,60})`")
_RE_TAG = re.compile(r"(?<![\w#])#([A-Za-z][\w\-]{1,30})")
_RE_CAP = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:[A-Z][a-zA-Z0-9]*)+)\b")

# jieba 词性白名单
KEEP_POS = {"n", "nz", "ns", "nt", "nl", "eng", "vn", "an", "j"}

# 英文停用词（jieba 会把它们标成 eng）
STOP_EN = set("""in the to and of for is are was were on at by with from as an or if it
this that be not you we they can will has have had but so no do does did all any our your
their its which who what when where how why than then there here about into over under
more most some such only also just very well may might must shall should would could
jpg jpeg png gif pdf md html http https www com cn org net php py js json yml yaml txt
csv xml url uri api sdk ui ux ok ipc cpu gpu ram ssd hdd tmp temp log logs src dst
""".split())

# 中文泛词（靠 df 过滤主要挡，这里是兜底）
STOP_CN = set("""系统 问题 方法 方式 内容 时间 文件 数据 情况 时候 部分 东西 事情 地方
意思 建议 说明 记录 目录 结果 原因 需求 功能 项目 文档 用户 使用 需要 一个 这个 那个
什么 怎么 因为 所以 如果 但是 已经 还是 就是 没有 不是 这样 那样 我们 他们 自己 现在
之前 之后 同时 另外 此外 因此 然后 而且 虽然 由于 通过 进行 实现 提供 支持 包括 以及
方面 过程 状态 类型 名称 标题 链接 地址 路径 版本 数量 大小 长度 单位 工具 平台 界面
""".split())

_CN = re.compile(r"[\u4e00-\u9fff]")
_DIGITISH = re.compile(r"^[\d\.\-%/]+$")


def extract_rules(text):
    """规则路：符号标记的实体（质量最高，原样保留）。"""
    out = set()
    for m in _RE_TITLE.findall(text):
        out.add((m.strip(), "title"))
    for m in _RE_QUOTE.findall(text):
        out.add((m.strip(), "quote"))
    for m in _RE_CODE.findall(text):
        m = m.strip()
        # 标识符形状才要（挡 `1/(k+rank)` 这类公式碎片）
        if re.fullmatch(r"[A-Za-z_][\w\.\-\(\)]*", m):
            out.add((m, "code"))
    for m in _RE_TAG.findall(text):
        out.add((m, "tag"))
    for m in _RE_CAP.findall(text):
        if len(m) <= 40:
            out.add((m, "ident"))
    return out


def extract_jieba(text, jieba_mod):
    """jieba 分词路（**不用词性标注**）。

    🔴 为什么不用 `pseg.cut`（2026-11-03 实测）：带词性标注比纯分词慢一个数量级 ——
    1000 chunk 4.7s vs 10000 chunk 9.4s（pseg 全库实测跑 28 分钟还没完，cut 只要 1.7 分钟）。
    pseg 能筛出纯名词、精度更好，但代价是实验跑不完；而 df 过滤 + 停用词已经能压住
    绝大部分噪声（动词/形容词通常也是高频词，会被 df 上限剔掉）。
    **宁可要一个能跑完的粗侧表，也不要一个跑不完的精细侧表。**
    """
    out = set()
    for w in jieba_mod.cut(text):
        w = w.strip()
        if len(w) < 2:
            continue
        if w in STOP_CN or w.lower() in STOP_EN:
            continue
        if _DIGITISH.match(w) or w.isdigit():
            continue
        # 必须含中文或字母（挡纯符号/标点碎片）
        if not _CN.search(w) and not re.search(r"[A-Za-z]", w):
            continue
        # 「的了是在和与及或对从把被让给」打头的碎词丢掉
        if w[0] in "的了是在和与及或对从把被让给":
            continue
        out.add((w, "noun" if _CN.search(w) else "ident"))
    return out


_W = {}


def _init_worker():
    """每个子进程各自初始化 jieba（fork 后不能共享 tokenizer 状态）。"""
    import jieba
    jieba.setLogLevel(20)
    _W["jieba"] = jieba


def _work(row):
    cid, content = row
    text = content or ""
    ents = extract_rules(text) | extract_jieba(text, _W["jieba"])
    return cid, [(e, t) for e, t in ents]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="沙盘库路径（**不要指向生产库**）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-df-ratio", type=float, default=0.05,
                    help="实体出现文档数超过「总文档数 × 此比例」即剔除（默认 5%%）")
    ap.add_argument("--min-df", type=int, default=2,
                    help="实体至少出现在几个不同文档里才保留（默认 2；1 的噪声太大）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个 chunk（0=全部，调试用）")
    args = ap.parse_args(argv)

    db = os.path.abspath(args.db)
    if "kb.db" == os.path.basename(db):
        print("[ERR] 拒绝在生产库上建实验侧表：%s" % db, file=sys.stderr)
        return 2

    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE IF NOT EXISTS %s(
        chunk_id INTEGER NOT NULL,
        entity   TEXT    NOT NULL,
        ent_type TEXT    NOT NULL,
        PRIMARY KEY(chunk_id, entity)) WITHOUT ROWID""" % TABLE)
    conn.execute("CREATE INDEX IF NOT EXISTS %s ON %s(entity)" % (INDEX, TABLE))
    conn.commit()

    q = "SELECT id, content FROM chunks"
    if args.limit:
        q += " LIMIT %d" % args.limit
    rows = conn.execute(q).fetchall()
    n_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    print("[kg2] chunk=%d docs=%d workers=%d" % (len(rows), n_docs, args.workers))

    t0 = time.time()
    pairs = []                                   # (chunk_id, [(ent,type)])
    if args.workers <= 1:
        # 🔴 单进程路径（默认该走这条）。Windows 上 Pool 实测会卡死 —— 16 进程跑
        # 19 分钟没出结果，而单进程实测全库只要 8.5 分钟（5000 条 22.8s）。
        # Windows 用 spawn 启动子进程，每个 worker 重新 import jieba 并抢同一份
        # 词典文件，加上 imap 结果的序列化/反序列化，开销远大于收益。别再开 Pool。
        import jieba
        jieba.setLogLevel(20)
        _W["jieba"] = jieba                       # _work 里从 _W 取，别忘了赋值
        for i, row in enumerate(rows):
            pairs.append(_work(row))
            if (i + 1) % 20000 == 0:
                el = time.time() - t0
                print("[kg2] %d/%d  %.0fs  预计剩余 %.0fs"
                      % (i + 1, len(rows), el, el / (i + 1) * (len(rows) - i - 1)))
    else:
        from multiprocessing import Pool
        with Pool(args.workers, initializer=_init_worker) as pool:
            for i, (cid, ents) in enumerate(pool.imap_unordered(_work, rows, chunksize=200)):
                pairs.append((cid, ents))
                if (i + 1) % 20000 == 0:
                    el = time.time() - t0
                    print("[kg2] %d/%d  %.0fs  预计剩余 %.0fs"
                          % (i + 1, len(rows), el, el / (i + 1) * (len(rows) - i - 1)))
    print("[kg2] 抽取完成 %.1fs，开始算 df" % (time.time() - t0))

    # 第一遍统计：实体 → 出现过的文档数（用 chunk_id→doc_id 映射）
    cid2doc = dict(conn.execute("SELECT id, doc_id FROM chunks"))
    df = {}
    for cid, ents in pairs:
        did = cid2doc.get(cid)
        for e, _t in ents:
            df.setdefault(e, set()).add(did)

    max_df = max(1, int(n_docs * args.max_df_ratio))
    keep = {e for e, s in df.items() if args.min_df <= len(s) <= max_df}
    dropped_hi = sum(1 for e, s in df.items() if len(s) > max_df)
    dropped_lo = sum(1 for e, s in df.items() if len(s) < args.min_df)
    print("[kg2] 实体 %d → 保留 %d ｜ 剔除高频 %d（df>%d）、低频 %d（df<%d）"
          % (len(df), len(keep), dropped_hi, max_df, dropped_lo, args.min_df))

    # 写侧表
    payload = []
    for cid, ents in pairs:
        for e, t in ents:
            if e in keep:
                payload.append((cid, e, t))
    conn.executemany("INSERT OR IGNORE INTO %s(chunk_id,entity,ent_type) VALUES(?,?,?)"
                     % TABLE, payload)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM %s" % TABLE).fetchone()[0]
    cn = conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT entity FROM %s WHERE entity GLOB '*[一-龥]*')" % TABLE).fetchone()[0]
    allents = conn.execute("SELECT COUNT(DISTINCT entity) FROM %s" % TABLE).fetchone()[0]
    print("[kg2] 侧表写入 %d 条 ｜ 不同实体 %d（含中文 %d，%.1f%%）｜ 耗时 %.1fs"
          % (n, allents, cn, 100.0 * cn / max(1, allents), time.time() - t0))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
