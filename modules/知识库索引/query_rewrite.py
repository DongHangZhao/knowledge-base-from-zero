#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""query_rewrite — 查询改写三层（L0 确定性 / L1 去口语 / L2 HyDE），零依赖

为什么做（#39）：三链路召回的上限是「查询本身长什么样」。用户问
「怎么搭建个人知识库和它和 wiki 有啥区别」，字面路只能命中含全句的块；
把它拆成「搭建个人知识库」「wiki 区别」两路召回再融合，命中率明显不同。
而 HyDE（用假设答案去检索）只在**有 LLM 出口**时才有意义，所以默认关。

三层设计（每层都能单独关，且关掉就退化回原行为）：

    off : 原文 + 别名扩展（= 改造前行为，逐位一致；**当前默认**，见 DEFAULT_LEVEL 注释）
    l0  : 别名扩展 → 按分隔符拆 2~3 条子查询 → 融合时按权重加权（默认关，实测无正收益）
    l1  : l0 + "去口语"派生子查询（剥"请问/怎么/如何/吗/？"）
    l2  : l1 + 可选 HyDE（**必须** KB_HYDE=1 且有 KB_HYDE_CMD 才会调外部命令）

🔴 三条硬边界：
  1. **纯函数优先**：`split_subqueries` / `strip_question` / `normalize` 不碰网络、
     不碰 DB、不读时钟 —— 同一输入同一输出，可离线单测。
  2. **HyDE 只走外部命令 + 硬超时**：本模块不 import 任何 LLM SDK（零绑定）。
     `subprocess.run(timeout=)` 保证不会把检索拖死；任何异常/超时/空输出 → `None`
     → 上层按 L1 继续检索（"失败即退化"，绝不因为改写失败而搜不出东西）。
  3. **权重口径唯一**：首条子查询恒为 1.0，其余 `KB_REWRITE_FANOUT_W`（默认 0.6）。
     改写在**召回**侧加权，**精排与查询日志永远用原始 query**（见 kb_rag 注释）。

用法：
    python query_rewrite.py --query "怎么搭建个人知识库 和 wiki 有啥区别" --level l0
    python query_rewrite.py --query "..." --level l2 --json

作者：FallenPetal ｜ 许可：KBL-1.0（非商用，商用见 COMMERCIAL.md）
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field

sys.stdout.reconfigure(encoding="utf-8")

#: 档位名 → 内部级别（数值越大越"重"）
LEVELS = {"off": 0, "l0": 1, "l1": 2, "l2": 3}
# 🔴 默认 off（2026-11-03 实测回滚，**不要再改成 l0**）：
#    100 条人工标注集跑三档，L0 在任何权重下都不是正收益 ——
#         off 基线 R@5 87.0% / MRR 0.7675
#         l0 w=0.6    R@5 86.0% / MRR 0.7592（-1pp，救回 0 条、弄坏 1 条）
#         l0 w=1.0    R@5 87.0% / MRR 0.7600（打平，但 NDCG 仍低 0.6pp，慢 20ms）
#    弄坏的那条是「长上下文 和 RAG 区别」—— 拆开后后半边权重被压，
#    把原本 Top5 内的 gt 挤出去。对比句式要的是**两半都召回**，不是降权。
#    代码保留：确有多跳/并列诉求时用 `KB_REWRITE=l0` 显式开，可随时回退。
DEFAULT_LEVEL = "off"
DEFAULT_FANOUT_W = 0.6
DEFAULT_HYDE_TIMEOUT = 8.0
MAX_SUBQUERIES = 3
MIN_SPLIT_LEN = 8             # 总长 < 8 字不拆（防「血透」「茻」这类短查询被切碎）
MIN_SEG_LEN = 2               # 每段 < 2 字不拆（防「和而不同」被切成「和」/「而不同」）

# 分隔符优先级：**显式分隔符在前，"和" 放最后**。
# 理由：中文里「和」既可能是并列连词，也可能是词的一部分（和而不同 / 和平 / 和谐），
# 放在最后意味着只有在其它分隔符都切不动时才尝试它，误切面最小。
_SEPARATORS = ("|", " vs. ", " vs ", " VS ", "以及", "、", "/", "和")

# 去口语：前缀客套、前置疑问词、后置语气词
_POLITE_PREFIXES = ("请问", "帮我看看", "帮我", "帮忙", "我想知道", "想知道", "想问一下", "想问")
_WH_PREFIXES = ("为什么", "是什么", "什么是", "怎么", "如何", "为啥", "哪些", "哪个", "多少")
_TAIL_PARTICLES = ("吗", "呢", "吧", "？", "?", "。", "！", "!")


