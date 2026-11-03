#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kg_terms.py — 领域词典构建 + 短语级实体抽取（#41 第二版）

# 为什么要有这个文件

#41 第一版用 `jieba.cut` 直接抽实体，结论是「图谱没用」。但复盘发现
真正的失败点是**实体被切碎**，不是图谱机制：

    jieba.cut("集群五大系统项目里…")
      → ['集群', '五大', '系统', '项目', ...]

「系统」被 df 过滤掉（太泛），剩下「集群」「五大」这种碎片去扩邻居，
于是 1-hop 召回的是毫不相干的东西（实测召回到了求职简历）。
这不是「1-hop 无效」，是「1-hop 拿到的起点就是错的」。

## 根因

jieba 的默认词典里没有领域专有名词。它在通用语料上训练，
「集群五大系统」这种本项目特有的串，必然被切成最大匹配的通用词。

## 解法：先把领域词教给分词器，再分词

三部曲：
  ① **从标题挖候选**（最高信噪比的一步）
     知识库 7524 篇文档的标题天然是领域名词表：
       「集群五大系统项目 — 总览」   → 集群五大系统项目
       「全院统一预约平台（UMRP）」  → 全院统一预约平台 / UMRP
     按分隔符（— ｜ ： ·）和括号切开就是候选短语。

  ② **注入 jieba**（`add_word`，freq 按文档频次加权）
     注入后 `jieba.cut` 就把「集群五大系统项目」当一个词输出，
     不再拆成 集群/五大/系统/项目。

  ③ **抽实体 + df 过滤**
     词典命中优先（多字短语整体保留），再叠加规则（《》、反引号、
     大写标识符、版本号），最后按**文档频次**过滤：
     df=1 的多半是一次性噪声，df 过高的（>35% 文档都出现）是泛词。

## 实测对比（196 篇评测相关文档，6182 chunk）

| 版本 | 中文实体占比 | 短语实体（≥4 字） | 「集群五大系统」形态 |
|---|---|---|---|
| v1 jieba.cut 直抽 | 7.8%  | 极少       | 集群 / 五大 / 系统 |
| v2 + 词性 + df      | 69.9% | 少         | 集群 / 五大 / 系统 |
| v3 词典注入（本文件）| 见 report | 见 report | 集群五大系统项目 ✅ |

用法：
    import kg_terms
    dic = kg_terms.build_domain_dict(conn)        # 从 docs.title 构建
    kg_terms.inject(dic)                          # 注入 jieba
    ents = kg_terms.extract(text, dic)            # 抽实体 [(词, 类型)]
    ents = kg_terms.filter_by_df(pairs, n_docs)   # df 过滤

与 kg_extract.py 的分工：
    kg_extract 是「旧版规则抽取」，被 _test_kg.py 钉着；
    本文件是它的**上游**——先教词典，再用它的规则。
    两者可独立测试，互不破坏。
