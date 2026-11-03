#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_kg_terms.py — 领域词典构建与短语抽取

钉住三件容易退化的事：
  ① 标题切分要能挖出「集群五大系统项目」这类完整短语（本模块存在的理由）
  ② 注入后分词不能再把它切碎（核心收益，退化即失效）
  ③ 路径碎片 / 目录名不许进词典（不然 1-hop 会扩到一堆无关目录）

第 ②③ 条都是「退化后不会报错、只会悄悄变差」的类型，所以必须机器判。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

P, F = [], []


def check(name, cond, detail=""):
    (P if cond else F).append(name)
    print("%s %s%s" % ("[PASS]" if cond else "FAIL", name,
                       (" → " + str(detail)) if detail else ""))
    return cond


class FakeConn:
    """最小 sqlite3 连接替身：只支持 build_domain_dict 用到的那条查询。"""

    def __init__(self, titles):
        self.titles = titles

    def execute(self, sql, params=()):
        assert "FROM docs" in sql.replace("\n", " ")
        rows = [(t,) for t in self.titles]

        class R:
            def __init__(self, rs):
                self.rs = rs

            def fetchall(self):
                return self.rs
        return R(rows)


def main():
    import kg_terms

    # ① 标题切分
    titles = [
        "集群五大系统项目 — 总览",
        "全院统一预约平台（UMRP）",
        "可复用模块总览",
        "Sources",
        "Raw",
        "wiki",
        "2026-11-03-每日运势总览",
        "智能血液透析信息系统 — 实际代码项目",
    ]
    dic = kg_terms.build_domain_dict(FakeConn(titles))
    check("① 从标题挖出完整短语「集群五大系统项目」", "集群五大系统项目" in dic, list(dic)[:6])
    check("①b 括号内缩写单独成词（UMRP）", "UMRP" in dic, "UMRP" in dic)
    check("①c 分隔符前后都能切（全院统一预约平台）",
          "全院统一预约平台" in dic, "全院统一预约平台" in dic)

    # ③ 路径碎片过滤
    bad = [t for t in dic if t.lower() in
           {"sources", "raw", "wiki", "modules", "state", "readme"}]
    check("③ 目录名不进词典", not bad, bad or "已过滤")

    # ② 注入后分词不切碎（核心回归）
    try:
        import jieba
        jieba.setLogLevel(20)
        kg_terms.inject(dic)
        text = "集群五大系统项目里，全院统一预约平台用的是 Flask。"
        toks = [w for w in jieba.cut(text) if len(w.strip()) > 1]
        check("② 注入后「集群五大系统项目」不被切碎",
              "集群五大系统项目" in toks, toks[:6])
        check("②b 注入后「全院统一预约平台」不被切碎",
              "全院统一预约平台" in toks, toks[:6])
    except ImportError:
        print("[SKIP] 没装 jieba，跳过分词断言")

    # ④ 首字倒排扫描正确性（词典命中的兜底路径）
    hits = kg_terms._scan_dict("本项目里的集群五大系统项目和可复用模块总览", dic)
    check("④ 倒排扫描命中词典词", "集群五大系统项目" in hits, hits)
    check("④b 长词优先（命中长词后跳过其覆盖区，不重复命中子串）",
          "集群五大系统" not in hits, "已按最长匹配")

    # ⑤ 抽取：词典优先于规则
    ents = dict(kg_terms.extract("集群五大系统项目 — 总览", dic))
    check("⑤ 词典命中标注为 dict 类型",
          ents.get("集群五大系统项目") == "dict", ents)

    # ⑥ 停用词/碎片不进结果
    ents2 = dict(kg_terms.extract("这个系统的数据文件里有很多东西", dic))
    junk = [k for k in ents2 if k in kg_terms.STOP_CN]
    check("⑥ 停用词不进实体表", not junk, junk or "已过滤")

    print()
    if F:
        print("❌ kg_terms：%d/%d 通过，%d 失败" % (len(P), len(P) + len(F), len(F)))
        return 1
    print("✅ kg_terms：%d/%d 断言通过" % (len(P), len(P)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
