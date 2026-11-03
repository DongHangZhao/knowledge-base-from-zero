#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_eval_readonly.py — 评测脚本绝不允许污染生产 query_log

为什么单独一个测试套件：
    2026-11-04 凌晨发现生产 query_log 6363 行里 5924 行（93%）是评测题，
    真人查询只剩 439 行。根因不是「有人手滑」，是 eval_rag.py 用裸
    sqlite3.connect() 拿了可写连接，而 kb_index 的检索埋点会往 query_log
    插行 —— 于是「评测」这件纯读取的事改写了业务表。

    这类缺陷有三个特点，所以必须用测试钉住而不是靠记性：
      ① 不报错：检索照常返回，日志照常写入
      ② 不影响功能：所以没人发现
      ③ 破坏的是**下游分析的可信度**：任何漏检归因都建在 93% 噪声上

    而且它极容易复发：修好之后一次「从仓库复制覆盖生产」就丢了
    （本轮就真的又丢过一次，因为当时只改了生产侧没改仓库侧）。

四条断言：
    ① eval_rag.py 源码里确实用 readonly=True 建连接
    ② 只读连接上的 INSERT 必须抛 OperationalError（证明 readonly 真的生效，
       不是「参数写了但被忽略」）
    ③ 在只读连接上跑一次 search_unified，query_log 行数必须不变
    ④ kb_index 对只读连接的埋点有兜底（不因写失败而让检索炸掉）

跑法：python _test_eval_readonly.py
环境：需要可读的 kb.db（KB_DB_PATH 或模块默认 state/kb.db）；库不存在则 skip。
"""

import os
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
P, F = [], []


def check(name, cond, detail=""):
    (P if cond else F).append(name)
    print("%s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                       (" → " + str(detail)) if detail else ""))
    return cond


def main():
    # ---- ① 源码静态检查：不许出现裸 connect 建库连接 ----
    src = open(os.path.join(HERE, "eval_rag.py"), encoding="utf-8").read()
    has_ro = "readonly=True" in src
    check("① eval_rag.py 用 readonly=True 建连接", has_ro,
          "源码含 readonly=True" if has_ro else "找不到 readonly=True，可能被覆盖回裸 connect")

    # 裸 sqlite3.connect(DB_PATH) 作为「评测主连接」出现即算违规
    bare = re.findall(r"^\s*conn\s*=\s*sqlite3\.connect\(DB_PATH\)", src, re.M)
    check("①b 主连接不是裸 sqlite3.connect(DB_PATH)", not bare,
          "命中 %d 处" % len(bare))

    # ---- 准备库 ----
    try:
        import kb_index
        db = kb_index.DB_PATH
    except Exception as e:
        check("② 可导入 kb_index 并定位库", False, str(e)[:60])
        return summary()
    if not os.path.exists(db):
        print("[SKIP] 库不存在：%s（本套件需真库，仓库侧请设 KB_DB_PATH）" % db)
        return summary()

    # ---- ② 只读连接必须真的挡住写 ----
    try:
        conn = kb_index.connect(db, readonly=True)
    except Exception as e:
        check("② 建立只读连接", False, str(e)[:60])
        return summary()
    check("② 建立只读连接", True, os.path.basename(db))

    blocked = False
    try:
        conn.execute(
            "INSERT INTO query_log(ts,query,mode,topk,n_results,latency_ms,"
            "top1_path) VALUES('x','_ro_probe_','x',1,1,1,'x')")
        conn.commit()
    except sqlite3.OperationalError:
        blocked = True
    except Exception:
        blocked = True
    check("②b 只读连接上 INSERT query_log 被拒绝", blocked,
          "已抛 OperationalError" if blocked else "居然写成功了！readonly 没生效")

    # ---- ③ 只读连接跑检索，query_log 行数不变 ----
    n0 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]

    n_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    if n_docs == 0:
        print("[SKIP] 库里没有文档，跳过检索断言")
        conn.close()
        return summary()

    row = conn.execute("SELECT title FROM docs WHERE title IS NOT NULL "
                       "AND length(trim(title))>=4 LIMIT 1").fetchone()
    q = row[0] if row else "知识库"

    # ③④ 需要真模型（search_unified 的向量路要 bge）。仓库侧 models/ 是空占位，
    # 这里必须先探一下，否则 kb_rag 在缺模型时会 print 后 sys.exit，
    # 把整个测试进程带走 —— 表现为「只跑了 4 条断言就没有总结了」。
    model_ok = False
    try:
        import bge_encoder
        # 各版本的变量名不统一（生产是 MODEL_DIR，仓库侧曾叫 BGE_MODEL_DIR），
        # 逐个候选探测，别把「变量名不同」误判成「没装模型」。
        for attr in ("BGE_MODEL_DIR", "MODEL_DIR", "SMALL_MODEL_DIR"):
            mdir = getattr(bge_encoder, attr, None)
            if not mdir or not os.path.isdir(mdir):
                continue
            if any(os.path.exists(os.path.join(mdir, f))
                   for f in ("config.json", "pytorch_model.bin",
                             "model.safetensors")):
                model_ok = True
                break
    except Exception:
        model_ok = False
    if not model_ok:
        print("[SKIP] ③④ 缺 bge 模型目录，跳过检索断言（生产机必跑）")
        conn.close()
        return summary()

    ok_search = True
    try:
        import kb_rag
        res, _note = kb_rag.search_unified(conn, q, topk=5, mode="fused",
                                           rerank=False)
        check("③ 只读连接上 search_unified 正常返回", True,
              "q=%r → %d 条" % (q[:14], len(res)))
    except Exception as e:
        ok_search = False
        check("③ 只读连接上 search_unified 正常返回", False, str(e)[:70])

    n1 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    check("③b 检索后 query_log 行数不变", n1 == n0,
          "%d → %d（差 %+d）" % (n0, n1, n1 - n0))

    # ---- ④ 兜底：埋点写失败不能把检索搞挂 ----
    # 上一断言若成功返回，就同时证明了「写失败被兜住」——因为只读连接上
    # 埋点必然抛 OperationalError，而检索结果照常返回。
    check("④ 埋点写失败不影响检索（except 兜底生效）", ok_search and n1 == n0,
          "检索成功且未写入" if (ok_search and n1 == n0) else "见 ③ 明细")

    conn.close()
    return summary()


def summary():
    print()
    if F:
        print("❌ eval 只读回归：%d/%d 通过，%d 失败" % (len(P), len(P) + len(F), len(F)))
        return 1
    print("✅ eval 只读回归：%d/%d 断言通过" % (len(P), len(P)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
