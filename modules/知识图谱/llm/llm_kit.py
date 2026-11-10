"""LLM 能力层 · 五件套（client / redact / cache / trace / 降级）。

口径：AGENTS.md §5.11 + 准则 §39 v3.65 —— **能力必建、开关可控**。
- 默认 off：`KG_LLM_ENABLED != "1"` 或没有 key → 不发起任何网络请求，返回 degraded。
- 凭据只读库外环境变量（AGNES_API_KEY / OPENAI_API_KEY），本文件、本目录任何文件都不存 key。
- 出口门禁：调用前必须过 `prepare()`，硬拦命中直接抛 ExportBlocked，一个字节都不发。
- 可复现：结果按 (model+system+prompt) 的 sha1 落盘缓存；关掉网络仍可重放。
- 留痕：state/llm_trace.jsonl，只记元信息，不记正文与凭据。

只用标准库，避免为这一层再引依赖。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE = "https://apihub.agnes-ai.com/v1"
DEFAULT_MODEL = "agnes-3.0-flash"
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
CACHE_DIR = STATE_DIR / "llm_cache"
TRACE_FILE = STATE_DIR / "llm_trace.jsonl"

# 免费/共享端点普遍有 QPS 限制（实测 agnes 网关 8 连发后 429）。
# 主动限速 + 尊重 Retry-After，比事后重试便宜得多。
_last_call = [0.0]


class ExportBlocked(RuntimeError):
    """出口门禁拦下：内容含不可外发成分。"""


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return default


def settings() -> dict:
    """一屏看清当前开关状态；cli.py --status 直接打印它（key 只报有/无）。"""
    key = _env("AGNES_API_KEY", "OPENAI_API_KEY")
    return {
        "enabled": _env("KG_LLM_ENABLED", default="0") == "1",
        "has_key": bool(key),
        "key": key,
        "base": _env("KG_LLM_BASE_URL", default=DEFAULT_BASE).rstrip("/"),
        "model": _env("KG_LLM_MODEL", default=DEFAULT_MODEL),
        "timeout": float(_env("KG_LLM_TIMEOUT", default="30")),
        "retries": int(_env("KG_LLM_RETRIES", default="3")),
        "min_interval": float(_env("KG_LLM_MIN_INTERVAL", default="2.5")),
        # W12 · D：缓存 TTL（秒），0 = 永不过期（默认，保证「关掉开关仍可复现」）。
        "cache_ttl": float(_env("KG_LLM_CACHE_TTL", default="0")),
        # W12 · D：提示词版本只由调用方显式传（见 prompt_version_of），默认空 = 键逐位不变，
        # 不让别的窗口（hyde_cmd / w9_eval）已落盘的缓存一夜之间全失效。
        "prompt_version_default": "",
        "cache_dir": str(CACHE_DIR),
        "trace_file": str(TRACE_FILE),
    }


# ---------------------------------------------------------------- 出口门禁
# 硬拦：命中即拒发（宁可误拦，不可漏发 —— 准则 §37.16 判据）
_HARD = [
    (re.compile(r"raw[/\\](个人信息|隐私)"), "隐私目录路径"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "身份证号"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "手机号"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "疑似 API Key"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "疑似哈希/长十六进制凭据"),
    (re.compile(r"(住院号|病历号|门诊号|患者姓名|身份证|银行卡号)\s*[:：]"), "业务主键字段"),
]

# 软替换：脱掉定位信息，保留语义（路径/邮箱/内网地址）
_SOFT = [
    (re.compile(r"[A-Za-z]:[\\/][^\s，。；：)）\]]+"), "<本地路径>"),
    (re.compile(r"\\\\[^\s，。；：)）\]]+"), "<网络路径>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<邮箱>"),
    (re.compile(r"\b(?:10|172|192\.168)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<内网地址>"),
]


# 🔴 结构级硬拦（2026-11-10 加）：正则打不过**表格式实名明细**。
#    起因：`raw/微信文件/项目/2026-11-09-26年10月多条手术的患者.xls` 修好读取后
#    才进得了库（表头 `CASEID / OPERID / OPER_NAME / SURGEON / ASA / BEGINTIME`），
#    60 行手术明细里没有一个「身份证」「手机号」「住院号：」字样 —— 现有 6 条正则
#    一条都不命中。它照样能搜到、也照样能被送去外发。
#    判据不看词，看**形状**：多行、每行 ≥3 个 Tab 分隔字段、表头出现实名/病案类主键。
_ROSTER_KEYS = ("姓名", "患者", "病人", "学号", "工号", "CASEID", "病历", "住院号", "诊断")
_ROSTER_MIN_ROWS = 6


def _looks_like_roster(text: str) -> int:
    """返回「多行表格行数」，0 = 不像实名明细表。"""
    rows = 0
    head_hit = False
    for i, line in enumerate(text.splitlines()[:400]):
        if line.count("\t") >= 2:
            rows += 1
            if i < 8 and any(k in line.upper() for k in _ROSTER_KEYS):
                head_hit = True
    return rows if (rows >= _ROSTER_MIN_ROWS and head_hit) else 0


def prepare(text: str) -> dict:
    """出口前处理。返回 {text, redacted, hits}；硬拦抛 ExportBlocked。"""
    hard = [why for pat, why in _HARD if pat.search(text)]
    n_rows = _looks_like_roster(text)
    if n_rows:
        hard.append("实名明细表（%d 行表格）" % n_rows)
    if hard:
        raise ExportBlocked("拒绝外发：" + "、".join(sorted(set(hard))))
    out, soft_hits = text, []
    for pat, rep in _SOFT:
        new = pat.sub(rep, out)
        if new != out:
            soft_hits.append(rep)
            out = new
    return {"text": out, "redacted": bool(soft_hits), "hits": soft_hits}


# ------------------------------------------------------------------- 缓存
def prompt_version_of(*parts: str) -> str:
    """W12 · D：把提示词模板本身做成版本号。

    模板一改，hash 就变，缓存键随之变 —— 「改提示词自动失效」不靠手工 drop。
    调用方显式传 chat(..., prompt_version=prompt_version_of(_SYS_A, _SYS_B))。
    """
    raw = "\x00".join(p or "" for p in parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def cache_key(model: str, system: str, prompt: str, prompt_version: str = "") -> str:
    """键 = model + system + prompt [+ prompt_version]。

    🔴 兼容纪律：prompt_version 为空时拼串与改造前逐位一致（3 段），
    保证既有缓存与其它窗口的回放不受影响；只有显式传版本才追加第 4 段。
    """
    parts = [model, system or "", prompt]
    if prompt_version:
        parts.append(prompt_version)
    raw = "\x00".join(parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:24]


def cache_get(key: str, ttl: float = 0.0) -> dict | None:
    """ttl>0 时按缓存文件 mtime 判过期；默认 0 = 永不过期（可复现优先）。"""
    p = CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    if ttl > 0:
        try:
            if (time.time() - p.stat().st_mtime) > ttl:
                return None
        except OSError:
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cache_put(key: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f"{key}.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_DIR / f"{key}.json")


def cache_drop(key: str) -> bool:
    """上层解析失败时主动作废缓存，避免坏结果被永久复现。"""
    p = CACHE_DIR / f"{key}.json"
    if p.exists():
        p.unlink()
        return True
    return False


def extract_json(text: str):
    """模型爱在 JSON 外面包 ``` 或解释句，按「先整体、再首个 {...}/[...]」两段解析。"""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        m = re.search(r"[\[{].*[\]}]", cleaned, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


# ------------------------------------------------------------------- 留痕
def trace(record: dict) -> None:
    """只记元信息。调用方保证里面没有正文、没有 key。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), **record}
    with TRACE_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------- 调用
def _throttle(min_interval: float) -> None:
    """全局限速：同一进程内相邻两次网络调用至少隔 min_interval 秒。"""
    wait = _last_call[0] + min_interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def _post(url: str, body: dict, key: str, timeout: float) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(prompt: str, system: str = "", task: str = "", *, temperature: float = 0.0,
         max_tokens: int = 512, prompt_version: str = "") -> dict:
    """单轮补全。返回统一结构，**任何失败都不抛给调用方**（降级而非中断，§5 L2）。

    {ok, text, degraded, cached, reason, latency_ms, usage}
    prompt_version: W12 · D，由调用方用 prompt_version_of(模板...) 显式传入；
                    空 = 兼容旧键（不传版本的历史调用仍命中原缓存）。
    """
    cfg = settings()
    key = cache_key(cfg["model"], system, prompt, prompt_version)
    t0 = time.perf_counter()

    if cached := cache_get(key, cfg["cache_ttl"]):
        trace({"task": task, "model": cfg["model"], "cache_key": key,
               "cached": True, "degraded": False, "latency_ms": 0,
               "prompt_version": prompt_version,
               "prompt_chars": len(prompt)})
        return {**cached, "ok": True, "cached": True, "degraded": False,
                "reason": "cache", "latency_ms": 0, "cache_key": key}

    if not cfg["enabled"]:
        return _degraded(key, task, t0, "disabled: KG_LLM_ENABLED != 1")
    if not cfg["has_key"]:
        return _degraded(key, task, t0, "no key: 请设置 AGNES_API_KEY（库外环境变量）")

    try:
        safe = prepare(prompt)
    except ExportBlocked as exc:
        trace({"task": task, "model": cfg["model"], "cache_key": key, "cached": False,
               "degraded": True, "reason": "export_blocked", "detail": str(exc),
               "prompt_chars": len(prompt)})
        return {"ok": False, "text": "", "degraded": True, "cached": False,
                "reason": f"export_blocked: {exc}", "latency_ms": 0, "usage": {},
                "cache_key": key}

    body = {
        "model": cfg["model"],
        "messages": ([{"role": "system", "content": system}] if system else [])
        + [{"role": "user", "content": safe["text"]}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    last = "unknown"
    for attempt in range(cfg["retries"] + 1):
        backoff = 0.0
        try:
            _throttle(cfg["min_interval"])
            raw = _post(cfg["base"] + "/chat/completions", body, cfg["key"], cfg["timeout"])
            text = (raw.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
            payload = {"text": text.strip(), "usage": raw.get("usage", {}),
                       "model": raw.get("model", cfg["model"])}
            cache_put(key, payload)
            ms = int((time.perf_counter() - t0) * 1000)
            trace({"task": task, "model": payload["model"], "cache_key": key,
                   "cached": False, "degraded": False, "latency_ms": ms,
                   "prompt_version": prompt_version, "redacted": safe["redacted"],
                   "prompt_chars": len(prompt), "usage": payload["usage"],
                   "attempt": attempt + 1})
            return {**payload, "ok": True, "cached": False, "degraded": False,
                    "reason": "ok", "latency_ms": ms, "cache_key": key}
        except urllib.error.HTTPError as exc:
            last = f"http_{exc.code}"
            if exc.code in (401, 403):
                break
            if exc.code not in (408, 409, 429, 500, 502, 503, 504):
                break
            if exc.code == 429:
                try:
                    backoff = min(float(exc.headers.get("Retry-After") or 0), 30.0)
                except (TypeError, ValueError):
                    backoff = 0.0
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"net: {type(exc).__name__}"
        except ValueError as exc:
            last = f"bad_json: {exc}"
            break
        if attempt < cfg["retries"]:
            time.sleep(max(backoff, min(2 ** (attempt + 1), 16)))

    return _degraded(key, task, t0, last, prompt_chars=len(prompt))


def _degraded(key: str, task: str, t0: float, reason: str, prompt_chars: int = 0) -> dict:
    ms = int((time.perf_counter() - t0) * 1000)
    trace({"task": task, "cache_key": key, "cached": False, "degraded": True,
           "reason": reason, "latency_ms": ms, "prompt_chars": prompt_chars})
    return {"ok": False, "text": "", "degraded": True, "cached": False,
            "reason": reason, "latency_ms": ms, "usage": {}, "cache_key": key}
