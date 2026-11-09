"""B3 有类型边 · LLM 质检（默认 off）。

规则版从文本里抽 41 条 typed 边，但实体切分会产生碎片（'也不该' -- 'AI' 构成），
只看三元组不读上下文也能筛掉一部分。这是 LLM 在 GraphRAG 里的真实用法之一（抽取后校验），
不是「拿模型替代规则」，而是**规则出候选、模型做质检、人看统计**。

    python b3_review.py                 # 默认 off：全部回落为 rule 判定，标 degraded
    KG_LLM_ENABLED=1 python b3_review.py --limit 12

输出：state/b3_llm_review_<时间>.json
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import llm_kit

MOD = Path(__file__).resolve().parent.parent
GRAPH = MOD / "state" / "kg_w6.pkl"
STATE = MOD / "state"

_SYS = (
    "你是知识图谱质检员。输入一条三元组（主语-关系-宾语），判断它是否构成有效知识。"
    "有效 = 主语和宾语都是完整的实体（名词性、可指代），关系明确；"
    "无效 = 任一侧是虚词/片段/半句（如「也不该」「当理」），或关系只是字面撞词。"
    "只输出 JSON，不要解释、不要代码块："
    '{"valid": true/false, "type": "属于|用于|依赖|导致|包含|相关", "confidence": 0到1, "why": "不超过20字"}'
)


def load_typed(limit: int) -> list[dict]:
    with GRAPH.open("rb") as fh:
        g = pickle.load(fh)
    rows = [
        {"head": str(u), "rel": str(d.get("rel") or ""), "tail": str(v),
         "w": float(d.get("w") or 0)}
        for u, v, d in g.edges(data=True) if d.get("kind") == "typed"
    ]
    rows.sort(key=lambda r: -r["w"])
    return rows[:limit]


def review(row: dict) -> dict:
    prompt = f"主语：{row['head']}\n关系：{row['rel']}\n宾语：{row['tail']}"
    res = llm_kit.chat(prompt, system=_SYS, task="edge_valid", max_tokens=220)
    if not res["ok"]:
        # 降级：不在模型缺席时假装判定过，明确标 rule_fallback
        return {**row, "valid": None, "type": None, "confidence": None,
                "why": "", "source": "rule_fallback", "degraded": True,
                "reason": res["reason"]}
    try:
        obj = llm_kit.extract_json(res["text"])
        if not isinstance(obj, dict):
            raise ValueError("not a dict")
        return {**row, "valid": bool(obj.get("valid")), "type": obj.get("type"),
                "confidence": float(obj.get("confidence", 0)), "why": str(obj.get("why", "")),
                "source": "llm", "degraded": False, "reason": "ok"}
    except (ValueError, TypeError, AttributeError) as exc:
        # 坏结果作废缓存，避免下次命中同一份坏文本
        llm_kit.cache_drop(res.get("cache_key", ""))
        return {**row, "valid": None, "type": None, "confidence": None,
                "why": "", "source": "rule_fallback", "degraded": True,
                "reason": f"parse: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    total = len(load_typed(10 ** 6))
    rows = [review(r) for r in load_typed(args.limit)]
    ok = [r for r in rows if not r["degraded"]]
    invalid = [r for r in ok if r["valid"] is False]

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "graph_typed_total": total,
        "sampled": len(rows),
        "llm_ok": len(ok),
        "degraded": len(rows) - len(ok),
        "invalid_found": len(invalid),
        "enabled": llm_kit.settings()["enabled"],
        "note": f"抽样 {len(rows)}/{total} 只验证接入链路，不作质量结论；判定仅供人审参考",
        "rows": rows,
    }
    STATE.mkdir(parents=True, exist_ok=True)
    out = STATE / f"b3_llm_review_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "rows"},
                     ensure_ascii=False, indent=2))
    print(f"逐条：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
