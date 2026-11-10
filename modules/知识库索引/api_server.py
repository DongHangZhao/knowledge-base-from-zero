#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api_server — 本地 REST API（FastAPI），由 ``mcp_server.py`` **cp 副本**改造而来。

🔴 双协议：MCP(stdio) 给智能体，REST(HTTP) 给前端/脚本；两者共用同一套 ``Ctx`` 检索上下文，
   原版 ``mcp_server.py`` 一行未动。
🔴 只绑 127.0.0.1，不上云、不对外发布；demo 默认读仓库内脱敏小库，不碰生产 kb.db。
🔴 ``/ask`` 遵循「LLM 能力必建·开关可控」：默认 off，一键可开，开起来真能跑；
   凭据只走库外环境变量，出口门禁写在调用链必经处，缓存落盘、调用留痕、失败可降级。

启动：KB_LLM_ENABLED=0 python modules/知识库索引/api_server.py
接口：/health  /search  /stats  /ask
"""
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
STATE = os.environ.get("KB_DEMO_STATE") or os.path.join(_REPO, "examples", "state")
DEMO_DB = os.path.join(STATE, "demo.db")
# 🔴 必须在 import kb_index 之前设：DB_PATH 是模块级常量，导入时绑定；
#    Ctx(db_path=...) 只改属性，connect() 仍读 kb_index.DB_PATH（W2 复核脚本踩过）。
os.environ.setdefault("KB_DB_PATH", DEMO_DB)

from fastapi import FastAPI, HTTPException, Query       # noqa: E402
from fastapi.middleware.cors import CORSMiddleware     # noqa: E402
from fastapi.responses import FileResponse             # noqa: E402
from mcp_server import Ctx, _note_text                 # noqa: E402  复用副本来源的检索上下文

APP = FastAPI(title="KB RAG Engine · REST", version="1.0.0")
# 🔴 index.html 是「单文件双击即用」= file:// 页面（Origin: null），不开 CORS 会被浏览器挡死。
#    服务只绑 127.0.0.1 且只读，allow_origins=["*"] 的暴露面仍限本机。
APP.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET"], allow_headers=["*"])
class RestCtx(Ctx):
    """长驻 REST 服务的上下文：连接允许跨线程 + 由 ``_LOCK`` 串行访问。

    🔴 两个坑叠在一起（2026-11-10 实测，24 并发 12~13 个 500）：
       ① FastAPI 同步端点跑在 anyio 线程池，``Ctx._conn`` 由第一个请求的线程创建；
       ② sqlite3 默认 ``check_same_thread=True`` 禁止跨线程使用——**加串行锁没用**，
          锁只保证不并发，不保证同一线程（约一半请求恰好落在创建线程上才成功）。
       正解按 mcp_server.py 文档：「check_same_thread=False 仅供长驻进程跨请求复用连接，
       调用方必须自己保证串行使用」。两条一起满足，才既跨线程又不并发。
    🔴 用子类覆盖而不是改原版：mcp_server.py 挂着 MCP 定时任务，一字不动。
    """
    def _ensure(self):
        if self._kb_rag is not None or self._import_error is not None:
            return
        try:
            import kb_rag
            import kb_index
            self._kb_rag = kb_rag
            self._conn = kb_index.connect(check_same_thread=False)
        except Exception as e:
            self._import_error = str(e)


CTX = RestCtx()
_LOCK = threading.RLock()
HOST = "127.0.0.1"
PORT = int(os.environ.get("KB_API_PORT") or "8788")


def _search(query, topk=5, mode="fused", rerank=False):
    """真 searcher 冒烟点：直接走 Ctx → kb_rag.search_unified，不注入假 searcher。"""
    t0 = time.time()
    with _LOCK:
        res, notes = CTX.search(query, topk=topk, mode=mode, use_rerank=rerank)
        items = []
        for i, it in enumerate(res or [], 1):
            snippet = (it.get("snippet") or it.get("content") or it.get("text")
                       or it.get("summary") or CTX.snippet_for(it.get("path") or ""))
            items.append({
                "rank": i, "path": it.get("path", ""), "title": it.get("title", ""),
                "score": it.get("score"), "snippet": str(snippet)[:300],
                "coverage": it.get("coverage"), "category": it.get("category", ""),
                "source": it.get("source", ""), "matched": it.get("matched", []),
            })
    params = {"mode": mode, "topk": topk, "rerank": rerank,
              "db": os.path.basename(os.environ["KB_DB_PATH"]),
              "engine": "kb_rag.search_unified",
              "elapsed_ms": round((time.time() - t0) * 1000, 2)}
    return items, _note_text(notes), params


@APP.get("/", include_in_schema=False)
def index():
    """预览地址：curl http://127.0.0.1:8788/ 可直接抓回单文件 UI 做字段比对。

    index.html 也支持 file:// 双击（CORS 已开），两个入口等价。
    """
    return FileResponse(os.path.join(_REPO, "examples", "index.html"), media_type="text/html")


@APP.get("/health")
def health():
    with _LOCK:
        CTX._ensure()
    return {"status": "ok", "service": "kb-rest", "version": APP.version,
            "host": HOST, "db": os.path.basename(os.environ["KB_DB_PATH"]),
            "engine_ready": CTX._import_error is None,
            "llm_enabled": os.environ.get("KB_LLM_ENABLED", "0") == "1"}


@APP.get("/search")
def search(q: str = Query("", description="检索问句"),
           topk: int = Query(5, ge=1, le=50),
           mode: str = Query("kw", pattern="^(kw|fused|vec|substr)$"),
           rerank: bool = Query(False)):
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query 不能为空（参数名 q）")
    try:
        items, note, params = _search(q, topk, mode, rerank)
    except RuntimeError as e:            # 引擎不可用 → 语义明确的 503，不伪装成 0 条
        raise HTTPException(status_code=503, detail=str(e))
    body = {"query": q, "n_results": len(items), "results": items,
            "params": params, "note": note}
    if not items:                        # W4 终态默认：0 条返回空 + 提示，不过滤不放宽
        body["hint"] = "库内未检索到相关内容；已按原参数返回空结果，不编造、不放宽。"
    return body


@APP.get("/stats")
def stats():
    try:
        with _LOCK:
            s = CTX.stats()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    s["db"] = os.path.basename(os.environ["KB_DB_PATH"])
    s["bge_coverage"] = round(s["chunk_bge"] / s["chunks"], 4) if s["chunks"] else None
    return s


# ==================== /ask：LLM 能力必建，默认 off，一键可开 ====================
def _export_gate(items):
    """出口门禁（必经处）：隐私路径 / 已脱敏标记一律不许进模型上下文。

    🔴 标记文本不手抄：直接读 ``kb_rag.PRIVACY_EXPORT_MARK``，避免门禁写成
       另一个字符串而静默失效（写规则时最容易犯的「目检幻觉」）。
    """
    import kb_rag
    mark = kb_rag.PRIVACY_EXPORT_MARK
    for it in items:
        if kb_rag.is_privacy_path(it.get("path", "")) or mark in (it.get("snippet") or ""):
            raise HTTPException(status_code=451, detail="命中隐私出口门禁，拒绝外发该检索结果")
        # _enrich 已把隐私路径 snippet 换成标记；这里再挡一次，防调用方绕过
        if it.get("redacted"):
            raise HTTPException(status_code=451, detail="结果已被脱敏，拒绝外发")
    return True


def _cache_path(key):
    d = os.path.join(STATE, "llm_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, key + ".json")


def _trace(rec):
    """调用留痕：不含凭据、不含 prompt 正文。"""
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "llm_trace.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _llm_call(messages, model, base_url, key, timeout):
    payload = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                 data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]


@APP.get("/ask")
def ask(q: str = Query("", description="检索问句"),
        topk: int = Query(3, ge=1, le=10)):
    """检索 + 可选生成。**默认 off**：只返回检索包并说明怎么开；开后真调 OpenAI 兼容端点。"""
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query 不能为空（参数名 q）")
    items, note, params = _search(q, topk, "kw", False)
    enabled = os.environ.get("KB_LLM_ENABLED", "0") == "1"
    base = {"query": q, "results": items, "params": params, "note": note,
            "llm_enabled": enabled}
    if not enabled:
        base["answer"] = None
        base["hint"] = ("生成未启用（默认 off，零联网 / 零依赖 / 可复现）。"
                        "开启：设 KB_LLM_ENABLED=1 并配 KB_LLM_BASE_URL / OPENAI_API_KEY；"
                        "不配 key 时可指向本地 mock 端点验链路。")
        return base
    if not items:                        # 0 条不外发、不编造
        base["answer"] = "库内未检索到相关内容，不生成。"
        return base
    # ---- 开关打开后的完整链路：门禁 → 缓存 → 调用 → 留痕 → 降级 ----
    _export_gate(items)                  # 🔴 必经处，不靠调用方自觉
    key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("KB_LLM_BASE_URL", "")
    model = os.environ.get("KB_LLM_MODEL", "local-mock")
    if not base_url:
        base["answer"] = None
        base["error"] = "KB_LLM_BASE_URL 未配置；已降级为纯检索，不编造答案。"
        return base
    if not key and not ("127.0.0.1" in base_url or "localhost" in base_url):
        base["answer"] = None
        base["error"] = "非本地端点必须提供 OPENAI_API_KEY（库外环境变量）；已降级为纯检索。"
        return base
    context = "\n".join("[%d] %s\n%s" % (i, it["path"], it["snippet"])
                        for i, it in enumerate(items, 1))
    messages = [
        {"role": "system", "content": "只依据给定片段作答，逐条标注引用路径；片段里没有就说没有。"},
        {"role": "user", "content": "问题：%s\n\n片段：\n%s" % (q, context)},
    ]
    ck = hashlib.sha1(json.dumps([model, messages], ensure_ascii=False,
                                 sort_keys=True).encode("utf-8")).hexdigest()
    cp = _cache_path(ck)
    if os.path.exists(cp):               # 缓存命中 → 可复现，零重复调用
        with open(cp, encoding="utf-8") as f:
            cached = json.load(f)
        base.update({"answer": cached["answer"], "model": model,
                     "cache_hit": True, "citations": [it["path"] for it in items]})
        _trace({"ts": datetime.now().isoformat(timespec="seconds"), "sha1": ck,
                "model": model, "status": "cache_hit", "hits": len(items)})
        return base
    t0 = time.time()
    try:
        answer = _llm_call(messages, model, base_url, key,
                           float(os.environ.get("KB_LLM_TIMEOUT") or "10"))
        status, err = "ok", None
        with open(cp, "w", encoding="utf-8") as f:
            json.dump({"answer": answer}, f, ensure_ascii=False)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        answer, status, err = None, "degraded", str(e)  # 能力自动降级，别卡住
    base.update({"answer": answer, "model": model, "cache_hit": False,
                 "citations": [it["path"] for it in items],
                 "llm_ms": round((time.time() - t0) * 1000, 2),
                 "llm_status": status})
    if err:
        base["error"] = "生成端点不可用，已降级为纯检索：" + err
    _trace({"ts": datetime.now().isoformat(timespec="seconds"), "sha1": ck,
            "model": model, "status": status, "hits": len(items),
            "llm_ms": base["llm_ms"]})
    return base


if __name__ == "__main__":
    import uvicorn
    print("KB REST → http://%s:%d  db=%s  llm=%s" %
          (HOST, PORT, os.environ["KB_DB_PATH"],
           "on" if os.environ.get("KB_LLM_ENABLED") == "1" else "off"))
    uvicorn.run(APP, host=HOST, port=PORT, log_level="warning")
