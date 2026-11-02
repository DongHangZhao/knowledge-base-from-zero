#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp_server — 知识库检索的零依赖 MCP（Model Context Protocol）服务端
================================================================

把本地知识库检索引擎（kb_rag.search_unified）暴露成 **标准 MCP 工具**，
让 Claude Code / 任意 MCP 客户端 / 智能体通过 stdio 直接检索知识库。

🔴 为什么零依赖手写而非用官方 mcp SDK：
  - 目标环境未装 `mcp`/`fastmcp`（且出网受限装不了），但 MCP 的 stdio 传输
    本质就是「按行读 JSON-RPC 2.0、按行写 JSON-RPC 2.0」——纯标准库即可完整实现。
  - 手写协议层与检索引擎**解耦**：协议逻辑（handle_message）不 import 任何重依赖，
    单测可注入假 searcher，离线跑通整条握手 + 工具调用。

🔴 只暴露「只读检索」，不暴露写入 / 隐私：
  - 工具只有 kb_search（检索）/ kb_stats（统计），无任何写操作；
  - 返回内容来自已脱敏的 wiki 合成层，不外泄 raw 原件或个人信息。

用法（stdio，Claude Code / 客户端拉起）：
    python mcp_server.py
    # 指定别的索引库：KB_DB_PATH=/path/to/kb.db python mcp_server.py

协议支持：initialize / ping / tools/list / tools/call / resources/list /
          notifications/initialized（通知不回包）。
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

try:
    from kb_index import DB_PATH, connect
except Exception:  # 极端情况：kb_index 不可用（不应发生），用兜底路径
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "state", "kb.db")
    connect = None

SERVER_NAME = "kb-mcp"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"


