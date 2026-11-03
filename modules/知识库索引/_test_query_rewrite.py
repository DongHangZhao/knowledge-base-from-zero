#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_query_rewrite — 查询改写三层的离线单测（零依赖、不联网、不碰 DB）

守什么（#39 的每一条"退化点"）：
  ① **短查询不拆**：「茻」「血透」是字面查找意图，l0/l1/l2 三档都必须只有 1 条子查询，
     且文本 == 原文（拆了就把"一个字也要搜得到"废掉）。
  ② 权重口径：首条恒 1.0，其余 `KB_REWRITE_FANOUT_W`（默认 0.6）。
  ③ **中文误切反例**：「和而不同」不能因为含「和」被切开（双阈值：总长>=8 且每段>=2 字）。
  ④ 别名扩展在 off 档也生效（D5 是老能力，关改写不该顺手关掉它）。
  ⑤ `strip_question` 只剥首尾口语壳：「怎么搭建个人知识库？」→ 不含「怎么」「？」。
  ⑥ HyDE：假命令成功 → 返回文本；超时 → **None 且不抛**；`KB_HYDE=0` → **一次都不调用**
     外部命令（用计数脚本钉死，防"默认关"变成"默认偷偷跑"）。
  ⑦ off 档 = 改造前行为（单子查询 + 原文 + 权重 1.0）。
  ⑧ 纯函数：同一输入两次计划完全相同；非法档名回落 l0 不抛。

跑法：python _test_query_rewrite.py     （纯 stdlib，无 numpy/torch/网络）
退出码：0 = 全 PASS；1 = 有 FAIL
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import query_rewrite as Q  # noqa: E402

PASS = 0
FAIL = 0
TMP = tempfile.mkdtemp(prefix="kbqr_")


def check(name, cond, detail=""):
    """打一条 [PASS]/[FAIL] —— run_tests.py:70 的正则依赖这个格式，不许改。"""
    global PASS, FAIL
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                         ("  ｜ " + str(detail)) if detail != "" else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def _win(path):
    """shlex.split 在 posix 模式会吃掉反斜杠 —— 传命令时统一用正斜杠。"""
    return path.replace("\\", "/")


