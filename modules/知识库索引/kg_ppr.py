#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kg_ppr.py — PPR 图扩展检索（HippoRAG 思路的本地实现）

# 为什么要换掉 1-hop

v3 把实体抽取修对了（中文实体 70.8%、关键短语 5/6 保留），
但 1-hop 共现扩展**仍然负收益，且权重越高越差**
（0.5 → −6.7pp，1.0 → −6.7pp，1.5 → −10pp，2.5 → −20pp）。

单调恶化排除了「排序不够靠前」，说明扩进来的内容本身是错的。
根因：**共现 ≠ 相关**。以枢纽实体为种子做 2 跳，覆盖 1429/4867 个实体（29%）——
它不是「跳到答案附近」，是「跳到整个知识库」。

# 业界怎么解（本文的实现依据）

HippoRAG（NeurIPS 2024, Gutiérrez et al.）用三步解决同一问题，
2WikiMultiHopQA 上 R@5 89.5（比 ColBERTv2 高 21.3）：

1. **PPR 替代 BFS**
   以查询实体为种子跑 Personalized PageRank，概率沿边扩散。
   关键差别：PPR 的分数是**归一化**的 —— 一个节点的出边再多，
   流过去的总概率也固定（alpha 决定留在种子 vs 往外走的比例）。
   BFS 是「走到就全收」，PPR 是「按结构分配」。
   这正是「权重越高越差」的解药：不是给扩展结果加权，而是让扩散自己衰减。

2. **Node Specificity（节点特异性）**
   种子权重乘以 `1/df` 一类的高频惩罚，防止「系统」「项目」这种泛词主导扩散。
   论文原话：模拟大脑依赖局部信号而非全局 IDF 计算。
   这条把「枢纽降权」从我的"待试优化项"变成**核心组件**。

3. **实体-文档矩阵**
   PPR 输出的是**实体分数**，再乘「实体在文档中出现的次数」得到文档分。
   我原来的 1-hop 是直接把邻居 chunk 塞进候选池 —— 那等于把图的中间产物
   当终产物用，噪声全留在里面。

# 与本项目检索层的接口

    search_ppr(conn, query, topk) -> [(chunk_id, path, score)]

不直接替换 `search_unified`，而是作为**额外一路**参与 RRF 融合 ——
`kb_rag` 里已经有 kg 路的钩子（`KB_W_KG`），把这里接上去即可。
这样出问题时可以一键关掉（`KB_KG` 未设 = 零加载）。

# 参考

- HippoRAG 论文 / 项目页：https://github.com/OSU-NLP-Group/HippoRAG
- 论文结论边界（很重要的反面提醒）：MuSiQue 上提升较小，HotpotQA 上**没有全面胜出**。
  原文意思：图方法是否有优势，取决于任务是否真的需要跨文档关联。
  所以本模块只在「多跳集」上宣称收益，不宣称对普通单跳查询也有效。
