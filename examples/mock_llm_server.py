#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 OpenAI 兼容 mock 端点：只为验证 ``/ask`` 开=True 时链路真跑。

🔴 零联网、零凭据、零真实模型——不要用它的输出当评测结论（LLM 不自当评测金标）。
   用途只有两个：① 证明开关打开后「检索 → 出口门禁 → 生成 → 引用」链路通；
   ② 让没有 API Key 的人也能在本机复现 A/B 差值与缓存命中。

启动：python examples/mock_llm_server.py
然后：KB_LLM_ENABLED=1 KB_LLM_BASE_URL=http://127.0.0.1:8789/v1 python modules/知识库索引/api_server.py
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("KB_MOCK_PORT") or "8789")
CALLS = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):                       # /calls 供缓存命中取证
        if self.path.startswith("/calls"):
            self._send({"calls": CALLS["n"]})
        else:
            self._send({"service": "mock-llm", "port": PORT})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._send({"error": str(e)}, 400)
            return
        CALLS["n"] += 1
        msgs = body.get("messages", [])
        user = msgs[-1]["content"] if msgs else ""
        paths = re.findall(r"^\[\d+\]\s+(\S+)", user, re.M)
        answer = ("（本地 mock 输出，不代表真实模型质量）依据给定片段作答；引用："
                  + "、".join(paths[:3]))
        self._send({
            "id": "mock-local", "object": "chat.completion",
            "model": body.get("model", "local-mock"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "mock_calls": CALLS["n"],
        })

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("mock LLM → http://127.0.0.1:%d/v1/chat/completions" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
