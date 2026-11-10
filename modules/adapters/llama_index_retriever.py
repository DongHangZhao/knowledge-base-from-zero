#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LlamaIndex 适配器：把自研 ``kb_rag.search_unified`` 包成 ``BaseRetriever``。

🔴 定位：**仅互操作 / 演示**，生产检索不走框架。理由见根 README「RAG Engine」一节。
依赖：``llama-index-core``（``pip install -r requirements-demo.txt``）；不装时生产 CLI 照跑。

设计取舍（§3 五何）：
- 为何不直接 import 引擎到模块顶层：``kb_rag`` 顶层会拉 bge_encoder / build_task 等一串，
  延迟到 ``_retrieve`` 才导入，import 本模块的成本≈0，服务启动不被模型绑住。
- 为何默认 mode="kw"：FTS5 关键词路零模型依赖，干净目录 clone 下来就能 rc=0 复现；
  配好 BGE 模型后设 ``KB_DEMO_MODE=fused`` 即切生产同款三链路融合。
- 零命中返回空列表，不编造（对齐 W4 终态默认）。
- ``notes`` 是 ``search_unified`` 已拼好的 **str**，不是 list（W2 真 bug：对 str 做 join 会逐字符拆）。
"""
import os
import sys
from typing import List

from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
ENGINE_DIR = os.environ.get("KB_ENGINE_DIR") or os.path.join(_REPO, "modules", "知识库索引")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)


class KBRetriever(BaseRetriever):
    """自研三链路检索引擎 → LlamaIndex ``NodeWithScore``。

    ``db_path`` 指向 demo 脱敏库（``examples/state/demo.db``），不碰生产 ``kb.db``。
    """
    # 🔴 llama-index-core 0.14 的 BaseRetriever 是**普通类 + 自定义 __init__**
    # （只收 callback_manager / object_map / objects / verbose），不是 pydantic 模型；
    # 用 Field 声明字段会在构造时 TypeError: unexpected keyword argument。
    # 0.13 及更早的部分版本是 pydantic v1 模型，写法不同——真跑一次才知道（W2 同款教训）。
    def __init__(self, db_path: str, mode: str = "kw", top_k: int = 5, **kwargs):
        super().__init__(**kwargs)
        self.db_path = db_path
        self.mode = mode
        self.top_k = max(1, min(int(top_k), 50))

    @staticmethod
    def _engine():
        import kb_rag  # 延迟导入，见模块 docstring
        return kb_rag

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        import kb_index

        q = query_bundle.query_str if hasattr(query_bundle, "query_str") else str(query_bundle)
        q = (q or "").strip()
        if not q:
            return []
        # search_unified 内部会 log_query + commit，必须是可写连接，不能 readonly
        conn = kb_index.connect(self.db_path, check_same_thread=False)
        try:
            rows, notes = self._engine().search_unified(
                conn, q, topk=self.top_k, mode=self.mode, rerank=False)
        finally:
            conn.close()
        out = []
        for r in rows or []:
            node = TextNode(
                text=r.get("snippet") or r.get("title") or "",
                metadata={
                    "path": r.get("path", ""),
                    "title": r.get("title", ""),
                    "category": r.get("category", ""),
                    "score": r.get("score"),
                    "coverage": r.get("coverage"),
                    "source": r.get("source", ""),
                    "notes": notes if isinstance(notes, str) else str(notes or ""),
                    "db": os.path.basename(self.db_path),
                },
            )
            out.append(NodeWithScore(node=node, score=float(r.get("score") or 0.0)))
        return out
