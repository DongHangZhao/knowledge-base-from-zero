# -*- coding: utf-8 -*-
"""W34 问句库通道（doc2query 文档侧增强）· 生产版。

为什么单独成模块而不并进 kb_rag：
  · kb_rag.py 是命脉链路，问句库只是**可选增量通道**——独立文件便于一键关停、单独回归、
    出事时不用在 1861 行里找改动点；
  · 生产代码不得 import `_w23_work/`（实验目录），逻辑在此重写一份纯生产版。

数据：只读 `state/kb_questions.db`（FTS5 + bge-small-zh CLS 向量），**不写 kb.db、不碰 chunks**，
      因此不需要 build、不抢 build.lock，也不会与并发窗口打架。

行为约定：
  · `KB_QCH=off` → 整模块零加载、零开销，与未接入前**逐位一致**（回滚开关）；
  · 任何异常（库缺失 / 模型不可用 / FTS 报错 / jieba 缺失）→ 返回 []，**绝不阻塞主链路**；
  · 只做「文档侧问句召回」，不改写用户 query、不调外部模型。

实证（2026-11-20 W34，见 W34 卡 §八）：
  · 设计集 B/D/F 进池 51→70（+19.39pp，McNemar b=0，p=3.81e-6）
  · 独立集 71 条（人验 71/71 认同）top1 40→42、MRR@10 0.675→0.687（p=0.824，不显著但零误伤）
  · 延迟 +19ms（12 条中位 0.449→0.467s）
  · 🔴 已知边界：问句只覆盖非媒体 wiki（906 条 / 289 篇），媒体 OCR 页故意不生成
    （会产出「图里写了啥」这类无区分度问句）；覆盖不到的文档此通道不贡献分数。
"""
from __future__ import annotations

import os
import sqlite3
import time

import numpy as np

# RRF 常数与 kb_rag 保持一致（Cormack 2009，k=60）
RRF_K = 60
# 实验定档：topn 60 明显好于 30（全量库 906 条时 51→70 vs 51→66，原因见 W34 卡 §八）
DEFAULT_TOPN = 60
# 低于此余弦不参与（实验里 0.45/0.55 无差别，取 0.0 = 不设阈值，靠 RRF 排序自然淘汰）
DEFAULT_MIN_SIM = 0.0

_STOP = set("的 了 是 在 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 "
            "自己 这 那 什么 怎么 为什么 如何 哪些 哪个 多少 吗 呢 吧 啊".split())

_BANK = {"obj": None, "err": None, "tried_at": 0.0, "db": None}
_RETRY_SEC = 300.0   # 加载失败后 5 分钟内不重试，避免每条查询都撞一次


def enabled() -> bool:
    """总开关。默认开；设 KB_QCH=off/0/false/no 关闭。"""
    v = (os.environ.get("KB_QCH") or "on").strip().lower()
    return v not in ("off", "0", "false", "no", "disable", "disabled")


def topn() -> int:
    try:
        return max(1, int(os.environ.get("KB_QCH_TOPN") or DEFAULT_TOPN))
    except Exception:
        return DEFAULT_TOPN


def weight() -> float:
    try:
        return float(os.environ.get("KB_W_QCH") or 1.0)
    except Exception:
        return 1.0


def db_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.environ.get("KB_QCH_DB") or os.path.join(here, "state", "kb_questions.db")


def status() -> dict:
    """给 doctor / 交付闸门看的自述（不含隐私内容）。"""
    p = db_path()
    b = _BANK["obj"]
    if b is None and enabled():
        b = _get_bank()      # 首次 status 主动加载一次，否则条数永远显示 0（假绿）
    return {
        "enabled": enabled(),
        "db": p,
        "db_exists": os.path.exists(p),
        "questions": len(b.ids) if b else 0,
        "docs": len(b.docs) if b else 0,
        "topn": topn(),
        "weight": weight(),
        "err": _BANK["err"],
    }