@dataclass
class Plan:
    """一次查询的改写结果（不可变语义，调用方只读）。"""

    original: str
    subqueries: list = field(default_factory=list)   # [(text, weight), ...]
    hyde: str = None                                  # 假设答案（仅 l2 且开关开时非 None）
    level: int = 0                                    # 0=off 1=l0 2=l1 3=l2
    notes: list = field(default_factory=list)

    def as_dict(self):
        return {"original": self.original,
                "subqueries": [{"q": t, "w": w} for t, w in self.subqueries],
                "hyde": self.hyde, "level": self.level, "notes": list(self.notes)}


# ============ 纯函数 ============

def _norm_level(level):
    """把 `off/l0/l1/l2` / 0..3 / None 归一成内部级别；非法值回落 l0。"""
    if level is None:
        return LEVELS[DEFAULT_LEVEL]
    if isinstance(level, int):
        return max(0, min(3, level))
    s = str(level).strip().lower()
    if s in LEVELS:
        return LEVELS[s]
    if s.isdigit():
        return max(0, min(3, int(s)))
    return LEVELS[DEFAULT_LEVEL]


def level_name(level):
    """内部级别 → 档位名（日志/note 可读）。"""
    for name, lv in LEVELS.items():
        if lv == int(level):
            return name
    return DEFAULT_LEVEL


def split_subqueries(q):
    """按分隔符把查询拆成 2~3 条子查询；不满足条件时返回 `[q]`（原文）。

    拆的条件（双阈值，缺一不拆）：
      · 原文长度 >= `MIN_SPLIT_LEN`（8 字）—— 短查询是"字面查找"意图，拆了就废；
      · 切出 2~3 段，且**每段** >= `MIN_SEG_LEN`（2 字）。

    反例（必须不拆）：「和而不同」（4 字，长度不够）。
    """
    q = (q or "").strip()
    if len(q) < MIN_SPLIT_LEN:
        return [q] if q else []
    for sep in _SEPARATORS:
        if sep not in q:
            continue
        parts = [p.strip(" ,，、。?？") for p in q.split(sep)]
        parts = [p for p in parts if p]
        if not (2 <= len(parts) <= MAX_SUBQUERIES):
            continue
        if any(len(p) < MIN_SEG_LEN for p in parts):
            continue
        return parts
    return [q]


def strip_question(q):
    """剥掉口语外壳（客套前缀 / 前置疑问词 / 后置语气词），保留关键词与顺序。

    只做**首尾**剥离，不动中间 —— 全文替换会把「怎么」这类词从正文关键词里误删
    （如「知识库怎么建」里的「怎么」是疑问词，而「怎么做饭」里的「做」是关键词）。
    """
    s = (q or "").strip()
    for p in _POLITE_PREFIXES:
        if s.startswith(p):
            s = s[len(p):].strip(" ，,、：:")
            break
    for w in _WH_PREFIXES:
        if s.startswith(w):
            rest = s[len(w):].lstrip(" 的了")
            if len(rest) >= MIN_SEG_LEN:      # 剥完太短就别剥（防只剩一个字）
                s = rest.strip()
            break
    changed = True
    while changed:
        changed = False
        for suf in _TAIL_PARTICLES:
            if s.endswith(suf) and len(s) - len(suf) >= MIN_SEG_LEN:
                s = s[: -len(suf)].rstrip(" ，,、")
                changed = True
    return s.strip()


