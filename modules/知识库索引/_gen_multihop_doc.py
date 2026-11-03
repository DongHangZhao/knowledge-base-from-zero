#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 state/kb_eval_multihop_30.json 生成 docs/decisions/KG-41-评测集说明.md

为什么要生成而不是手写：集子一改，说明里的 30 行 query 就会全部过期。
单一真相源 = JSON + 本模块 _verify_multihop_30.py 里的 ANCHOR / AUDIT 元数据。
改完集子重跑本脚本即可。

用法：
    python _gen_multihop_doc.py
"""
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.join(_HERE, "state", "kb_eval_multihop_30.json")
DB = os.path.join(_HERE, "state", "kb.db")
OUT = "F:/ZDH/kb-from-zero/docs/decisions/KG-41-评测集说明.md"

SOURCE = {  # 来源归类（人工判定，写死便于复核）
    "A": "mh01 mh02 mh03 mh04 mh05 mh06 mh07 mh09 mh10 mh12 mh13 mh24 mh25 mh26 mh27",
    "B": "mh08 mh11 mh14 mh16 mh17 mh19 mh20 mh21 mh22 mh23",
    "C": "mh15 mh18 mh28 mh29 mh30",
}


def load_meta():
    spec = importlib.util.spec_from_file_location(
        "_v", os.path.join(_HERE, "_verify_multihop_30.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.ANCHOR, m.AUDIT, m.ANCHOR_DISCLOSED


def main():
    data = json.load(open(EVAL, encoding="utf-8"))
    ANCHOR, AUDIT, DISC = load_meta()
    conn = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    titles = [r[0] for r in conn.execute("select title from docs") if r[0]]

    def content(p):
        r = conn.execute("select content from docs where path=?", (p,)).fetchone()
        return r[0] if r else ""

    def title_of(p):
        r = conn.execute("select title from docs where path=?", (p,)).fetchone()
        return r[0] if r else "（未找到）"

    # 跑一次校验拿原文
    vp = subprocess.run([sys.executable, "-X", "utf8", "_verify_multihop_30.py"],
                        cwd=_HERE, capture_output=True)
    verify_out = vp.stdout.decode("utf-8", "replace")
    # 只截取到「锚点链条表」之前的部分作为门禁原文
    head = verify_out.split("=====")[0].rstrip()

    L = []
    A = L.append
    A("# KG-41 多跳评测集说明（30 条真实多跳题 · 锚点版）")
    A("")
    A("> 产出：`F:/ZDH/知识库/modules/知识库索引/state/kb_eval_multihop_30.json`（生产侧，不入 git）")
    A("> 同步副本：`F:/ZDH/kb-from-zero/modules/知识库索引/state/kb_eval_multihop_30.json`（仓库侧，跑实验用）")
    A("> 校验：`_verify_multihop_30.py` ｜ 本文件由 `_gen_multihop_doc.py` 生成，勿手改")
    A("")
    A("---")
    A("")
    A("## 0. 为什么不能用单跳集测 1-hop")
    A("")
    A("单跳题的 gt 只有一篇，答案就在那一篇里。给这类题加 1-hop 邻居，**邻居全是噪声**：")
    A("既提不了 Recall（gt 本来就排得到），又必然拉高延迟。1-hop 的价值只在多跳：")
    A("**答案分散在两三篇文档里，单路召回只能捞到其中一篇**。")
    A("")
    A("## 1. `hops` 字段的语义")
    A("")
    A("`hops = len(gt)` = **回答这条 query 需要读几篇文档**。")
    A("")
    n2 = sum(1 for x in data if x["hops"] == 2)
    n3 = sum(1 for x in data if x["hops"] == 3)
    A("- `hops: 2` → %d 条" % n2)
    A("- `hops: 3` → %d 条" % n3)
    A("")
    A("## 2. 锚点规则（第一版曾把锚点也禁掉，导致 1-hop 全程返回空）")
    A("")
    A("`kg_gate.expand_1hop` 的机制是 `_query_entities(query)` → 查侧表里共享这些实体的 chunk。")
    A("**查询里没有实体，这一路直接返回 `[]`** —— 于是 treat 组测的是「没东西可扩」而不是「图谱没用」，")
    A("那是假阴性，比不做实验更糟（会得出一个看起来有数据支撑的错误结论）。")
    A("")
    A("第一版为了防关键词作弊，把「gt 文件名原词」全禁了，连**锚点**一起禁掉，题面变成")
    A("「那批系统里第一个上线的那套…」这种没人这么说话的句子。现行规则两句话：")
    A("")
    A("1. ✅ **第一跳的锚点实体必须出现**（系统名 / 主题名 / 上级概念）—— 这是用户已知信息，不是答案。")
    A("   第一跳的信息本来就该在提问里，图谱做的正是「第一跳 → 第二跳」这段连接。")
    A("2. ❌ **最终答案所在文档的标题、以及答案本身的关键词不许出现。**")
    A("")
    A("锚点加回来之后，首篇（枢纽文档）会被关键词路轻松命中 —— **这没关系，它就是第一跳**。")
    A("题目的难点在第二跳：得从枢纽跳到子文档才算真命中，所以每条的 gt 都保留了首篇。")
    A("")
    A("额外加了一条比 team-lead 要求更严的自检：**至少一个锚点必须出现在 gt 末篇正文里**。")
    A("理由：1-hop 是「查询实体 → 共享该实体的 chunk」，若答案 chunk 里根本没有这个实体，")
    A("1-hop 扩不过去，这条题对 treat 组仍然是零贡献 —— 那样锚点就只是装饰。")
    A("")
    A("## 3. 来源分布")
    A("")
    A("| 来源 | 条数 | 题号 | 说明 |")
    A("|---|---|---|---|")
    A("| A · 枢纽/总览型 + 下辖子文档 | %d | %s | 「总览/系统/清单」类文档只给实体清单与属性索引，细节在下辖子文档 |"
      % (len(SOURCE["A"].split()), SOURCE["A"].replace(" ", ", ")))
    A("| B · 现有漏检难例改写 | %d | %s | 取自 `state/rag_eval_report.md`「混合检索未命中样例」，挑确实需要跨两篇的，把 query 改写到答案落在另一篇 |"
      % (len(SOURCE["B"].split()), SOURCE["B"].replace(" ", ", ")))
    A("| C · 真实使用场景 | %d | %s | 按栋行真实提问习惯造，答案分散在「模块文档 + 准则/规范文档」两处 |"
      % (len(SOURCE["C"].split()), SOURCE["C"].replace(" ", ", ")))
    A("")
    A("## 4. 逐条说明")
    A("")
    A("> 「为什么多跳」= **第一篇承担什么、后面第几篇承担什么**。")
    A("> 「锚点」列里带 ⚠ 的，表示该锚点同时也是末篇标题的一部分（非等于），逐条披露在 §5 末尾。")
    A("")
    A("| 题号 | query | 锚点 | gt | hops | 为什么多跳 |")
    A("|---|---|---|---|---|---|")
    for it in data:
        anchors = ANCHOR.get(it["id"], [])
        astr = " / ".join(
            ("⚠ " + a) if it["id"] in DISC and a in DISC[it["id"]].split("「")[1].split("」")[0]
            else a for a in anchors)
        gt = "<br>".join("`%s`" % p for p in it["gt"])
        why = AUDIT[it["id"]][1]
        A("| %s | %s | %s | %s | %d | %s |"
          % (it["id"], it["query"], astr, gt, it["hops"], why))
    A("")
    A("## 5. 硬性校验结果（脚本原文）")
    A("")
    A("```")
    A("$ python _verify_multihop_30.py")
    A("")
    A(head)
    A("```")
    A("")
    A("7 类门禁：条数 / gt 全部 SQL 命中 / hops==len(gt) 且分布达标 / 无重复 /")
    A("**锚点存在且可桥接** / **锚点≠末篇标题** / **答案不泄露** / 多跳性质（answer_kw 首篇无、末篇有）。")
    A("")
    if DISC:
        A("### 已披露的锚点例外（锚点是末篇标题的一部分，非等于；答案均不在标题里）")
        A("")
        for k, v in DISC.items():
            A("- **%s**：%s" % (k, v))
        A("")
    A("### 锚点链条表（第一跳锚点 → 第二跳答案）")
    A("")
    A("| 题号 | 锚点实体 | 锚点出现在哪篇文档（title） | gt 最后一篇（title） |")
    A("|---|---|---|---|")
    for it in data:
        anchors = ANCHOR.get(it["id"], [])
        hit = next((t for t in titles if anchors[0] in t), "（非标题实体，跨文档复现）")
        A("| %s | %s | %s | %s |"
          % (it["id"], " / ".join(anchors), hit, title_of(it["gt"][-1])))
    A("")
    A("## 6. 区分力体检（⚠ 本节数字基于锚点版之前的旧集，已失效）")
    A("")
    A("旧版（去锚点）跑过 `mode=kw` top20：R@5 已满格 7/30、未满格 23/30、掉出 top20 9/30。")
    A("**锚点加回来后关键词路会更容易命中首篇，这些数字必须重跑才有效。**")
    A("重跑命令 `_kg41_discrimination.py`，但它会经 `kb_rag.py search` 写生产 `query_log`，")
    A("需先拿到许可再跑（本轮未跑）。")
    A("")
    A("## 7. 已知缺口")
    A("")
    A("1. **mh05 / mh06 的第三跳是 OCR 产物的图片文档**（`扫描全能王 ….jpg.md`），内容信息量弱于普通 wiki 页。")
    A("   留着是为了让「三跳」有真实的第三跳可落，而不是拿另一篇 wiki 硬凑伪三跳。")
    A("   若剔除这两条：hops2=22、hops3=6 —— **hops3 不达 8 条配额**，需另补 2 条三跳。")
    A("2. 锚点是否为 kg 侧表里真实实体，最终取决于 `kg_extract` 的抽取质量。")
    A("   本文件用 `docs.title` + 「首末篇正文都出现」作为代理判据，是必要条件不是充分条件。")
    A("")
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("已生成 %s（%d 行）" % (OUT, len(L)))


if __name__ == "__main__":
    main()
