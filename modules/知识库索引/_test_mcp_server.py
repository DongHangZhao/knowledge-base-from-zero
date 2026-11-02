#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_test_mcp_server — mcp_server.py 协议层单测 + stdio 握手冒烟

设计：协议逻辑（handle_message）与检索引擎解耦，这里注入一个「假 Ctx」
（duck-typed，带假 search / stats），离线验证整条 JSON-RPC 握手 + 工具调用，
不依赖 torch / 真实索引库。

跑法：
    python _test_mcp_server.py            # 协议单测
    python _test_mcp_server.py --stdio    # 再跑一遍真实子进程 stdio 握手冒烟
"""
import json
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp_server as M

PASS = 0
FAIL = 0


# ---- 假 Ctx（duck-typed，仅实现 search / stats）----
class FakeCtx:
    def __init__(self):
        self.calls = []

    def search(self, query, topk=5, mode="fused", use_rerank=True):
        self.calls.append((query, topk, mode, use_rerank))
        return [
            {"path": "wiki/a.md", "title": "A 文档", "score": 0.91,
             "content": "这是关于 %s 的内容片段，足够长会被截断测试。" % query},
            {"path": "wiki/b.md", "title": "B 文档", "score": 0.42},
        ], ["D5 别名扩展: %s" % query]

    def stats(self):
        return {"docs": 100, "chunks": 5000, "chunk_bge": 5000}

    def snippet_for(self, path):
        # 测试用：FakeCtx 结果与真实路径都不缺 content，这里仅补契约
        return ""


def check(name, cond):
    global PASS, FAIL
    # 🔴 统一成 [PASS]/[FAIL]（2026-11-03，T04）：原来打 ✓/✗，run_tests.py:70 的正则
    # 数不到，看板「断言」栏只能标「按退出码」。格式改了、断言与退出码不变。
    print("[%s] %s" % ("PASS" if cond else "FAIL", name))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def rpc(msg, ctx):
    return M.handle_message(msg, ctx)


def test_protocol():
    ctx = FakeCtx()

    # 1) initialize：回包含 protocolVersion / capabilities.tools / serverInfo
    r = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05"}}, ctx)
    check("initialize 返回 protocolVersion", r["result"]["protocolVersion"] == "2024-11-05")
    check("initialize 暴露 tools 能力", "tools" in r["result"]["capabilities"])
    check("initialize 含 serverInfo",
          r["result"]["serverInfo"]["name"] == M.SERVER_NAME)

    # 2) ping
    r = rpc({"jsonrpc": "2.0", "id": 2, "method": "ping"}, ctx)
    check("ping 回空 result", r["result"] == {})

    # 3) tools/list：返回 2 个工具，含 name + inputSchema
    r = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, ctx)
    names = [t["name"] for t in r["result"]["tools"]]
    check("tools/list 含 kb_search", "kb_search" in names)
    check("tools/list 含 kb_stats", "kb_stats" in names)
    kb = [t for t in r["result"]["tools"] if t["name"] == "kb_search"][0]
    check("kb_search 有 inputSchema", kb["inputSchema"]["type"] == "object")
    check("kb_search 必填 query", "query" in kb["inputSchema"]["required"])

    # 4) tools/call kb_search：回包 content 非空、isError=False，命中假结果
    r = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "kb_search",
                        "arguments": {"query": "测试", "topk": 3, "mode": "fused"}}}, ctx)
    res = r["result"]
    check("kb_search isError=False", res["isError"] is False)
    text = res["content"][0]["text"]
    check("kb_search 命中 A 文档", "A 文档" in text and "wiki/a.md" in text)
    check("kb_search 透传参数", ctx.calls[-1] == ("测试", 3, "fused", True))

    # 5) tools/call kb_stats
    r = rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "kb_stats", "arguments": {}}}, ctx)
    text = r["result"]["content"][0]["text"]
    check("kb_stats 含 docs=100", "docs       = 100" in text)

    # 6) 未知工具 → isError=True
    r = rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "no_such", "arguments": {}}}, ctx)
    check("未知工具 isError=True", r["result"]["isError"] is True)

    # 7) 空 query → 工具抛错被包成 isError
    r = rpc({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "kb_search", "arguments": {"query": "  "}}}, ctx)
    check("空 query isError=True", r["result"]["isError"] is True)

    # 8) 通知（无 id）不回包
    r = rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, ctx)
    check("通知不回包 (None)", r is None)

    # 9) 未知方法（带 id）→ error
    r = rpc({"jsonrpc": "2.0", "id": 9, "method": "foobar"}, ctx)
    check("未知方法返回 error", "error" in r and r["error"]["code"] == -32601)

    # 10) 非对象消息 → error
    r = rpc("not-a-dict", ctx)
    check("非对象消息返回 error", "error" in r)


def test_stdio_smoke():
    """真实子进程跑 stdio 握手（initialize + tools/list + ping），不需 torch。"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ]
    inp = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs)
    p = subprocess.run([sys.executable, script], input=inp,
                       capture_output=True, text=True, timeout=60)
    out_lines = [l for l in p.stdout.splitlines() if l.strip()]
    check("stdio 回 3 行 JSON", len(out_lines) == 3)
    if len(out_lines) == 3:
        r1 = json.loads(out_lines[0])
        r2 = json.loads(out_lines[1])
        r3 = json.loads(out_lines[2])
        check("stdio initialize 成功", r1["id"] == 1 and "result" in r1)
        check("stdio tools/list 成功", r2["id"] == 2 and "tools" in r2["result"])
        check("stdio ping 成功", r3["id"] == 3 and r3["result"] == {})


def main():
    test_protocol()
    if "--stdio" in sys.argv:
        test_stdio_smoke()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