def normalize(plan_obj, fanout_w=DEFAULT_FANOUT_W):
    """权重归一：首条 1.0，其余 `fanout_w`；顺手去重与截断到 MAX_SUBQUERIES。"""
    try:
        fw = float(fanout_w)
    except (TypeError, ValueError):
        fw = DEFAULT_FANOUT_W
    fw = max(0.0, min(1.0, fw))
    seen, subs = set(), []
    for text, _w_old in (plan_obj.subqueries or []):
        t = (text or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        subs.append((t, 1.0 if not subs else fw))
    if not subs:
        subs = [((plan_obj.original or "").strip(), 1.0)]
    plan_obj.subqueries = subs[:MAX_SUBQUERIES]
    return plan_obj


def hyde(q, cmd, timeout=DEFAULT_HYDE_TIMEOUT):
    """跑外部命令生成「假设答案」。任何异常/非零退出/空输出 → None（不抛）。

    Args:
        q: 用户原始查询（作为子进程 stdin）。
        cmd: 命令行字符串（`KB_HYDE_CMD`），如 `python my_llm.py --hyde`。
        timeout: 硬超时秒数（默认 8s）。**必须**有，否则一个卡住的命令能把检索拖死。

    Returns:
        str（去空行后前 600 字）或 None。
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return None
    try:
        proc = subprocess.run(shlex.split(cmd), input=q, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=float(timeout))
    except Exception:            # 含 TimeoutExpired / FileNotFoundError / OSError
        return None
    if proc.returncode != 0:
        return None
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    text = "\n".join(lines).strip()
    return text[:600] if text else None


# ============ 编排 ============

def plan(q, level=None, alias_fn=None, env=None):
    """生成改写计划（纯函数：不写库、不读盘；唯一外部动作是 l2 的 HyDE 子进程）。

    Args:
        q: 原始查询。
        level: `off|l0|l1|l2` 或 0..3；None → 读 `KB_REWRITE`，再回落 l0。
        alias_fn: 别名扩展函数（`kb_rag.expand_alias`）。**off 档也会调用它** ——
            别名扩展是改造前就有的 D5 能力，"关改写"只关新增的三层，不该顺手
            把老功能也关掉（否则 off 档就不再等于改造前行为）。
        env: 注入用的环境映射（单测传 dict）。

    Returns:
        Plan(original, subqueries[(text,w)], hyde, level, notes)
    """
    env = os.environ if env is None else env
    raw = (q or "").strip()
    lv = _norm_level(level if level is not None else (env.get("KB_REWRITE") or DEFAULT_LEVEL))
    notes = []
    if not raw:
        return Plan(raw, [], None, lv, ["查询为空"])

    base = raw
    if callable(alias_fn):
        try:
            expanded = alias_fn(raw)
            if expanded:
                base = expanded.strip()
        except Exception as e:                 # 别名表坏了不该让检索失败
            notes.append("别名扩展失败，已忽略: %s" % str(e)[:60])

    if lv == 0:                                # ---- off：与改造前逐位一致 ----
        return Plan(raw, [(base, 1.0)], None, 0, notes)

    fanout_w = env.get("KB_REWRITE_FANOUT_W", DEFAULT_FANOUT_W)
    parts = split_subqueries(base)
    if len(parts) > 1:
        notes.append("L%d 拆分：%d 条子查询" % (lv, len(parts)))
    subs = [(p, 1.0) for p in parts]

    if lv >= 2:                                # ---- l1：去口语派生 ----
        stripped = strip_question(raw)
        if stripped and stripped not in [t for t, _ in subs] and len(stripped) >= MIN_SEG_LEN:
            subs.append((stripped, 1.0))
            notes.append("L1 去口语子查询：%s" % stripped[:40])

    hyde_text = None
    if lv >= 3:                                # ---- l2：HyDE（默认关）----
        on = str(env.get("KB_HYDE") or "0").strip().lower() in ("1", "true", "yes", "on")
        if not on:
            notes.append("KB_HYDE=0：L2 退化为 L1（HyDE 默认关）")
        else:
            cmd = (env.get("KB_HYDE_CMD") or "").strip()
            if not cmd:
                notes.append("HyDE 回落：KB_HYDE=1 但未配置 KB_HYDE_CMD，按 L1 检索")
            else:
                hyde_text = hyde(raw, cmd, env.get("KB_HYDE_TIMEOUT", DEFAULT_HYDE_TIMEOUT))
                if hyde_text:
                    notes.append("HyDE 命中：%d 字假设答案并入 vec 路" % len(hyde_text))
                else:
                    notes.append("HyDE 回落：外部命令失败/超时/空输出，按 L1 检索")

    p = Plan(raw, subs, hyde_text, lv, notes)
    return normalize(p, fanout_w)


def plan_from_env(q, alias_fn=None, env=None):
    """便捷入口：完全按环境变量（`KB_REWRITE` 等）出计划。"""
    return plan(q, level=None, alias_fn=alias_fn, env=env)


def main(argv=None):
    ap = argparse.ArgumentParser(description="查询改写三层（off/l0/l1/l2）")
    ap.add_argument("--query", required=True)
    ap.add_argument("--level", default=None, choices=sorted(LEVELS), help="默认读 KB_REWRITE，再回落 l0")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    alias_fn = None
    try:                                  # 有别名表就带上，没有也不影响（纯函数可单测）
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import kb_rag
        alias_fn = kb_rag.expand_alias
    except Exception:
        alias_fn = None

    p = plan(args.query, level=args.level, alias_fn=alias_fn)
    if args.json:
        print(json.dumps(p.as_dict(), ensure_ascii=False, indent=2))
    else:
        print("原始：%s" % p.original)
        print("档位：%s(L%d)" % (level_name(p.level), p.level))
        for i, (t, w) in enumerate(p.subqueries, 1):
            print("  子查询%d (w=%.2f)：%s" % (i, w, t))
        if p.hyde:
            print("HyDE：%s" % p.hyde[:120])
        for n in p.notes:
            print("  · %s" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