"""

import math
import os
import sqlite3
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_ALPHA = 0.85          # 阻尼系数（留在当前节点的概率 1-alpha）
DEFAULT_SPEC_POWER = 1.0      # 节点特异性指数：1.0 = 1/df 线性降权


# ---------------------------------------------------------------- 建图
def build_graph(conn, min_df=2, min_w=2, idf_weight=True, n_docs=None,
                max_entities=None, weighting="npmi", max_df_ratio=0.35,
                max_deg_df_ratio=25.0):
    """从 chunk_entities 建实体共现图。

    返回 (nx.Graph, stats)

    **边权用什么很关键**（这是本轮最大的一个发现）：

      · 原始共现次数 —— 15684 个实体能连出 311 万条边、平均度数 397。
        图密到这个程度，PPR 一扩散就被摊平成一片 0.002，等于没有区分度。
        实测「某集群五大系统」种子 0.15，其余全 ≤0.003，结果召回一堆泛文档。
      · **NPMI（归一化点互信息）** —— 高频词的共现会被自身高频惩罚：
            pmi(a,b) = log( p(a,b) / (p(a)·p(b)) )
            npmi     = pmi / -log p(a,b)    ∈ [-1, 1]
        两个泛词（「系统」「项目」）虽然到处一起出现，p(a)、p(b) 都很大，
        pmi 接近 0 甚至负 → 边几乎被抹平；而「血透 + 透析机」这种
        只在少数块里同现的强关联，pmi 很高 → 边被保留。

    这就是 LightRAG 说的「低层实体 vs 高层主题」的量化形式：
    NPMI 高的边是「具体关系」，NPMI 低的边是「泛泛共现」。
    """
    import networkx as nx
    import math as _m

    rows = conn.execute("SELECT entity, df, ent_type FROM entity_stats").fetchall()
    ent_df = {r[0]: r[1] for r in rows}
    ent_ty = {r[0]: r[2] for r in rows}
    allow = {e for e, df in ent_df.items() if df >= min_df}
    if max_entities and len(allow) > max_entities:
        allow = set(sorted(allow, key=lambda e: -ent_df[e])[:max_entities])

    # 🔴 df 上限：光靠边权压不住泛词。
    # 实测 NPMI 加权后，度数次高的仍是「一种 / 能够 / 一些 / 它们 / 一定 /
    # 比如 / 成为 / 其实」——因为 NPMI 只惩罚「共现比例」，而这些词和几乎
    # **所有**词共现，p_ab ≈ min(p_a,p_b)，pmi 恒为正，压不下去。
    # 唯一的办法是把它们从图里剔除：df 超过语料 35% 的就不是实体，是虚词。
    if not n_docs:
        try:
            n_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        except Exception:
            n_docs = max(ent_df.values()) if ent_df else 1

    # 共现统计：**一次全表扫描 + 按 chunk 分组配对**
    #
    # 早期写法是「按实体分批 → 每批查这些实体涉及的 chunk → 组内配对」，
    # 结果同一个 chunk 会被反复取到几十次（一个 chunk 有 6~10 个实体，
    # 会被 6~10 个批次重复命中），15684 个实体时直接跑超 5 分钟。
    # 现在只扫一遍 chunk_entities（21 万行），顺序读取、边扫边配对。
    pair = defaultdict(int)
    cur, buf = None, []
    for cid, e in conn.execute(
            "SELECT chunk_id, entity FROM chunk_entities ORDER BY chunk_id"):
        if cid != cur:
            if len(buf) > 1:
                u = sorted(set(buf))
                for a in range(len(u)):
                    ua = u[a]
                    for b in range(a + 1, len(u)):
                        pair[(ua, u[b])] += 1
            cur, buf = cid, []
        if e in allow:
            buf.append(e)
    if len(buf) > 1:
        u = sorted(set(buf))
        for a in range(len(u)):
            for b in range(a + 1, len(u)):
                pair[(u[a], u[b])] += 1

    if max_df_ratio and max_df_ratio < 1.0:
        # 🔴 分母必须是「图覆盖的文档数」，不是全库文档数。
        # 侧表只覆盖部分文档时，拿全库 8363 算阈值（8363*0.35=2927）等于不筛，
        # 虚词全留在图里当枢纽。
        try:
            _gd = conn.execute(
                "SELECT COUNT(DISTINCT c.doc_id) FROM chunks c WHERE c.id IN "
                "(SELECT DISTINCT chunk_id FROM chunk_entities)").fetchone()[0]
        except Exception:
            _gd = None
        _base = _gd or n_docs
        hi_df = max(3, int(_base * max_df_ratio))
        dropped = {e for e in allow if ent_df.get(e, 0) > hi_df}
        if dropped:
            allow -= dropped
            print("[kg_ppr] 剔除高频泛词 %d 个（df > %d）：%s"
                  % (len(dropped), hi_df,
                     sorted(dropped, key=lambda e: -ent_df[e])[:6]))

    def idf(e):
        return math.log(1.0 + n_docs / max(1, ent_df.get(e, 1)))

    # chunk 级频次（NPMI 必须与共现同口径：都是「多少个块里出现」，
    # 不能拿文档级 df 混算，否则 pm i的量纲不对）
    chunk_df = {}
    total_chunks = max(1, conn.execute(
        "SELECT COUNT(DISTINCT chunk_id) FROM chunk_entities").fetchone()[0])
    for e, n in conn.execute(
            "SELECT entity, COUNT(*) FROM chunk_entities GROUP BY entity"):
        if e in allow:
            chunk_df[e] = n

    def edge_weight(a, b, co):
        """共现次数 co → 边权。"""
        if weighting == "raw":
            return float(co)
        ca = chunk_df.get(a, 1)
        cb = chunk_df.get(b, 1)
        pab = co / total_chunks
        pa = ca / total_chunks
        pb = cb / total_chunks
        if pab <= 0 or pa <= 0 or pb <= 0:
            return 0.0
        pmi = _m.log(pab / (pa * pb))
        if weighting == "pmi":
            return max(0.0, pmi)
        denom = -_m.log(pab)                     # NPMI
        if denom <= 1e-12:
            return 0.0
        v = pmi / denom
        return max(0.0, v)                       # 只留正相关

    G = nx.Graph()
    for e in allow:
        G.add_node(e, df=ent_df.get(e, 1), type=ent_ty.get(e, "noun"))
    kept = 0
    for (a, b), w in pair.items():
        if w < min_w:
            continue
        ww = edge_weight(a, b, w)
        if idf_weight and weighting == "raw":
            ww *= _m.sqrt(idf(a) * idf(b))
        if ww <= 0:
            continue
        if G.has_edge(a, b):
            G[a][b]["w"] += ww
        else:
            G.add_edge(a, b, w=ww)
            kept += 1

    # 图实际覆盖的文档数：泛词阈值必须按这个算。
    # 拿「全库 8363 篇」算会把阈值放到天上（196 篇的侧表里 df=15 就成了枢纽），
    # 拿「节点最大 df」算又会把阈值压到十几个，把核心实体全踢掉。
    try:
        graph_docs = conn.execute(
            "SELECT COUNT(DISTINCT c.doc_id) FROM chunks c WHERE c.id IN "
            "(SELECT DISTINCT chunk_id FROM chunk_entities)").fetchone()[0] or n_docs
    except Exception:
        graph_docs = n_docs
    G.graph["n_docs"] = graph_docs

    # 🔴 剔除「和什么都共现」的虚词。
    #
    # 判据不是 df，也不是边权，而是 **度数 / df 的比值**。实测（196 篇侧表）：
    #   虚词   一种 83.8 ｜ 能够 95.8 ｜ 一些 117.6 ｜ 它们 57.9 ｜ 比如 79.0
    #   真实体 血透  7.4 ｜ 时序  6.2 ｜ 某集群五大系统 5.7 ｜ 护士 5.7
    # 差一个数量级，阈值取 25 就能干净切开。
    #
    # 为什么 df 判据不管用：「一种」只在 33 篇里出现（df 不高），
    # 但那些篇里几乎每句都有它，于是和 2767 个不同实体共现 ——
    # df 看不出问题，度数看得出。为什么 NPMI 也压不住：它和几乎所有词共现时
    # p_ab ≈ min(p_a,p_b)，PMI 恒为正。
    dropped_v = []
    if max_deg_df_ratio and max_deg_df_ratio > 0:
        for n in list(G.nodes):
            df = G.nodes[n].get("df", 1)
            if G.degree(n) / max(1, df) > max_deg_df_ratio:
                dropped_v.append(n)
        if dropped_v:
            G.remove_nodes_from(dropped_v)
            print("[kg_ppr] 剔除虚词 %d 个（度/df > %.0f）：%s"
                  % (len(dropped_v), max_deg_df_ratio,
                     sorted(dropped_v, key=lambda e: -G.degree(e) if e in G else 0)[:6]))

    stats = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "graph_docs": graph_docs,
        "dropped_filler": len(dropped_v),
        "pairs_raw": len(pair),
        "docs": n_docs,
        "total_chunks": total_chunks,
        "weighting": weighting,
        "min_w": min_w,
        "density": (2 * G.number_of_edges()
                    / max(1, G.number_of_nodes() * (G.number_of_nodes() - 1))),
        "avg_degree": (2 * G.number_of_edges() / max(1, G.number_of_nodes())),
    }
    return G, stats


# ---------------------------------------------------------------- 种子
def link_entities(query, graph, dic=None, max_seeds=24, n_docs=None):
    """把查询链接到图节点 → {entity: weight}

    两级匹配（HippoRAG 用稠密编码器做同义链接，这里用更轻的确定性办法）：
      ① 精确：查询里出现图节点的完整名字
      ② 包含：图节点名是查询子串（≥3 字），或查询词是节点名子串
    长词优先，命中后从查询里「挖掉」已匹配部分，避免「集群五大系统」
    同时把「集群」「五大」「系统」也配上（那会把种子污染成泛词）。
    """
    q = (query or "").strip()
    if not q or graph.number_of_nodes() == 0:
        return {}

    nodes = list(graph.nodes)
    by_len = sorted(nodes, key=len, reverse=True)
    seeds, consumed = {}, []

    def _free(span):
        s, e = span
        for a, b in consumed:
            if s < b and a < e:      # 有交叠
                return False
        return True

    # ① 精确子串（长词优先）
    for e in by_len:
        if len(e) < 2:
            continue
        idx = q.find(e)
        while idx >= 0:
            if _free((idx, idx + len(e))):
                seeds[e] = seeds.get(e, 0) + 1
                consumed.append((idx, idx + len(e)))
                break
            idx = q.find(e, idx + 1)

    # ② 查询词 → 节点（查询侧的分词结果作为弱种子）
    if not seeds:
        try:
            import kg_terms
            cand = [t for t, _ty in kg_terms.extract(q, None, use_jieba=True)]
        except Exception:
            cand = []
        for c in cand:
            for e in nodes:
                if len(e) >= 3 and (c in e or e in c):
                    seeds[e] = seeds.get(e, 0) + 0.5

    # 截断（长词优先）
    if len(seeds) > max_seeds:
        seeds = dict(sorted(seeds.items(), key=lambda kv: (-len(kv[0]), -kv[1]))
                     [:max_seeds])

    # 🔴 种子质量闸门（这是 PPR 的命门）
    #
    # 实测教训：查询「集群五大系统里第一个上线的那套用了什么库」，
    # 第一版只链接到「第一个」一个种子，PPR 从那扩散，一路走到
    # 《C++ 编程自学宝典》——因为「第一个」「第二个」「运算符」「初始化」
    # 在编程教材里密集共现。
    #
    # 结论：**PPR 再先进，种子错了全盘皆错。** 而种子质量取决于
    # 抽取与链接，不取决于 PageRank 本身。
    #
    # 三道闸：
    #   ① 序数/指代词直接踢（没有指称对象，扩散出去必然跑偏）
    #   ② 中文 <3 字的短词踢掉（除非在词典里且 df 很低 —— 那说明是真专名）
    #   ③ df 超过语料 35% 的泛词踢掉（「系统」「项目」这类，作为种子没有定位力）
    try:
        import kg_terms as _kt
        ord_re = _kt._ORDINAL
    except Exception:
        ord_re = None
    # 🔴 必须用**真实文档数**算泛词阈值，不能拿「节点最大 df」顶替。
    # 踩过：196 篇的侧表里最大 df 是 39，拿它当 n_docs 会把阈值压到 13.65，
    # 于是 df=15 的核心实体「某集群五大系统」被当成泛词踢掉，种子直接变空 ——
    # 表现为「PPR 什么都不返回」，但根因在阈值算错，不在 PPR。
    if not n_docs:
        n_docs = graph.graph.get("n_docs") or max(
            [d.get("df", 1) for _n, d in graph.nodes(data=True)] or [1])
    hi = n_docs * 0.35
    try:
        import kg_terms as _kt2
        stop = _kt2.STOP_CN
    except Exception:
        stop = set()
    clean = {}
    for e, w in seeds.items():
        if ord_re and ord_re.match(e):
            continue
        # 停用词不进种子：字面上确实是图节点，但它不是「实体」。
        # 实测「哪种」「专门」「常规」都成了种子，把 PPR 概率摊掉一半。
        if e in stop:
            continue
        df = graph.nodes.get(e, {}).get("df", 1)
        if df > hi:
            continue
        # 二字词：不按长度一刀切（「时序」「血透」是真名词），
        # 而是要求「有稳定共现」——df≥3 且度数≥5，才认为它是概念而非碎词。
        if len(e) < 3 and not (dic and e in dic):
            if df < 3 or graph.degree(e) < 5:
                continue
        clean[e] = w
    return clean


def specificity(df, n_docs, power=DEFAULT_SPEC_POWER):
    """节点特异性：高频实体的种子权重要压下去。

    HippoRAG 的动机：模拟大脑依赖**局部**信号，而不是做全局 IDF 统计。
    形式上就是高频降权。这里用 `1/(df/n_docs)^power` 的温和版本，
    power=0 等于关闭（用于做消融对照）。
    """
    if power <= 0:
        return 1.0
    return (max(1, df) / max(1, n_docs)) ** (-power)


# ---------------------------------------------------------------- PPR
_NUMPY_CACHE = {}


def _csr(graph):
    """把 networkx 图转成 (src, dst, trans, pers_slots, node_list)。

    为什么要自己建 CSR：networkx 的 `pagerank` 内部走 scipy 稀疏矩阵，
    本机没装 scipy 就直接 ModuleNotFoundError；装 scipy 又给这个模块增加
    一个重依赖（约 40MB）。PPR 本身只是一次「稀疏矩阵 × 向量」的幂迭代，
    用 numpy 的 `bincount` 做加权聚合就够，几十行、零额外依赖。
    """
    import numpy as np

    key = id(graph)
    if key in _NUMPY_CACHE:
        return _NUMPY_CACHE[key]

    nodes = list(graph.nodes)
    idx = {n: i for i, n in enumerate(nodes)}
    N = len(nodes)
    src, dst, w = [], [], []
    for u, v, d in graph.edges(data=True):
        ww = float(d.get("w", 1.0))
        iu, iv = idx[u], idx[v]
        src.append(iu); dst.append(iv); w.append(ww)
        src.append(iv); dst.append(iu); w.append(ww)
    if not src:                                    # 无边：退化成单节点
        src, dst, w = [0], [0], [0.0]
        if N == 0:
            N = 1
            nodes = [""]
            idx = {"": 0}
    src = np.asarray(src, dtype=np.int64)
    dst = np.asarray(dst, dtype=np.int64)
    w = np.asarray(w, dtype=np.float64)
    outdeg = np.bincount(src, weights=w, minlength=N)
    inv = np.where(outdeg > 0, 1.0 / np.maximum(outdeg, 1e-12), 0.0)
    trans = w * inv[src]                           # 每条转移边携带的概率质量
    dangling = outdeg <= 0
    out = (nodes, idx, src, dst, trans, dangling, N)
    _NUMPY_CACHE[key] = out
    return out


def ppr_numpy(graph, pers_vec, alpha=DEFAULT_ALPHA, max_iter=20, tol=1e-5):
    """PPR 幂迭代（numpy 实现）。pers_vec 必须已归一化。

    max_iter=20 / tol=1e-5 是实测出来的，不是拍的：
    70 万边的图上，跑满 59 轮（tol=1e-10）要 808ms，而 12 轮只要 148ms，
    **两者 top5 完全一致**。原因：alpha=0.85 时每轮泄漏 15%，
    12 轮已覆盖 1-0.85^12 = 86% 的概率质量，再迭代只在小数点后变化。
    取 20 轮留余量（245ms），换不同查询的稳定性。
    """
    import numpy as np

    nodes, _idx, src, dst, trans, dangling, N = _csr(graph)
    x = pers_vec.copy()
    for _ in range(max_iter):
        contrib = np.bincount(dst, weights=trans * x[src], minlength=N)
        leak = float(x[dangling].sum())            # 悬挂节点不泄漏，回流到 personalization
        x_new = (1.0 - alpha) * pers_vec + alpha * (contrib + leak * pers_vec)
        if np.abs(x_new - x).sum() < tol:
            x = x_new
            break
        x = x_new
    return {nodes[i]: float(x[i]) for i in range(N) if x[i] > 0}


def ppr(graph, seeds, alpha=DEFAULT_ALPHA, n_docs=None, spec_power=DEFAULT_SPEC_POWER):
    """以 seeds 为起点跑 Personalized PageRank → {entity: score}

    与「扩 N 跳再取权重」的本质区别：
      · BFS/1-hop：距离内全部收下，远近不分
      · PPR：概率按图的度数结构分配，且 (1-alpha) 每步泄漏，
             远了自然衰减，枢纽节点因为出边多而被摊薄

    `personalization` 就是种子分布。种子按 node specificity 加权，
    等价于「泛词的种子不如专有名词值钱」。
    """
    import numpy as np

    if not seeds or graph.number_of_nodes() == 0:
        return {}

    if n_docs is None:
        n_docs = max([d.get("df", 1) for _n, d in graph.nodes(data=True)] or [1])

    nodes, idx, _s, _d, _t, _dg, N = _csr(graph)
    vec = np.zeros(N, dtype=np.float64)
    total = 0.0
    for e, w in seeds.items():
        i = idx.get(e)
        if i is None:
            continue
        df = graph.nodes[e].get("df", 1)
        v = float(w) * specificity(df, n_docs, spec_power)
        vec[i] = v
        total += v
    if total <= 0:
        for e in seeds:
            i = idx.get(e)
            if i is not None:
                vec[i] = 1.0
                total += 1.0
    if total <= 0:
        return {}
    vec /= total
    return ppr_numpy(graph, vec, alpha=alpha)


# ---------------------------------------------------------------- 文档打分
def score_chunks(conn, ent_scores, topk=10, min_ent_score=0.0,
                 by_doc=True, len_penalty=0.5):
    """实体分数 → chunk/文档 分数（HippoRAG 的实体-文档矩阵）

    passage_score = Σ_{e ∈ passage} PPR[e]

    但直接 sum 有两个坑（实测踩到）：

      ① **长文档霸榜**：`wiki/log.md` 被切成 546 块，每块都能蹭到一点实体分，
         累加后必然排第一。它不是「最相关」，只是「最长」。
      ② **chunk 数 ≠ 相关度**：同一篇文档命中 5 个 chunk，不该比命中 1 个的
         得 5 倍分。

    所以默认 `by_doc=True`：同一 path 的 chunk 分数取 **max**（最强证据），
    再除以 `(1+log(1+该文档chunk数))^len_penalty` 做长度惩罚。
    `len_penalty=0` 关掉惩罚（用于消融）。

    评测的 gt 本来就是文档级（path），按文档聚合也更对齐。
    """
    if not ent_scores:
        return []
    items = [(e, s) for e, s in ent_scores.items() if s > min_ent_score]
    if not items:
        items = list(ent_scores.items())
    items.sort(key=lambda kv: -kv[1])
    items = items[:60]                 # 长尾会把分数摊平

    score = defaultdict(float)
    B = 200
    for i in range(0, len(items), B):
        part = items[i:i + B]
        ents = [e for e, _ in part]
        ph = ",".join("?" * len(ents))
        w = {e: s for e, s in part}
        for cid, e in conn.execute(
                "SELECT chunk_id, entity FROM chunk_entities WHERE entity IN (%s)"
                % ph, tuple(ents)):
            score[cid] += w.get(e, 0.0)

    # 取候选 chunk（多取一些，聚合后再截断）
    ids = sorted(score, key=lambda c: -score[c])[:max(topk * 20, 200)]
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    meta = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT id, path, doc_id FROM chunks WHERE id IN (%s)" % ph, tuple(ids))}

    if not by_doc:
        out = [(cid, meta.get(cid, ("", 0))[0], score[cid]) for cid in ids]
        out.sort(key=lambda x: -x[2])
        return out[:topk]

    # 按文档聚合：max + 长度惩罚
    best = {}
    cnt = defaultdict(int)
    for cid in ids:
        p, _d = meta.get(cid, ("", 0))
        if not p:
            continue
        cnt[p] += 1
        if score[cid] > best.get(p, -1):
            best[p] = score[cid]
    # 文档总 chunk 数（用于长度惩罚）；拿不到就按命中数近似
    paths = list(best)
    total_chunks = {}
    for i in range(0, len(paths), 300):
        part = paths[i:i + 300]
        ph2 = ",".join("?" * len(part))
        for p, n in conn.execute(
                "SELECT path, COUNT(*) FROM chunks WHERE path IN (%s) GROUP BY path"
                % ph2, tuple(part)):
            total_chunks[p] = n

    def adj(p):
        n = total_chunks.get(p, cnt[p]) or 1
        if len_penalty <= 0:
            return best[p]
        return best[p] / ((1.0 + math.log(1.0 + n)) ** len_penalty)

    out = sorted(((p, adj(p)) for p in best), key=lambda x: -x[1])
    return [(None, p, s) for p, s in out[:topk]]


# ---------------------------------------------------------------- 端到端
def search_ppr(conn, query, topk=10, graph=None, dic=None, alpha=DEFAULT_ALPHA,
               spec_power=DEFAULT_SPEC_POWER, n_docs=None, return_debug=False):
    """查询 → PPR → chunk 列表。

    graph 传进来的话复用（在线检索必须复用，建图是重活）。
    返回 [(chunk_id, path, score)]；return_debug 时多返回种子与实体分。
    """
    if graph is None:
        graph, _st = build_graph(conn)
    if n_docs is None:
        try:
            n_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        except Exception:
            n_docs = None
    seeds = link_entities(query, graph, dic=dic, n_docs=n_docs)
    if not seeds:
        return ([], {}, {}) if return_debug else []
    es = ppr(graph, seeds, alpha=alpha, n_docs=n_docs, spec_power=spec_power)
    res = score_chunks(conn, es, topk=topk)
    if return_debug:
        top_ent = dict(sorted(es.items(), key=lambda kv: -kv[1])[:8])
        return res, seeds, top_ent
    return res


# ---------------------------------------------------------------- 社区（LightRAG 的高层）
def communities(graph, resolution=1.0, seed=42):
    """Louvain 社区检测（networkx 自带，不需要 python-louvain）。

    LightRAG 的「高层检索」依赖社区：把图切成主题簇，簇级摘要用于回答
    「整体讲了什么」这类问题。这里只做**划分**，不做摘要 ——
    摘要需要 LLM，成本高，且本项目的多跳问题还没走到那一步。
    产出的社区编号用于：① 可视化按主题岛着色 ② 后续 high-level 检索的雏形。
    """
    import networkx as nx

    if graph.number_of_edges() == 0:
        return {}, []
    try:
        parts = nx.community.louvain_communities(graph, weight="w",
                                                 resolution=resolution, seed=seed)
    except Exception:
        return {}, []
    comm = {}
    for ci, part in enumerate(sorted(parts, key=len, reverse=True)):
        for n in part:
            comm[n] = ci
    sizes = [len(p) for p in sorted(parts, key=len, reverse=True)]
    return comm, sizes


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "state/_kg_sandbox.db"
    conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    import time as _t
    t0 = _t.time()
    G, st = build_graph(conn)
    print("建图 %.1fs ｜ 节点 %d ｜ 边 %d ｜ 密度 %.5f ｜ 共现对 %d"
          % (_t.time() - t0, st["nodes"], st["edges"], st["density"], st["pairs_raw"]))
    comm, sizes = communities(G)
    print("社区 %d 个 ｜ 前 8 大 %s" % (len(sizes), sizes[:8]))
    q = sys.argv[2] if len(sys.argv) > 2 else "集群五大系统里第一个上线的那套用了什么数据库"
    t1 = _t.time()
    res, seeds, ents = search_ppr(conn, q, topk=5, graph=G, return_debug=True)
    print("\n查询：%s" % q)
    print("种子 %s" % seeds)
    print("PPR top 实体 %s" % {k: round(v, 5) for k, v in ents.items()})
    print("耗时 %.2fs ｜ 结果：" % (_t.time() - t1))
    for cid, path, sc in res:
        print("  %.6f  %s" % (sc, path))
    conn.close()