def _mk_script(name, body):
    p = os.path.join(TMP, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return _win(p)


def main():
    print("== 查询改写三层（query_rewrite）==")

    # ---------- ① 短查询不拆（三档全钉） ----------
    for lv in ("l0", "l1", "l2"):
        p = Q.plan("血透", level=lv, env={})
        check("① 短查询「血透」在 %s 档不拆（1 条 + 原文）" % lv,
              len(p.subqueries) == 1 and p.subqueries[0][0] == "血透",
              p.subqueries)
    p1 = Q.plan("茻", level="l2", env={})
    check("①b 单字查询「茻」不拆", len(p1.subqueries) == 1 and p1.subqueries[0][1] == 1.0,
          p1.subqueries)

    # ---------- ② A vs B → 2 条 + 权重 [1.0, 0.6] ----------
    p = Q.plan("sqlite 全文检索 vs 向量检索", level="l0", env={})
    ws = [w for _t, w in p.subqueries]
    check("② 「A vs B」拆成 2 条", len(p.subqueries) == 2, p.subqueries)
    check("②b 权重 = [1.0, 0.6]", ws == [1.0, 0.6], ws)
    check("②c 首条子查询 = 原文首段（不含分隔符）",
          "vs" not in p.subqueries[0][0] and "向量检索" in p.subqueries[1][0],
          p.subqueries)

    # 自定义 fanout 权重
    p = Q.plan("sqlite 全文检索 vs 向量检索", level="l0",
               env={"KB_REWRITE_FANOUT_W": "0.3"})
    check("②d KB_REWRITE_FANOUT_W 可调（0.3）", [w for _t, w in p.subqueries] == [1.0, 0.3],
          p.subqueries)

    # ---------- ③ 中文误切反例 ----------
    check("③ 「和而不同」不因含「和」被切开",
          Q.split_subqueries("和而不同") == ["和而不同"], Q.split_subqueries("和而不同"))
    check("③b 短查询整体返回原文（不拆）",
          Q.split_subqueries("血透") == ["血透"])
    # 正常并列：应拆
    sp = Q.split_subqueries("知识库索引 和 向量召回怎么配合")
    check("③c 长查询按「和」正常拆分（2 段）", len(sp) == 2, sp)
    # 段太短则不拆：「如何和为什么」→ 段 "如何"/"为什么" 都 >=2 → 会拆；用 1 字段做反例
    check("③d 出现 1 字段时不拆（每段必须 >=2 字）",
          Q.split_subqueries("知识库索引和x和向量召回怎么配合") == ["知识库索引和x和向量召回怎么配合"],
          Q.split_subqueries("知识库索引和x和向量召回怎么配合"))

    # ---------- ④ 别名扩展（off 档也生效） ----------
    alias_json = os.path.join(TMP, "alias.json")
    with open(alias_json, "w", encoding="utf-8") as f:
        f.write('{"rrf": ["倒数排名融合", "reciprocal rank fusion"]}')

    def fake_alias(q):
        return q + " 倒数排名融合" if "rrf" in q.lower() else q

    p_off = Q.plan("rrf 怎么调", level="off", alias_fn=fake_alias, env={})
    check("④ off 档仍做别名扩展（D5 老行为）",
          "倒数排名融合" in p_off.subqueries[0][0], p_off.subqueries)
    check("④b off 档只有 1 条子查询、权重 1.0",
          len(p_off.subqueries) == 1 and p_off.subqueries[0][1] == 1.0, p_off.subqueries)
    check("④c off 档 level == 0", p_off.level == 0, p_off.level)

    # ---------- ⑤ 去口语 ----------
    s = Q.strip_question("怎么搭建个人知识库？")
    check("⑤ strip_question 剥离「怎么」与「？」",
          "怎么" not in s and "？" not in s and "搭建个人知识库" in s, s)
    s2 = Q.strip_question("请问 RRF 和 加权融合有什么区别吗")
    check("⑤b strip_question 剥离客套前缀与语气词",
          s2.startswith("RRF") and not s2.endswith("吗"), s2)
    p_l1 = Q.plan("怎么搭建个人知识库？", level="l1", env={})
    texts = [t for t, _ in p_l1.subqueries]
    check("⑤c l1 多出「去口语」子查询", len(texts) >= 2 and any("怎么" not in t for t in texts),
          texts)

    # ---------- ⑥ HyDE：成功 / 超时 / 默认关 ----------
    ok_script = _mk_script("hyde_ok.py", "import sys\nsys.stdin.read()\nprint('假设答案' * 5)\n")
    got = Q.hyde("测试查询", '"%s" "%s"' % (_win(sys.executable), ok_script), timeout=10)
    check("⑥ HyDE 假命令成功 → 返回文本", bool(got) and "假设答案" in got, (got or "")[:30])

    sleep_script = _mk_script("hyde_slow.py", "import time\ntime.sleep(5)\nprint('late')\n")
    t0 = time.time()
    got2 = Q.hyde("测试查询", '"%s" "%s"' % (_win(sys.executable), sleep_script), timeout=0.5)
    el = time.time() - t0
    check("⑥b HyDE 超时 → None 且不抛", got2 is None and el < 4.0, "%.1fs" % el)

    bad_script = _mk_script("hyde_bad.py", "import sys\nsys.exit(3)\n")
    check("⑥c HyDE 非零退出 → None",
          Q.hyde("x", '"%s" "%s"' % (_win(sys.executable), bad_script), timeout=10) is None)
    check("⑥d HyDE 空命令 → None", Q.hyde("x", "", 10) is None)

    # 计数脚本：KB_HYDE=0 时一次都不许调用
    counter = os.path.join(TMP, "hyde_calls.txt")
    count_script = _mk_script("hyde_count.py",
                              "import sys\nsys.stdin.read()\n"
                              "open(r'%s','a').write('1')\nprint('假设答案')\n" % counter)
    cmd = '"%s" "%s"' % (_win(sys.executable), count_script)

    Q.plan("测试 l2 默认关", level="l2", env={"KB_HYDE": "0", "KB_HYDE_CMD": cmd})
    check("⑥e KB_HYDE=0 → 外部命令调用 0 次（默认关是真的关）",
          not os.path.exists(counter))

    p_l2 = Q.plan("测试 l2 打开", level="l2",
                  env={"KB_HYDE": "1", "KB_HYDE_CMD": cmd, "KB_HYDE_TIMEOUT": "10"})
    n_calls = len(open(counter, encoding="utf-8").read()) if os.path.exists(counter) else 0
    check("⑥f KB_HYDE=1 → 调用 1 次且 hyde 非空", n_calls == 1 and bool(p_l2.hyde),
          "calls=%d hyde=%s" % (n_calls, (p_l2.hyde or "")[:10]))

    p_l2_fail = Q.plan("测试 l2 失败", level="l2",
                       env={"KB_HYDE": "1", "KB_HYDE_CMD": "definitely-not-a-command-xyz"})
    check("⑥g HyDE 命令不存在 → hyde=None 且 note 含「HyDE 回落」",
          p_l2_fail.hyde is None and any("HyDE 回落" in n for n in p_l2_fail.notes),
          p_l2_fail.notes)

    # ---------- ⑦ normalize / 去重 / 上限 ----------
    p3 = Q.Plan("a", [("x", 0.9), ("y", 0.9), ("x", 0.9), ("", 0.9)], None, 1, [])
    Q.normalize(p3, 0.6)
    check("⑦ normalize：首条 1.0、其余 0.6、去重去空",
          p3.subqueries == [("x", 1.0), ("y", 0.6)], p3.subqueries)
    p4 = Q.Plan("q", [("a", 1.0), ("b", 1.0), ("c", 1.0), ("d", 1.0), ("e", 1.0)], None, 1, [])
    Q.normalize(p4, 0.6)
    check("⑦b 最多 3 条子查询", len(p4.subqueries) == Q.MAX_SUBQUERIES, len(p4.subqueries))

    # ---------- ⑧ 纯函数 / 非法档位 ----------
    a = Q.plan("知识库 和 wiki 有啥区别", level="l0", env={}).as_dict()
    b = Q.plan("知识库 和 wiki 有啥区别", level="l0", env={}).as_dict()
    check("⑧ 纯函数：同输入两次计划完全相同", a == b)
    # 非法档名回落到 DEFAULT_LEVEL。2026-11-03 默认由 l0 改 off 后，断言跟着改：
    # 实测 L0 在 100 条集上无正收益（见 query_rewrite.DEFAULT_LEVEL 注释），
    # 所以「打错档名」的安全兜底应该是「什么都不做」而不是「悄悄开改写」。
    check("⑧b 非法档名回落 off（level==0，= DEFAULT_LEVEL）",
          Q._norm_level("no-such") == Q.LEVELS[Q.DEFAULT_LEVEL], Q._norm_level("no-such"))
    check("⑧c off 归一成 0 / l2 归一成 3",
          (Q._norm_level("off"), Q._norm_level("l2"), Q._norm_level("OFF")) == (0, 3, 0))
    check("⑧d 空查询不抛、返回空子查询", Q.plan("", level="l0", env={}).subqueries == [])

    print("-" * 56)
    print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
    try:
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
    except Exception:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
