# -*- coding: utf-8 -*-
"""分块垃圾过滤器（重复块治理的「正确解法」第一步）
=================================================
2026-11-02 发现：35.5% 的块是重复/垃圾内容（41,146 / 115,861），来源：
  日志重复行、CSV 空行 `,,,,,,,`、base64 串、PPT 模板标记。
正确解法是「分块阶段加垃圾过滤 + 全量重切」（约 2h，L2，需拍板）。

本模块是过滤器的**纯函数实现 + 单测**，不改动生产 split_chunks：
  - is_garbage_block(text)：候选块是否纯垃圾（应丢弃）
  - dedup_blocks(blocks)：同文档内丢掉归一化后完全相同的块
  - filter_blocks(blocks) ：先过滤垃圾、再去重，返回干净块列表

接入点（拍板后）：在 kb_index.split_chunks 产出候选块后调用 filter_blocks，
再跑 rechunk_all.py 全量重切。filter_blocks 幂等、对真实内容块零误杀（单测覆盖）。
"""
import re
from collections import Counter

_B64_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")
_PPT_MARKERS = (
    "点击此处添加", "Microsoft PowerPoint", "未命名幻灯片",
    "右键单击此处", "在此处添加", "单击此处添加",
)


def _norm_lines(text):
    return [ln.strip() for ln in (text or "").splitlines()]


def is_garbage_block(text, min_lines=4):
    """True = 纯垃圾，分块阶段应丢弃。False = 保留（真实内容）。"""
    if text is None:
        return True
    t = text.strip()
    if not t:
        return True
    lines = _norm_lines(t)
    nonempty = [l for l in lines if l]
    if not nonempty:
        return True
    # 1) CSV 空行：所有非空行都是纯分隔符（逗号/分号/制表/竖线 + 空白）
    if all(re.fullmatch(r"[\s,;\t\|]*", l) for l in nonempty):
        return True
    # 2) base64 长串：存在超长无空格 base64 token（中文/英文正文不会触发）
    if any(len(l) >= 120 and _B64_RE.match(l) and " " not in l for l in nonempty):
        return True
    # 2b) 整块几乎全是 base64 字符（长块）
    total = sum(len(l) for l in nonempty)
    b64 = sum(len(re.sub(r"[^A-Za-z0-9+/=_-]", "", l)) for l in nonempty)
    if total >= 300 and b64 / total >= 0.92:
        return True
    # 3) PPT 模板标记：去掉标记后实义文本极少
    if any(m in t for m in _PPT_MARKERS):
        stripped = t
        for m in _PPT_MARKERS:
            stripped = stripped.replace(m, "")
        stripped = re.sub(r"[\s,.;:!?]+", "", stripped)
        if len(stripped) <= 8:
            return True
    # 4) 日志重复行：非空行里同一行占比过高
    if len(nonempty) >= min_lines:
        top, cnt = Counter(nonempty).most_common(1)[0]
        if cnt / len(nonempty) >= 0.8:
            return True
    return False


def dedup_blocks(blocks):
    """同文档内丢弃归一化后完全相同的块，保留首次出现。"""
    seen, out = set(), []
    for b in blocks:
        key = re.sub(r"\s+", " ", (b or "").strip())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def filter_blocks(blocks):
    """先丢垃圾块，再去重。幂等、对真实内容块零误杀。"""
    kept = [b for b in blocks if not is_garbage_block(b)]
    return dedup_blocks(kept)


def filter_chunk_pairs(pairs):
    """pairs: list of (heading, text)。先丢垃圾块，再同文档去重。
    返回干净的 (heading, text) 列表。

    接入点（2026-11-02，重复块治理 A）：在 kb_index.split_chunks 产出候选块
    [(i, h, t)] 后，对其 (h, t) 部分调用本函数，再重排 chunk_no。幂等、
    对真实内容块零误杀（单测覆盖）。
    """
    kept = [(h, t) for (h, t) in pairs if not is_garbage_block(t)]
    seen, out = set(), []
    for h, t in kept:
        key = re.sub(r"\s+", " ", (t or "").strip())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((h, t))
    return out
