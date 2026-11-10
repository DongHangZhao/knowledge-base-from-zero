#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链路 demo：加载示例语料 → 检索 → 带引用作答（不依赖生产 kb.db）。"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path[:0] = [_REPO, os.path.join(_REPO, "modules", "adapters")]

from llama_index.core.schema import QueryBundle            # noqa: E402
from llama_index_retriever import KBRetriever              # noqa: E402

DB = os.environ.get("KB_DEMO_DB") or os.path.join(_HERE, "state", "demo.db")
QUERY = sys.argv[1] if len(sys.argv) > 1 else "混合检索 RRF 融合怎么做"

nodes = KBRetriever(db_path=DB, mode="kw", top_k=3).retrieve(QueryBundle(QUERY))
print("查询：%s" % QUERY)
if not nodes:
    print("库内未检索到相关内容（不编造）")
    sys.exit(0)
for i, n in enumerate(nodes, 1):
    print("%d. [%.4f] %s" % (i, n.score or 0, n.node.metadata["title"]))
    print("   引用：%s" % n.node.metadata["path"])
    print("   片段：%s" % n.node.text[:100])
print("引擎：自研 kb_rag.search_unified ｜ 适配：LlamaIndex BaseRetriever")
