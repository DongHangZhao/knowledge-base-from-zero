#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""KG-41 多跳评测集校验脚本（一次性审计工具，不参与生产链路）

校验 9 件事：
  1. 条数 = 30
  2. gt 每条路径都在 kb.db 的 docs.path 里真实存在
  3. hops 分布达标（hops == len(gt)；hops2 >= 15，hops3 >= 8）
  4. 单条内无重复/同义重复路径
  5. id 唯一且连续
  6. **每条 query 含锚点实体，且该实体真实存在于 docs.title**（1-hop 的起点）
  7. **锚点不等于其 gt 最后一篇的标题**（否则锚点就是答案）
  8. **答案关键词不出现在 query 里**（防泄露）
  9. 多跳性质：answer_kw 在「最后一篇」出现、在「第一篇」不出现

为什么把旧的「query 不含 gt 文件名原词」换成 6/7/8：
  1-hop 的机制是 `_query_entities(query)` → 查共享实体的 chunk。查询里没有实体，这一路直接返回 []，
  于是 treat 组测的是「没东西可扩」而不是「图谱没用」—— 那是假阴性。
  旧规则把锚点也一起禁掉了，会把题改造成「那批系统里…」这种没人这么说话的句子。
  新规则允许第一跳锚点出现（用户已知信息），只禁止**答案侧**泄露。

用法：
    python _verify_multihop_30.py             # 校验 + 锚点链条表
    python _verify_multihop_30.py --matrix    # 额外打印 answer_kw 全文命中矩阵