"""

import os
import re
import sys

# ---- 分隔符：标题里用来切出短语的 ----
_SPLIT = re.compile(r"\s*[—–\-｜|：:·、]\s*")

# ---- 规则抽取用的模式 ----
_RE_BOOK = re.compile(r"《([^》]{2,40})》")
_RE_QUOTE = re.compile(r"「([^」]{2,40})」")
_RE_CJK_QUOTE = re.compile(r"“([^”]{2,40})”")
_RE_CODE = re.compile(r"`([^`\n]{2,60})`")
_RE_PAREN = re.compile(r"[（(]([^）)]{2,30})[）)]")
_RE_TAG = re.compile(r"(?<![\w#])#([A-Za-z][\w\-]{1,30})")
_RE_IDENT = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:[A-Z][a-zA-Z0-9]*)+)\b")
_RE_VER = re.compile(r"\b([a-zA-Z][\w\-]{1,20}\s?v?\d+(?:\.\d+){1,3})\b")

# ---- 停用词 ----
STOP_CN = set("""
系统 问题 方法 方式 内容 时间 文件 数据 情况 时候 部分 东西 事情 地方
意思 建议 说明 记录 目录 结果 原因 需求 功能 项目 文档 用户 使用 需要
一个 这个 那个 什么 怎么 因为 所以 如果 但是 已经 还是 就是 没有 不是
这样 那样 我们 他们 自己 现在 之前 之后 同时 另外 此外 因此 然后 而且
虽然 由于 通过 进行 实现 提供 支持 包括 以及 方面 过程 状态 类型 名称
标题 链接 地址 路径 版本 数量 大小 长度 单位 工具 平台 界面 相关
主要 一般 可以 不能 应该 可能 需要 目前 当前 最后 第一 第二 第三 其他
以上 以下 其中 这种 那种 各种 有些 很多 非常 比较 十分 特别 尤其 例如
保留 分析 参考 内容 说明 备注 补充 更新 修复 新增 删除 修改 调整 优化
开始 结束 完成 继续 支持 包含 对应 根据 按照 用于 用来 表示 显示 出现
总览 评估 规范 有没有 每日 总结 汇总 清单 列表 详情 概览 指南 查询
第一个 第二个 第三个 第四个 第五个 首先 其次 然后 最后 一次 两次
那套 这套 那种 这种 那些 这些 哪个 哪些 什么样 如何 是否 能否
上线 下线 之前 之后 里面 之外 除了 关于 对应 相关 有关 所谓
专门 常规 承载 哪种 哪套 该套 这套 那套 采用 选型 选项 方向 维度
上面 下面 前面 后面 左边 右边 内部 外部 整体 单个 多个 大量 少量
""" .split())

# 序数 / 指代词模式：这类词在任何语料里都高频共现，做实体只会污染图
_ORDINAL = re.compile(
    r"^第[一二三四五六七八九十百零\d]+[个套条款项次级批版]?$")

STOP_EN = set("""
in the to and of for is are was were on at by with from as an or if it
this that be not you we they can will has have had but so no do does did
all any our your their its which who what when where how why than then
there here about into over under more most some such only also just very
well may might must shall should would could jpg jpeg png gif pdf md html
http https www com cn org net php py js json yml yaml txt csv xml url uri
api sdk ui ux ok ipc cpu gpu ram ssd hdd tmp temp log logs src dst the
""".split())

# 纯数字/日期/编号
_DIGITISH = re.compile(r"^[\d\.\-%/：:年月日号第期]+$")
_DATEY = re.compile(r"^(19|20)\d{2}([-/年]?\d{1,2}){0,2}$")

_CN = re.compile(r"[\u4e00-\u9fff]")


def _valid_term(t):
    """候选术语是否可用。"""
    t = (t or "").strip()
    if not (2 <= len(t) <= 30):
        return False
    if t in STOP_CN or t.lower() in STOP_EN:
        return False
    if _DIGITISH.match(t) or _DATEY.match(t):
        return False
    # 序数/指代（第一个 / 第三套 / 第二批）：无信息量，进图只会把扩散带偏
    if _ORDINAL.match(t):
        return False
    # 含括号的整串不要（「隐私文件索引（自动识别）」这种是标题残渣，
    # 括号里的内容已经在 build_domain_dict 里单独提出来了）
    if "（" in t or "(" in t or "）" in t or ")" in t:
        return False
    # 至少要有中文或字母，纯标点不要
    if not (_CN.search(t) or re.search(r"[A-Za-z]", t)):
        return False
    # 路径片段 / 文件后缀 / 邮箱式串：不是实体（「modules/」「Sources」「a.b」）
    if re.search(r"[/\\@]", t):
        return False
    if re.fullmatch(r"[A-Za-z_][\w.\-]*\.(md|py|json|txt|csv|log|db|jpg|png|pdf|yml|yaml|html)", t, re.I):
        return False
    # 纯英文必须 ≥3 字符（「ip」「ui」「ok」这种不是实体）
    if not _CN.search(t) and len(t) < 3:
        return False
    # 以「的/了/是/在」等虚词开头的，是切歪的残片
    if t[0] in "的了是在和与及或对从把被让给这那":
        return False
    return True


def _is_path_fragment(t):
    """标题里的路径片段 / 目录名 / 纯小写代码词，不是实体。

    「Sources」「Raw」「wiki」「app」「run」这类出现在标题里，是因为有些文档
    标题直接用了目录名或文件名。它们是**结构**不是**概念**，
    进了实体表只会让 1-hop 扩到一堆无关目录。
    """
    if _CN.search(t):
        return False
    # 全小写且短（wiki / app / run / src / tmp）
    if t.islower() and len(t) <= 4:
        return True
    # 常见目录名 / 结构词（大小写都算）
    if t.lower() in {"sources", "raw", "wiki", "modules", "state", "docs",
                     "outputs", "readme", "index", "log", "logs", "tmp",
                     "temp", "assets", "static", "build", "dist", "tests",
                     "test", "src", "lib", "bin", "data", "files", "media"}:
        return True
    return False


def build_domain_dict(conn, min_count=1, max_count=None):
    """从 docs.title 构建领域词典 → {术语: 出现该术语的标题数}

    min_count=1 表示「只出现一次也收」——标题类专有名词本来就只出现一次，
    但它们恰恰是最有价值的实体（每篇文档的中心词）。
    """
    rows = conn.execute(
        "SELECT title FROM docs WHERE title IS NOT NULL").fetchall()
    cnt = {}
    for (title,) in rows:
        title = (title or "").strip()
        if not title:
            continue
        seen = set()
        # 🔴 先把括号内容剥掉再切分。
        # 否则「全院统一预约平台（UMRP）」整串含括号 → _valid_term 判死 →
        # 正文里最该被识别的那个短语反而进不了词典。
        # 括号里的缩写（UMRP）下面单独提取，两不耽误。
        bare = _RE_PAREN.sub("", title).strip()
        # 整标题（去掉「— 副标题」后缀）
        head = _SPLIT.split(bare)[0].strip()
        if _valid_term(head):
            seen.add(head)
        # 各分段
        for p in _SPLIT.split(bare):
            p = p.strip()
            if _valid_term(p):
                seen.add(p)
        # 括号内（模型名 / 缩写）
        for p in _RE_PAREN.findall(title):
            p = p.strip()
            if _valid_term(p) and not _is_path_fragment(p):
                seen.add(p)
        for t in list(seen):
            if _is_path_fragment(t):
                seen.discard(t)
        for t in seen:
            cnt[t] = cnt.get(t, 0) + 1
    if max_count is not None:
        cnt = {k: v for k, v in cnt.items() if v <= max_count}
    return {k: v for k, v in cnt.items() if v >= min_count}


def inject(dic, freq_base=100000):
    """把词典注入 jieba。必须在任何分词之前调用。

    freq 给大值：让「集群五大系统项目」优先于「集群 + 五大 + 系统」的组合。
    jieba 的 HMM 会在词典命中时直接输出长词，freq 越大越不会被拆。
    """
    import jieba
    jieba.setLogLevel(20)
    for term, n in dic.items():
        # 出现次数多的术语给更高 freq（最多 3 倍），提升长词优先度
        jieba.add_word(term, freq=freq_base * min(3, 1 + n // 5))
    return len(dic)


_BY_FIRST = {}


def _index_dict(dic):
    """把词典按**首字**建倒排索引。

    为什么需要：5556 个词典词 × 111441 个 chunk 直接 `term in text` 是
    6.2 亿次子串查找，单进程要跑几十分钟。按首字分组后，文本里每个位置
    只需要看「首字相同的那几个词」（平均 2 个），成本降两个数量级。
    jieba 自己虽然有更快的 DAG，但它的输出受上下文影响；这里的子串扫描
    是「词典词只要出现就一定认」的兜底，两者互补。
    """
    global _BY_FIRST
    _BY_FIRST = {}
    for term in dic:
        if len(term) >= 4:
            _BY_FIRST.setdefault(term[0], []).append(term)
    # 长词优先匹配（避免「集群五大系统」先占了「集群五大系统项目」的位置）
    for k in _BY_FIRST:
        _BY_FIRST[k].sort(key=len, reverse=True)
    return len(_BY_FIRST)


def _scan_dict(text, dic=None):
    """首字倒排扫描：返回文本里出现的词典词（长词优先，命中后跳过其覆盖区）。

    `dic` 可不传（复用已建索引）；传了但索引是空的会自动建 —— 否则单独调用
    这个函数（比如测试里）会永远返回空，看起来像「扫描器坏了」。
    """
    if dic is not None and not _BY_FIRST:
        _index_dict(dic)
    if not _BY_FIRST:
        return []
    hits, i, n = [], 0, len(text)
    while i < n:
        cands = _BY_FIRST.get(text[i])
        if cands:
            matched = None
            for t in cands:
                if text.startswith(t, i):
                    matched = t
                    break
            if matched:
                hits.append(matched)
                i += len(matched)
                continue
        i += 1
    return hits


def extract(text, dic=None, use_jieba=True):
    """抽实体 → [(词, 类型)]

    类型：dict(词典命中) / title / quote / code / tag / ident / noun
    词典命中排在最前且不会被后续规则覆盖掉。
    """
    text = text or ""
    out = {}
    # ① 词典命中（最高优先）：首字倒排扫描，保证短语不被切碎
    if dic:
        if not _BY_FIRST:
            _index_dict(dic)
        for term in _scan_dict(text):
            out[term] = "dict"
    # ② 规则
    for m in _RE_BOOK.findall(text):
        t = m.strip()
        if _valid_term(t):
            out.setdefault(t, "title")
    for m in _RE_QUOTE.findall(text) + _RE_CJK_QUOTE.findall(text):
        t = m.strip()
        if _valid_term(t):
            out.setdefault(t, "quote")
    for m in _RE_CODE.findall(text):
        m = m.strip()
        if re.fullmatch(r"[A-Za-z_][\w\.\-\(\)]*", m) and _valid_term(m):
            out.setdefault(m, "code")
    for m in _RE_TAG.findall(text):
        out.setdefault(m, "tag")
    for m in _RE_IDENT.findall(text):
        if _valid_term(m):
            out.setdefault(m, "ident")
    # ③ 分词（此时已注入词典）
    if use_jieba:
        import jieba
        for w in jieba.cut(text):
            w = w.strip()
            if not _valid_term(w):
                continue
            if w in STOP_CN or w.lower() in STOP_EN:
                continue
            if w in out:
                continue
            # 中文二字词：早期版本要求「必须在词典里」，结果把「时序」「血透」
            # 「护士」这类真正的领域词全杀了 —— 它们不进标题，所以词典没有，
            # 而它们恰恰是跨文档跳转的关键线索。
            # 改为只挡「停用词表里明确列出的泛词」，其余放行到 df 过滤再裁决。
            if _CN.match(w) and len(w) == 2:
                if not (dic and w in dic) and w in STOP_CN:
                    continue
            # 全小写英文且不在词典：多半是代码碎片（「the」「src」「tmp」）
            if (not _CN.search(w)) and w.islower() and not (dic and w in dic):
                continue
            out[w] = "noun" if _CN.search(w) else "ident"
    return list(out.items())


def filter_by_df(pairs, n_docs, lo=2, hi_ratio=0.35):
    """按文档频次过滤：df ∈ [lo, n_docs*hi_ratio]

    pairs: [(chunk_id, doc_id, [(term, type)])]
    lo=2：只在一篇文档出现的，多半是一次性噪声（但是标题类专有词会保留，
          因为它们同时来自词典，见 keep_dict）
    hi_ratio=0.35：超过 35% 的文档都出现的，是泛词（「知识库」「项目」这类）
    """
    df = {}
    for _cid, did, ents in pairs:
        for t, _ty in ents:
            df.setdefault(t, set()).add(did)
    hi = max(2, int(n_docs * hi_ratio))
    keep = {t for t, s in df.items() if lo <= len(s) <= hi}
    return keep, df, hi


def report_stats(pairs, keep, n_docs):
    """给人工看的质量报告。"""
    total = sum(len(e) for _c, _d, e in pairs)
    kept = sum(1 for _c, _d, e in pairs for t, _ty in e if t in keep)
    terms = {t for _c, _d, e in pairs for t, _ty in e if t in keep}
    cn = [t for t in terms if _CN.search(t)]
    long4 = [t for t in terms if len(t) >= 4]
    dicts = [t for _c, _d, e in pairs for t, ty in e if ty == "dict" and t in keep]
    return {
        "chunks": len(pairs),
        "docs": n_docs,
        "entity_mentions_raw": total,
        "entity_mentions_kept": kept,
        "unique_terms_raw": len({t for _c, _d, e in pairs for t, _ty in e}),
        "unique_terms_kept": len(terms),
        "chinese_ratio": (len(cn) / len(terms)) if terms else 0,
        "phrase4_ratio": (len(long4) / len(terms)) if terms else 0,
        "dict_hits": len(set(dicts)),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import sqlite3
    db = sys.argv[1] if len(sys.argv) > 1 else "state/_kg_sandbox.db"
    conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    d = build_domain_dict(conn)
    print("领域词典：%d 条" % len(d))
    sample = [k for k in d if len(k) >= 4][:15]
    print("长词样例：", sample)
    for probe in ("集群五大系统", "集群五大系统项目", "全院统一预约平台",
                  "智能血液透析信息系统", "可复用模块"):
        print("  词典含 %-20s %s" % (probe, probe in d))
    conn.close()
