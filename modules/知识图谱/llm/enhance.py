"""W6 图谱增强任务层：有类型关系判定 + 查询改写。

设计原则（准则 §39.2 判据 3）：**先有可复现的本地基线，LLM 是在基线上加的能力**。
任何一次调用失败（disabled / no key / 超时 / 429 / 出口被拦）都回落到本地规则，
调用方永远拿到一个结构合法的结果，只是 `source` 变成 `rule`、`degraded=True`。
"""
from __future__ import annotations

import json
import re

import llm_kit

# ---------------------------------------------------------------- 本地基线
# 关系类型：属于 / 用于 / 依赖 / 导致 / 包含 / 相关
_RULES = [
    ("用于", r"用于|用来|应用于|服务于|作用于|面向"),
    ("依赖", r"依赖|基于|依靠|依托|建筑在|构建于"),
    ("导致", r"导致|引起|造成|引发|带来|结果是"),
    ("包含", r"包含|包括|涵盖|由.*组成|分为"),
    ("属于", r"属于|是.*的一部分|归类为|算作"),
]

_STOP = set("的了和与及是在对把被为以于其这那请问怎么如何什么哪些")


def rule_relation(head: str, phrase: str, tail: str) -> dict:
    for label, pat in _RULES:
        if re.search(pat, phrase):
            return {"type": label, "confidence": 0.6, "source": "rule", "degraded": False}
    return {"type": "相关", "confidence": 0.3, "source": "rule", "degraded": False}


def rule_rewrite(query: str) -> dict:
    """本地兜底：原式 + 去停用词式 + 相邻二字切分。够用，不追求好看。"""
    q = query.strip()
    stripped = "".join(ch for ch in q if ch not in _STOP) or q
    pairs = [q[i:i + 2] for i in range(0, max(len(q) - 1, 1)) if q[i:i + 2].strip()]
    out = []
    for cand in (q, stripped, " ".join(pairs[:6])):
        if cand and cand not in out:
            out.append(cand)
    return {"queries": out, "source": "rule", "degraded": False}


# ------------------------------------------------------------------ LLM 版
_SYS_REL = (
    "你是知识图谱的关系抽取器。只输出 JSON，不要解释、不要 markdown 代码块。"
    '格式：{"type": "属于|用于|依赖|导致|包含|相关", "confidence": 0到1的小数}。'
    "判断依据只能是给定的主语、关系短语、宾语；信息不足就给「相关」并给低置信度。"
    "三条硬规则：① 关系短语含否定（不用于/无需/不必/并非）时一律判「相关」且置信度≤0.4；"
    "② 短语表达「A 使/让/令 B 出现某后果」即按「导致」处理，不要求出现「导致」二字；"
    "③ 「基于」只表示依据来源而非运行依赖（如「基于时间顺序排列」）时判「相关」。"
)

_SYS_REW = (
    "你是检索查询改写器。只输出 JSON 数组，不要解释。"
    '格式：["改写式1", "改写式2", "改写式3"]。'
    "要求：保留原意与专有名词，补全省略的主体，每条不超过 30 字，不要编造原文没有的实体。"
)


def _parse_json(text: str):
    return llm_kit.extract_json(text)


def llm_relation(head: str, phrase: str, tail: str, context: str = "") -> dict:
    base = rule_relation(head, phrase, tail)
    prompt = f"主语：{head}\n关系短语：{phrase}\n宾语：{tail}"
    if context:
        prompt += f"\n上下文（脱敏后）：{context[:300]}"
    res = llm_kit.chat(prompt, system=_SYS_REL, task="relation", max_tokens=120)
    if not res["ok"]:
        return {**base, "degraded": True, "reason": res["reason"]}
    try:
        obj = _parse_json(res["text"])
        label = str(obj.get("type", "")).strip()
        if label not in {"属于", "用于", "依赖", "导致", "包含", "相关"}:
            raise ValueError(f"非法类型 {label!r}")
        conf = float(obj.get("confidence", 0.7))
        return {"type": label, "confidence": round(max(0.0, min(1.0, conf)), 3),
                "source": "llm", "degraded": False,
                "agree_with_rule": label == base["type"], "reason": "ok"}
    except (ValueError, KeyError, TypeError) as exc:
        return {**base, "degraded": True, "reason": f"parse: {exc}"}


def llm_rewrite(query: str) -> dict:
    base = rule_rewrite(query)
    res = llm_kit.chat(f"原查询：{query}", system=_SYS_REW, task="rewrite", max_tokens=200)
    if not res["ok"]:
        return {**base, "degraded": True, "reason": res["reason"]}
    try:
        arr = _parse_json(res["text"])
        if not isinstance(arr, list):
            raise ValueError("not a list")
        qs = [str(x).strip() for x in arr if str(x).strip()]
        if not qs:
            raise ValueError("empty")
        merged = list(dict.fromkeys([query] + qs))[:4]
        return {"queries": merged, "source": "llm", "degraded": False, "reason": "ok"}
    except (ValueError, KeyError, TypeError) as exc:
        return {**base, "degraded": True, "reason": f"parse: {exc}"}