class _Bank:
    """问句库内存索引：FTS5 走 jieba 分词 OR 匹配，向量走 bge-small-zh 余弦。"""

    def __init__(self, path: str):
        self.conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=15)
        self.conn.row_factory = None
        ids, paths, vecs = [], [], []
        # LEFT JOIN：规则法问句库没有向量，但它同样要能走 FTS（旧写法 INNER JOIN 会把整库读成 0 条）
        for qid, doc_path, blob, dim in self.conn.execute(
                "SELECT q.id, q.doc_path, v.vec, v.dim FROM questions q "
                "LEFT JOIN qvec v ON v.qid=q.id"):
            if blob is None:
                continue
            ids.append(qid)
            paths.append(doc_path)
            vecs.append(np.frombuffer(blob, dtype="float32") if blob else np.zeros(dim, dtype="float32"))
        self.ids = ids
        self.paths = paths
        self.mat = np.vstack(vecs) if vecs else np.zeros((0, 512), dtype="float32")
        self.docs = {r[0] for r in self.conn.execute("SELECT DISTINCT doc_path FROM questions")}

    def fts(self, query: str, limit: int):
        try:
            import jieba
        except Exception:
            return []
        toks = [t.strip().lower() for t in jieba.lcut(query or "")]
        toks = [t for t in toks if t and t not in _STOP and (len(t) >= 2 or t.isdigit())]
        if not toks:
            return []
        expr = " OR ".join('"%s"' % t.replace('"', "") for t in toks[:12])
        try:
            rows = self.conn.execute(
                "SELECT q.id, q.doc_path FROM qfts JOIN questions q ON q.id=qfts.rowid "
                "WHERE qfts MATCH ? ORDER BY bm25(qfts) LIMIT ?", (expr, limit)).fetchall()
        except sqlite3.Error:
            return []
        return [r[1] for r in rows]

    def vec(self, query: str, limit: int, min_sim: float):
        if self.mat.size == 0:
            return []
        try:
            import bge_encoder
            qv = bge_encoder.encode([query], batch=1, is_query=True)[0]
        except Exception:
            return []
        sims = self.mat @ qv
        idx = np.argsort(-sims)[:limit]
        return [self.paths[i] for i in idx if sims[i] >= min_sim]


def _rrf(lists, k=RRF_K):
    score = {}
    for lst in lists:
        for i, p in enumerate(lst, 1):
            score[p] = score.get(p, 0.0) + 1.0 / (k + i)
    return sorted(score.items(), key=lambda t: -t[1])


def _dedup(paths):
    seen, out = set(), []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _get_bank():
    """懒加载单例；失败缓存 5 分钟，避免每条查询都重试。"""
    if not enabled():
        return None
    now = time.time()
    if _BANK["obj"] is not None:
        return _BANK["obj"]
    if _BANK["err"] and now - _BANK["tried_at"] < _RETRY_SEC:
        return None
    p = db_path()
    _BANK["tried_at"] = now
    if not os.path.exists(p):
        _BANK["err"] = "问句库不存在: %s" % p
        return None
    try:
        _BANK["obj"] = _Bank(p)
        _BANK["err"] = None
        _BANK["db"] = p
        return _BANK["obj"]
    except Exception as e:
        _BANK["err"] = "%s: %s" % (type(e).__name__, str(e)[:80])
        return None


def search(query: str, topn_n: int | None = None, min_sim: float = DEFAULT_MIN_SIM):
    """返回按 RRF 排序的 doc_path 列表（已去重、已截断）。异常一律返回 []。

    为什么 FTS 与向量两路都要：FTS 抓字面重合（问句与查询共享实词），
    向量抓语义（问句是「用户会怎么问」，与文档标题措辞不同）——单独任一路都比两路融合差。
    """
    if not enabled() or not (query or "").strip():
        return []
    bank = _get_bank()
    if bank is None:
        return []
    n = topn_n or topn()
    try:
        a = _dedup(bank.fts(query, n))
        b = _dedup(bank.vec(query, n, min_sim))
        if not a and not b:
            return []
        fused = _rrf([x for x in (a, b) if x])
        return [p for p, _ in fused[:n]]
    except Exception:
        return []


if __name__ == "__main__":  # 自检：python qch_channel.py "查什么"
    import json
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "缓存失效怎么办"
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    for i, p in enumerate(search(q, topn_n=10), 1):
        print("%2d. %s" % (i, p))
