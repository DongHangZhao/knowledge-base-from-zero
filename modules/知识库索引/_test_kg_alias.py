#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_kg_alias.py — 实体链接 / 同义词边的纯逻辑单测（零依赖，不需要 networkx/jieba）

验证：
  ① candidate_aliases 的后缀归一：长词+通用后缀 ↔ 短词，且短词高 df
  ② ALIAS_MAP 的显式归并：同一领域家族的多种表述被连起来
  ③ 不误并：明显不同的概念不产生同义词边
  ④ 低 df / 过短 / 缺节点 不触发

用法：$PY -u _test_kg_alias.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
import kg_ppr

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s" % name)


# ① 后缀归一（candidate_aliases）
ent_df = {
    "智能血液透析系统": 20, "智能血液透析": 12,   # 应配对
    "全院统一预约平台": 15, "全院统一预约": 9,     # 应配对
    "知识库索引": 30,                            # 无后缀可剥，不配对
    "操作系统": 25, "操作": 3,                    # 后缀可剥但「操作」df<5 → 不配对
    "系统": 200,                                 # 剥出空串，不配对
}
pairs = kg_ppr.candidate_aliases(ent_df, min_df=5, min_len=4)
got = set((a, b) for a, b in pairs)
check("后缀归一：智能血液透析系统↔智能血液透析", ("智能血液透析系统", "智能血液透析") in got)
check("后缀归一：全院统一预约平台↔全院统一预约", ("全院统一预约平台", "全院统一预约") in got)
check("不配对：低 df 变体（操作 df=3）", ("操作系统", "操作") not in got)
check("不配对：无后缀词（知识库索引）", all(a != "知识库索引" for a, _ in got))

# ② ALIAS_MAP 显式归并
names = {"集群五大系统": 30, "五大系统": 18, "那批系统": 11, "无关实体": 9}
ap = kg_ppr.alias_pairs(names, alias_map=kg_ppr.ALIAS_MAP)
ap_got = set((a, b) for a, b, _ in ap)
check("ALIAS_MAP：集群五大系统↔五大系统", ("集群五大系统", "五大系统") in ap_got)
check("ALIAS_MAP：集群五大系统↔那批系统", ("集群五大系统", "那批系统") in ap_got)
check("不误并：无关实体不进同义词边", all("无关实体" not in (a, b) for a, b, _ in ap))

# ③ 缺 variant 节点不产生对
names2 = {"集群五大系统": 30}
ap2 = kg_ppr.alias_pairs(names2, alias_map=kg_ppr.ALIAS_MAP)
check("缺 variant 节点时不产生同义词边", len(ap2) == 0)

# ④ 两类来源（alias_map + suffix）同时存在
names3 = {"集群五大系统": 30, "五大系统": 18,
          "智能血液透析系统": 20, "智能血液透析": 12}
ap3 = kg_ppr.alias_pairs(names3, alias_map=kg_ppr.ALIAS_MAP)
srcs = set(s for _, _, s in ap3)
check("两类来源都存在（alias_map + suffix）", "alias_map" in srcs and "suffix" in srcs)

print("\n[%s] PASS=%d FAIL=%d" % ("OK" if FAIL == 0 else "FAIL", PASS, FAIL))
sys.exit(1 if FAIL else 0)
