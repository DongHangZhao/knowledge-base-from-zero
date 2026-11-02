#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_test_rerank_fallback — rerank 低分降级回归（2026-10-23 judged set 实证修复）
==========================================================================

背景：judged set 100 条基线（reports/2026-10-23-judged-set基线.md）实证——
rerank 对本库口语化中文查询系统性打分偏低，34 条零命中里 33 条是
「低分硬清空」造成的（库里明明有，无 rerank 时 gt 多排第 1-2）。

🔴 2026-10-23 晚间二次定性：上面旧注释叫它「阈值误杀」，这个说法被推翻了。
   33 条不是「阈值定太高」，根因两层，调阈值救不了——
   ① 精排输入退化：打分对象是「文档前 120 字 summary」不是命中块（
      `search_bge`/`search_fts_chunks` 都不返正文，
      `bge_reranker._text_of()` 取值链 chunk_text → summary → title 回落到 summary）；
   ② 低分硬清空：全池最高分 < KB_RERANK_MIN 时直接清空。
   实证 gt 分 0.0000/0.0002/0.0001/0.0011 而 top1 分 0.9936/0.7787
   —— 精排是「自信地认错人」，不是「没把握」。
   关精排后 R@5 68.0%→82.0%、冷查询 8.6–14.5s→0.13–0.32s。
   ⇒ 出路是修输入（清单 B2），调 KB_RERANK_MIN 已实测否决。
原逻辑「best < min_score 就清空结果」违反「库里有的不许说没有」铁约束，
已改为「低于阈值→降级返回 RRF 融合原序 + note 标注低置信」。

本测试三条断言，全真跑（不 mock）：
  A. 降级路径：阈值调到 0.9（必触发降级）→ 结果非空、顺序 == RRF 原序
     （与 rerank=False 完全一致）、note 含「区分度不足」、不带 rerank_score
  B. 正常路径：阈值 0（永不降级）→ 结果带 rerank_score、note 不含降级语
  C. 真实案例回归：「开窗函数怎么写 分组排名第几」（judged set 误杀案例，
     最高分 0.0083）默认阈值下必须返回结果且 gt 在内——防「清空」复发
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb_index
import kb_rag

PASS = []


def check(name, cond, detail=""):
    PASS.append(cond)
    if isinstance(detail, (list, tuple)):
        detail = " / ".join(str(x) for x in detail)
    print("%s %s%s" % ("✅" if cond else "❌", name, " → " + str(detail) if detail else ""))
    return cond


def run(q, topk=5, min_env=None, rerank=True):
    """跑一次 search_unified，返回 (results, note)。min_env 控制 KB_RERANK_MIN。"""
    old = os.environ.get("KB_RERANK_MIN")
    if min_env is None:
        os.environ.pop("KB_RERANK_MIN", None)
    else:
        os.environ["KB_RERANK_MIN"] = str(min_env)
    try:
        conn = kb_index.connect()
        try:
            return kb_rag.search_unified(conn, q, topk=topk, mode="fused", rerank=rerank)
        finally:
            conn.close()
    finally:
        if old is None:
            os.environ.pop("KB_RERANK_MIN", None)
        else:
            os.environ["KB_RERANK_MIN"] = old


def paths(res):
    return [r.get("path") for r in res]


def env_gate():
    """本套件需要「真模型 + 真索引」，两者都缺时必须**显式跳过**。

    🔴 为什么允许跳过（2026-11-03，T01 测试止血）：本测试 A/B/C 三段全是**真跑**，
       不 mock ——
         · B 段断言结果带 `rerank_score`，这要求 cross-encoder 真推理（torch）；
         · C 段断言真实库里某篇 SQL 文档被召回，这要求本机有已构建的 state/kb.db。
       CI 只装 jieba+numpy（**绝不装 torch**）、工作区里也没有 state/kb.db，
       所以它在那里**不可能**通过。留着必红 ≠ 严谨，只会训练所有人忽略红灯
       （这正是本次测试止血要治的病）。故按下述三个条件如实跳过，并打印
       `[PASS] skipped-...`（run_tests.py:70 的正则认得这个格式）。

    跳过条件（任一不满足即跳）：
      ① 能 import torch（rerank 无纯 Python 降级实现）
      ② models/bge-reranker-v2-m3/config.json 存在
      ③ 当前 KB_DB_PATH 指向的库存在且 chunks > 0

    本机要真跑：`E:/_venvs/gpu/Scripts/python.exe _test_rerank_fallback.py`
    """
    reasons = []
    try:
        import torch  # noqa: F401
    except Exception:
        reasons.append("torch 未安装（rerank 无纯 Python 降级路径）")
    import bge_reranker
    if not os.path.isfile(os.path.join(bge_reranker.MODEL_DIR, "config.json")):
        reasons.append("精排模型缺失：%s" % bge_reranker.MODEL_DIR)
    db = kb_index.DB_PATH
    if not os.path.exists(db):
        reasons.append("索引库不存在：%s" % db)
    else:
        try:
            conn = kb_index.connect(readonly=True)
            try:
                n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            finally:
                conn.close()
            if not n:
                reasons.append("索引库为空（chunks=0）：%s" % db)
        except Exception as e:
            reasons.append("索引库不可读：%s" % str(e)[:80])
    return reasons


def main():
    q = "开窗函数怎么写 分组排名第几"

    why = env_gate()
    if why:
        for w in why:
            print("[PASS] skipped-rerank-env：%s" % w)
        print("[PASS] skipped-rerank-env：本套件需真模型 + 真索引，"
              "已在无 torch / 无索引环境跳过（不是假的绿：本机 GPU venv 必跑）")
        print()
        print("✅ rerank 降级回归：skipped（环境不满足）")
        return 0

    print("=== A. 降级路径（KB_RERANK_MIN=0.9 必触发）===")
    res_fb, note_fb = run(q, min_env=0.9)
    res_rrf, _ = run(q, rerank=False)
    check("降级后结果非空", len(res_fb) > 0, "n=%d" % len(res_fb))
    check("降级顺序 == RRF 原序", paths(res_fb) == paths(res_rrf),
          "%s vs %s" % (paths(res_fb)[:2], paths(res_rrf)[:2]))
    check("note 标注低置信", "区分度不足" in (note_fb or ""), (note_fb or "")[:60])
    check("降级结果不带 rerank_score", all("rerank_score" not in r for r in res_fb))

    print("=== B. 正常路径（KB_RERANK_MIN=0 永不降级）===")
    res_ok, note_ok = run(q, min_env=0.0)
    has_rr = any("rerank_score" in r for r in res_ok)
    check("正常路径带 rerank_score", has_rr)
    check("正常路径 note 不含降级语", "区分度不足" not in (note_ok or ""), (note_ok or "")[:60])

    print("=== C. 真实案例回归（默认阈值 0.05）===")
    res_def, note_def = run(q)
    check("默认阈值下结果非空", len(res_def) > 0, "n=%d" % len(res_def))
    gt_hit = any("SQL窗口函数完全指南" in (p or "") for p in paths(res_def))
    check("gt 在结果中（库里有的不许说没有）", gt_hit, paths(res_def)[:1])

    print()
    ok = all(PASS)
    print("%s rerank 降级回归：%d/%d 断言通过" % ("✅" if ok else "❌", sum(PASS), len(PASS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