# ============ 工具注册表（数据驱动，便于单测） ============
TOOLS = [
    {
        "name": "kb_search",
        "description": (
            "在知识库检索与 query 最相关的文档/片段，返回按相关度排序的列表"
            "（路径、标题、得分、摘要片段）。\n"
            "mode: fused（关键词⊕语义 RRF 融合，默认）/ kw（仅关键词）/ "
            "vec（仅语义）/ substr（子串）。use_rerank=true 时融合结果再过 "
            "Cross-Encoder 精排（仅 fused 生效）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问句或关键词"},
                "topk": {"type": "integer", "default": 5, "description": "返回条数"},
                "mode": {"type": "string", "enum": ["fused", "kw", "vec", "substr"],
                         "default": "fused", "description": "检索模式"},
                "use_rerank": {"type": "boolean", "default": True,
                               "description": "是否过 Cross-Encoder 精排"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "kb_stats",
        "description": "返回知识库索引统计：文档数 / 分块数 / 已编码语义向量数。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOL_HANDLERS = {}  # 在文件末尾填充


# ============ 上下文（懒加载检索引擎） ============
class Ctx:
    """持有数据库连接与检索引擎，全部懒加载，避免协议层依赖重模型。"""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.environ.get("KB_DB_PATH") or DB_PATH
        self._conn = None
        self._kb_rag = None
        self._import_error = None

    def _ensure(self):
        if self._kb_rag is not None or self._import_error is not None:
            return
        try:
            import kb_rag  # 懒导入：触发 torch/bge 等重依赖
            self._kb_rag = kb_rag
            if connect is not None:
                self._conn = connect()
        except Exception as e:  # 引擎不可用 → 工具返回友好错误，不崩服务
            self._import_error = str(e)

    def search(self, query, topk=5, mode="fused", use_rerank=True):
        self._ensure()
        if self._import_error:
            raise RuntimeError("检索引擎不可用: %s" % self._import_error)
        res, notes = self._kb_rag.search_unified(
            self._conn, query, topk=topk, mode=mode, rerank=use_rerank)
        return res, notes

    def stats(self):
        self._ensure()
        if self._import_error:
            raise RuntimeError("检索引擎不可用: %s" % self._import_error)
        c = self._conn
        d = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        try:
            ch = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        except Exception:
            ch = 0
        try:
            bg = c.execute("SELECT COUNT(*) FROM chunk_bge").fetchone()[0]
        except Exception:
            bg = 0
        return {"docs": d, "chunks": ch, "chunk_bge": bg}

    def snippet_for(self, path):
        """取文档摘要作片段兜底（检索结果无 content 字段时用）。"""
        self._ensure()
        if self._import_error or self._conn is None or not path:
            return ""
        try:
            row = self._conn.execute(
                "SELECT summary FROM docs WHERE path=?", (path,)).fetchone()
            return (row[0] or "") if row else ""
        except Exception:
            return ""


# ============ 工具实现 ============
def _fmt_result(it):
    title = it.get("title") or ""
    path = it.get("path") or ""
    score = it.get("score")
    score_s = ("%.4f" % score) if isinstance(score, (int, float)) else "n/a"
    snippet = it.get("content") or it.get("text") or it.get("summary") or ""
    if isinstance(snippet, str):
        snippet = snippet.replace("\n", " ").strip()[:160]
    else:
        snippet = ""
    return ("· 得分 %s ｜ %s\n    路径: %s\n    片段: %s"
            % (score_s, title, path, snippet))


def h_kb_search(ctx, args):
    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("query 不能为空")
    topk = int(args.get("topk") or 5)
    mode = args.get("mode") or "fused"
    use_rerank = bool(args.get("use_rerank", True))
    res, notes = ctx.search(query, topk=topk, mode=mode, use_rerank=use_rerank)
    if not res:
        return [{"type": "text",
                 "text": "未检索到相关内容。%s" % ("；".join(notes) if notes else "")}]
    lines = ["命中 %d 条（mode=%s）：" % (len(res), mode)]
    for it in res:
        # 片段兜底：结果自带 content/text/summary 优先，否则查文档摘要
        if not (it.get("content") or it.get("text") or it.get("summary")):
            enriched = dict(it)
            enriched["summary"] = ctx.snippet_for(it.get("path") or "")
            it = enriched
        lines.append(_fmt_result(it))
    if notes:
        lines.append("\n备注: " + "；".join(notes))
    return [{"type": "text", "text": "\n".join(lines)}]


def h_kb_stats(ctx, args):
    s = ctx.stats()
    text = ("知识库索引统计：\n  文档数 docs       = %d\n"
            "  分块数 chunks     = %d\n  语义向量 chunk_bge = %d"
            % (s["docs"], s["chunks"], s["chunk_bge"]))
    return [{"type": "text", "text": text}]


TOOL_HANDLERS["kb_search"] = h_kb_search
TOOL_HANDLERS["kb_stats"] = h_kb_stats


# ============ 协议层（纯函数，可注入假 ctx 单测） ============
def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle_message(msg, ctx):
    """处理一条 JSON-RPC 消息，返回要回写的响应 dict；通知类返回 None（不回包）。"""
    if not isinstance(msg, dict):
        return _err(None, -32600, "无效 JSON-RPC 消息（非对象）")
    method = msg.get("method")
    mid = msg.get("id")

    # 通知（无 id）不回包
    if mid is None:
        return None

    if method == "initialize":
        params = msg.get("params", {}) or {}
        pv = params.get("protocolVersion") or PROTOCOL_VERSION
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": pv,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params", {}) or {}
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            # 工具名不存在：按 MCP 约定走 tools/call 的 result.isError，
            # 而非协议级 error（方法 tools/call 本身成功，只是找不到该工具）
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text",
                                            "text": "未知工具: %s" % name}],
                               "isError": True}}
        try:
            content = handler(ctx, arguments)
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": content, "isError": False}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text",
                                            "text": "工具执行失败: %s" % str(e)}],
                               "isError": True}}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"resources": []}}

    return _err(mid, -32601, "方法未实现: %s" % method)


# ============ stdio 主循环 ============
def main():
    ctx = Ctx(os.environ.get("KB_DB_PATH"))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            sys.stderr.write("解析失败: %s\n" % e)
            continue
        try:
            resp = handle_message(msg, ctx)
        except Exception as e:
            sys.stderr.write("处理异常: %s\n" % e)
            resp = None
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