"""
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.join(_HERE, "state", "kb_eval_multihop_30.json")
DB = os.path.join(_HERE, "state", "kb.db")

EXPECT_N = 30
MIN_HOPS2 = 15
MIN_HOPS3 = 8

# 锚点实体（第一跳起点，用户已知信息）。每条给 1~2 个，要求：
#   · 全部必须出现在 query 里
#   · 至少 1 个「桥接成功」：同时出现在 gt 首篇与末篇正文中（否则 1-hop 扩不到答案 chunk）
#   · 每个必须能在 docs.title 里查到，或作为跨文档复现实体（首末篇正文都出现）
ANCHOR = {
    "mh01": ["集群五大系统"],
    "mh02": ["集群五大系统"],
    "mh03": ["集群五大系统"],
    "mh04": ["集群五大系统"],
    "mh05": ["集群五大系统"],
    "mh06": ["集群五大系统"],
    "mh07": ["公众号日更系统", "配图"],
    "mh08": ["Agnes", "公众号"],
    "mh09": ["公众号日更系统", "草稿箱"],
    "mh10": ["简历求职生成管线", "docx"],
    "mh11": ["RAG 评测", "RRF"],
    "mh12": ["采集爬虫"],
    "mh13": ["每日运势与卜易系统", "起卦"],
    "mh14": ["备份防线", "bge"],
    "mh15": ["知识库双链路"],
    "mh16": ["RAG 评测"],
    "mh17": ["剃刀", "微调"],
    "mh18": ["AI Agent 的边界感", "越界"],
    "mh19": ["知识管理"],
    "mh20": ["知识库检索", "混合检索"],
    "mh21": ["Flask"],
    "mh22": ["代码架构"],
    "mh23": ["AI Agent 岗位", "智能体"],
    "mh24": ["媒体生成管线", "图表"],
    "mh25": ["微信文件同步"],
    "mh26": ["可复用模块"],
    "mh27": ["可复用模块"],
    "mh28": ["第 101 个任务", "准则"],
    "mh29": ["版本管理"],
    "mh30": ["C 盘", "磁盘"],
}

# 锚点是末篇标题的一部分（非等于）—— 不判 FAIL，但逐条披露，供人工判断是否构成泄露
ANCHOR_DISCLOSED = {
    "mh08": "「公众号」也是末篇标题的一部分（图表三通道评测 —— 公众号配图通道决策），答案（中文大量乱码 / 1MB 上限）不在标题里",
    "mh13": "「起卦」也是末篇标题的一部分（起卦必须真随机 + 回归只测不变量），答案（动爻率 / 种子可复现）不在标题里",
    "mh20": "「混合检索」也是末篇标题的一部分（业界 RAG 栈对标…混合检索…），但它不是答案（答案是 rank_constant=60）",
    "mh24": "「图表」也是末篇标题的一部分（图表三通道评测…），答案（38 KB / 1MB）不在标题里",
    "mh28": "「准则」也是末篇标题的一部分（准则修订历史…），答案（6198 字符 / 28 段继承链）不在标题里",
    "mh30": "「磁盘」也是末篇标题的一部分（磁盘清理扫描体系），答案（-shm/-wal 陷阱）不在标题里",
}

# 多跳证据 + 答案关键词：answer_kw 必须在最后一篇 gt 出现、第一篇不出现、且 query 里不出现
AUDIT = {
    "mh01": ("SpringBoot", "总览只给优先级/上线顺序，技术栈在子文档"),
    "mh02": ("PostgreSQL", "总览只给承建方差异，数据库选型在子文档"),
    "mh03": ("MyBatis", "总览定优先级 → 急诊系统指向代码工程 → ORM 在第三篇"),
    "mh04": ("InfluxDB", "总览定上线顺序，时序库选型在子文档"),
    "mh05": ("图片 OCR", "总览定优先级 → 文档清单说培训签到=扫描图 → OCR 产物落在图片分流"),
    "mh06": ("图片 OCR", "总览定承建方 → 无纸化定位 → OCR 产物落在图片分流"),
    "mh07": ("水印", "日更系统定「不许用积分通道」，硬伤清单在对照页"),
    "mh08": ("中文大量乱码", "对照页给耗时与体积，正文上限与中文乱码在决策页"),
    "mh09": ("freepublish", "日更系统说只到草稿箱，群发为何要人手在边界页"),
    "mh10": ("create_doc", "管线给 md→docx→pdf，通道规则在对接层"),
    "mh11": ("六桶", "评测页给旧口径 → 方法论给调参集≠测试集 → 六桶给题集结构"),
    "mh12": ("项目所有者已拍板", "v0.1 是纯设计，四条拍板只写在 v0.2"),
    "mh13": ("动爻率", "系统页说必须真随机，不变量口径沉淀成提示词卡"),
    "mh14": ("chunks_fts", "备份页给阈值，链路页给各表角色"),
    "mh15": ("9 秒", "链路页说静默答错，体检页说 build 其实增量"),
    "mh16": ("MinerU", "评测页给精排数据 → 清单给两处根因 → 规划给处置位置"),
    "mh17": ("消融", "细则给三步顺序，怎么证明有效在实战篇"),
    "mh18": ("五何", "边界感篇给越界数据，接活模板在细则"),
    "mh19": ("信息仓库", "管人篇给 DIKW，第二大脑篇给「信息仓库」结论"),
    "mh20": ("rank_constant", "实战篇给稀疏路必要性，业界常数在对标页"),
    "mh21": ("SemVer", "Flask 篇给安全项，版本规则在规范页"),
    "mh22": ("healthz", "架构篇给分层，探活端点在部署方案"),
    "mh23": ("SWE-MISBEHAVIOR", "面试篇给追问方向，可靠性证据在边界感篇"),
    "mh24": ("38 KB", "管线给能力清单 → 对照页给实测差异 → 决策页给本地出图体积"),
    "mh25": ("网盘总容量", "同步体系给隐私识别，为什么不上云在总览红线"),
    "mh26": ("§2.5", "总览给红线，条文条款在细则"),
    "mh27": ("100 MiB", "总览给「先问」，平台硬限制在对接层"),
    "mh28": ("6198", "成稿 → 细则 4.5 → 修订历史解释拆页原因"),
    "mh29": ("AGENTS.md", "版本管理给 HANDOFF，跨工具落地在上下文工程，条款在细则"),
    "mh30": ("-shm", "复盘篇给四层量化，mtime/去重陷阱在磁盘清理页"),
}

# 已知且已披露的例外：锚点是项目名，同时也是末篇标题的一部分（非等于，见 ANCHOR_DISCLOSED）


def main():
    show_matrix = "--matrix" in sys.argv
    fails = []

    with open(EVAL, encoding="utf-8") as f:
        data = json.load(f)
    conn = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    all_titles = [r[0] for r in conn.execute("select title from docs") if r[0]]

    # --- 1 ---
    if len(data) == EXPECT_N:
        print("[PASS] 条数 = %d" % len(data))
    else:
        print("[FAIL] 条数 = %d，期望 %d" % (len(data), EXPECT_N))
        fails.append("count")

    # --- 5 ---
    ids = [it.get("id") for it in data]
    expect_ids = ["mh%02d" % (i + 1) for i in range(len(data))]
    if ids == expect_ids:
        print("[PASS] id 唯一且连续 mh01..mh%02d" % len(data))
    else:
        print("[FAIL] id 不连续/不唯一，首个不一致：%s"
              % [(a, b) for a, b in zip(ids, expect_ids) if a != b][:3])
        fails.append("ids")

    # --- 2 ---
    all_paths = [p for it in data for p in it["gt"]]
    miss_path = [p for p in set(all_paths)
                 if not conn.execute("select 1 from docs where path=?", (p,)).fetchone()]
    n_path = len(set(all_paths))
    if not miss_path:
        print("[PASS] %d/%d 路径全部存在于 kb.db docs.path（共 %d 条唯一路径）"
              % (n_path, n_path, n_path))
    else:
        print("[FAIL] %d 条路径不存在：" % len(miss_path))
        for p in miss_path:
            print("       - %s" % p)
        fails.append("paths")

    # --- 3+4 ---
    h2 = h3 = h_other = 0
    dup_bad, hop_bad = [], []
    for it in data:
        n = len(it["gt"])
        if it.get("hops") != n:
            hop_bad.append((it["id"], it.get("hops"), n))
        if n == 2:
            h2 += 1
        elif n == 3:
            h3 += 1
        else:
            h_other += 1
        if len(set(it["gt"])) != n:
            dup_bad.append(it["id"])
    if hop_bad:
        print("[FAIL] hops 与 gt 条数不一致：%s" % hop_bad[:5])
        fails.append("hops")
    else:
        print("[PASS] hops = len(gt)，逐条一致（hops 语义：需要读几篇文档）")
    if dup_bad:
        print("[FAIL] gt 内部有重复路径：%s" % dup_bad)
        fails.append("dup")
    else:
        print("[PASS] 无重复 gt 路径")
    ok_dist = h2 >= MIN_HOPS2 and h3 >= MIN_HOPS3 and h_other == 0
    print("[%s] hops 分布：hops2=%d（要求>=%d） hops3=%d（要求>=%d） 其他=%d"
          % ("PASS" if ok_dist else "FAIL", h2, MIN_HOPS2, h3, MIN_HOPS3, h_other))
    if not ok_dist:
        fails.append("dist")

    # --- 6. 锚点齐全 + 桥接 ---
    docs = {p: (conn.execute("select content from docs where path=?", (p,)).fetchone() or [""])[0]
            for p in set(all_paths)}
    anchor_bad, bridge_bad = [], []
    for it in data:
        anchors = ANCHOR.get(it["id"])
        if not anchors:
            anchor_bad.append((it["id"], "ANCHOR 表缺条目"))
            continue
        f = docs.get(it["gt"][0]) or ""
        l = docs.get(it["gt"][-1]) or ""
        for a in anchors:
            if a not in it["query"]:
                anchor_bad.append((it["id"], "锚点「%s」未出现在 query" % a))
            known = any(a in t for t in all_titles) or (a in f and a in l)
            if not known:
                anchor_bad.append((it["id"], "锚点「%s」既不在 docs.title 也不是跨文档复现实体" % a))
        if not any(a in l for a in anchors):
            bridge_bad.append((it["id"], "/".join(anchors)))
    if anchor_bad:
        print("[FAIL] 锚点检查 %d 条不过：" % len(anchor_bad))
        for x in anchor_bad:
            print("       - %s：%s" % x)
        fails.append("anchor")
    else:
        print("[PASS] 30/30 条含锚点实体且实体在库中存在")
    if bridge_bad:
        print("[FAIL] 锚点未落进答案文档（1-hop 扩不到）%d 条：" % len(bridge_bad))
        for x in bridge_bad:
            print("       - %s：%s" % x)
        fails.append("bridge")
    else:
        print("[PASS] 30/30 至少一个锚点出现在 gt 末篇正文（1-hop 可达前提成立）")

    # --- 7. 锚点 != 末篇标题 ---
    norm = lambda s: "".join(s.split()).lower()
    eq_bad, sub_warn = [], []
    for it in data:
        last_title = conn.execute("select title from docs where path=?",
                                  (it["gt"][-1],)).fetchone()
        lt = norm(last_title[0] if last_title else "")
        for a in ANCHOR.get(it["id"], []):
            na = norm(a)
            if na == lt:
                eq_bad.append((it["id"], a, lt))
            elif na in lt:
                sub_warn.append((it["id"], a,
                                 ANCHOR_DISCLOSED.get(it["id"], "未披露")))
    if eq_bad:
        print("[FAIL] 锚点等于末篇标题 %d 条：" % len(eq_bad))
        for x in eq_bad:
            print("       - %s：%s ← %s" % x)
        fails.append("anchor_eq")
    else:
        print("[PASS] 无一条的锚点等于其 gt 最后一篇的标题")
    if sub_warn:
        print("  ℹ 锚点是末篇标题的一部分（非等于，不判 FAIL，逐条披露）：")
        for x in sub_warn:
            print("       - %s：锚点「%s」— %s" % x)

    # --- 8+9. 答案不泄露 + 多跳性质 ---
    leak, mh_bad = [], []
    for it in data:
        kw, _why = AUDIT.get(it["id"], (None, None))
        if not kw:
            mh_bad.append((it["id"], "AUDIT 缺条目"))
            continue
        if kw in it["query"]:
            leak.append((it["id"], kw))
        first = docs.get(it["gt"][0]) or ""
        last = docs.get(it["gt"][-1]) or ""
        if kw not in last or kw in first:
            mh_bad.append((it["id"], kw, "last=%s" % (kw in last), "first=%s" % (kw in first)))
        if show_matrix:
            marks = ["%s:%s" % (i + 1, "有" if kw in (docs.get(p) or "") else "-")
                     for i, p in enumerate(it["gt"])]
            print("   %s kw=%-22s %s" % (it["id"], kw, " ".join(marks)))
    if leak:
        print("[FAIL] 答案关键词出现在 query 里 %d 条：" % len(leak))
        for x in leak:
            print("       - %s：答案词「%s」泄露" % x)
        fails.append("leak")
    else:
        print("[PASS] 30/30 答案关键词未出现在 query 中（不泄露）")
    if mh_bad:
        print("[FAIL] 多跳证据不足 %d 条：" % len(mh_bad))
        for x in mh_bad:
            print("       - %s" % (x,))
        fails.append("multihop")
    else:
        print("[PASS] 30/30 多跳性质成立（答案关键词在第一篇不出现、在最后一篇出现）")

    # --- 锚点链条表 ---
    print("")
    print("=" * 100)
    print("锚点链条表：第一跳锚点 → 第二跳答案（末篇）")
    print("=" * 100)
    print("%-6s %-26s %-38s %s" % ("题号", "锚点实体", "锚点出现在哪篇文档（title）", "gt 最后一篇（title）"))
    print("-" * 108)
    for it in data:
        anchors = ANCHOR.get(it["id"], [])
        shown, hits = [], []
        for a in anchors:
            t = next((t for t in all_titles if a in t), None)
            shown.append(a)
            hits.append(t[:34] if t else "（非标题实体）")
        lt_row = conn.execute("select title from docs where path=?",
                             (it["gt"][-1],)).fetchone()
        lt = lt_row[0] if lt_row else "（未找到）"
        print("%-6s %-26s %-38s %s" % (it["id"], " / ".join(shown),
                                       hits[0], lt[:46]))
        if len(hits) > 1:
            print("%-6s %-26s %-38s" % ("", "", hits[1]))

    conn.close()
    print("")
    if fails:
        print("==== 结果：FAIL（%d 项）====" % len(fails))
        return 1
    print("==== 结果：PASS 30/30 ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
