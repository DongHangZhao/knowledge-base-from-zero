#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W8-A · 检索参数读取层：让 search_unified 的默认值来自注册表。

设计（🔴 边界：不改任何默认值 / 不动三链路 / 不动 RRF k=2 / 不动 rerank 默认）：
  1. 注册表 `rag_params.yaml` 是「默认值登记表」，不是调参开关；
  2. `get(name)` 读注册表 default；**模块加载时先跑等价性守卫**，若注册表的
     default 与改造前硬编码 `_FALLBACK` 不逐位一致，显式报警并回落 `_FALLBACK`
     —— 防止有人改了 yaml 就悄悄改了生产默认；
  3. 调参一律走环境变量覆盖 `resolve(name)` / `param_sweep.py --grid`，
     生产默认值一个不动；
  4. `snapshot()` 给出「这次结果是用哪组参数跑的」的 sha1，供 query_trace.py 回溯。

🔴 为什么要 fallback：kb_rag.py 是 20+ 脚本共用的生产入口，绝不能因为
   yaml 缺文件 / 手滑写坏 / 被别的窗口覆盖就让全库检索行为漂移。
   宁可报警回落，也不能静默改默认。

作者：FallenPetal ｜ 许可：KBL-1.0
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS_YAML = os.path.join(HERE, "rag_params.yaml")

# 改造前（2026-11-07 备份 kb_rag.py.bak-20261107-W8-pre）的硬编码值，逐位一致。
# 🔴 这张表是等价性守卫的基准，改它 = 改生产默认，必须先过 evidence_to_change。
_FALLBACK = {
    "rrf_k": 2,
    "kw_weight": 2.0,
    "vec_weight": 1.5,
    "sub_weight": 0.5,
    "rerank_on": True,
    "rerank_pool": 30,
    "topk": 5,
    "rewrite_level": "off",
    "chunk_size": 480,
    "chunk_overlap": 0,
    "category_relax": "off",
    "m3_route": False,
    "filter_pool": 20,
    "media_penalty": 0.5,
    "like_timeout": 2.0,
    "rewrite_fanout_w": 0.6,
}

# 参数名 → 环境变量名（与 rag_params.yaml 的 env_map 一致）
_ENV_MAP = {
    "rrf_k": "KB_RRF_K",
    "kw_weight": "KB_W_KW",
    "vec_weight": "KB_W_VEC",
    "sub_weight": "KB_W_SUBSTR",
    "rerank_pool": "KB_RERANK_POOL",
    "rewrite_level": "KB_REWRITE",
    "m3_route": "KB_VEC_ROUTE",
    "category_relax": "KB_FILTER_RELAX",
    "media_penalty": "KB_MEDIA_PENALTY",
    "rewrite_fanout_w": "KB_REWRITE_FANOUT_W",
}

_REG = None
_WARNED = False


def _warn(msg):
    """进度/告警一律走 stderr，绝不污染 --json 的 stdout。"""
    print("[rag_config] %s" % msg, file=sys.stderr)


def _read_registry():
    global _REG
    if _REG is not None:
        return _REG
    try:
        import yaml
        with open(PARAMS_YAML, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        params = {p["name"]: p for p in doc.get("params", [])}
        env_map = dict(doc.get("env_map") or {})
        _REG = {"params": params, "env_map": env_map, "version": doc.get("version"),
                "ok": True}
    except Exception as e:  # noqa: BLE001 - 注册表读坏也不能让检索挂掉
        _warn("注册表读取失败，回落内嵌等价默认值：%s" % str(e)[:120])
        _REG = {"params": {}, "env_map": {}, "version": None, "ok": False}
    return _REG


def verify_registry():
    """等价性守卫：注册表 default 必须与改造前硬编码逐位一致。

    返回 (ok, diffs)。不一致时不改注册表，只报警 + get() 回落，
    保证「注册表搬家」这件事本身不改变任何默认行为。
    """
    reg = _read_registry()
    diffs = []
    for name, hard in _FALLBACK.items():
        rec = reg["params"].get(name)
        if rec is None:
            diffs.append({"name": name, "why": "注册表缺项", "registry": None,
                          "fallback": hard})
            continue
        val = rec.get("default")
        if type(val) is not type(hard) or val != hard:
            diffs.append({"name": name, "why": "值与硬编码不一致",
                          "registry": val, "fallback": hard})
    if reg["env_map"] != _ENV_MAP:
        diffs.append({"name": "env_map", "why": "环境变量映射与读取层不一致",
                      "registry": reg["env_map"], "fallback": _ENV_MAP})
    return (not diffs), diffs


def get(name):
    """取注册表中登记的默认值（不含环境变量覆盖）。

    🔴 若该参数被守卫判为漂移，回落 `_FALLBACK`，生产行为不变。
    """
    if name not in _FALLBACK:
        raise KeyError("未登记的参数：%s" % name)
    global _WARNED
    ok, diffs = verify_registry()
    if not ok and not _WARNED:
        _warn("等价性守卫发现 %d 处漂移，已回落改造前硬编码（默认值未变）：%s"
              % (len(diffs), [d["name"] for d in diffs]))
        _WARNED = True
    drifted = {d["name"] for d in diffs}
    if name in drifted:
        return _FALLBACK[name]
    rec = _read_registry()["params"].get(name)
    return rec["default"] if rec is not None else _FALLBACK[name]


def resolve(name, env=None):
    """当前有效值：环境变量优先，否则注册表默认。供 sweep / trace 记录用。"""
    env = os.environ if env is None else env
    ev = _ENV_MAP.get(name)
    raw = env.get(ev) if ev else None
    if raw not in (None, ""):
        return raw
    return get(name)


def snapshot(env=None):
    """参数快照 + sha1（让「这次结果用哪组参数跑的」可追溯）。"""
    env = os.environ if env is None else env
    eff = {name: resolve(name, env) for name in _FALLBACK}
    blob = json.dumps(eff, sort_keys=True, ensure_ascii=False, default=str)
    return {"effective": eff,
            "hash": hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16],
            "registry_version": _read_registry().get("version"),
            "registry_ok": _read_registry().get("ok")}


def as_table():
    """给实验台账 / 结论页用：返回六列全表。"""
    reg = _read_registry()
    rows = []
    for name, hard in _FALLBACK.items():
        rec = reg["params"].get(name) or {}
        rows.append({
            "name": name,
            "default": hard,
            "range": rec.get("range", ""),
            "source": rec.get("source", ""),
            "killed": rec.get("killed", None),
            "evidence_to_change": rec.get("evidence_to_change", ""),
            "env": _ENV_MAP.get(name, ""),
        })
    return rows


def main():
    ok, diffs = verify_registry()
    snap = snapshot()
    print("注册表：%s ｜ version=%s ｜ 参数 %d 个" % (
        PARAMS_YAML, snap["registry_version"], len(_FALLBACK)))
    print("等价性守卫：%s ｜ 漂移 %d 处" % ("PASS（与改造前硬编码逐位一致）" if ok
                                     else "FAIL（已回落，默认值未变）", len(diffs)))
    for d in diffs:
        print("  - %s：%s registry=%r fallback=%r" % (
            d["name"], d["why"], d.get("registry"), d.get("fallback")))
    print("参数快照 hash=%s" % snap["hash"])
    for r in as_table():
        print("  %-18s default=%-6r killed=%-5s env=%s" % (
            r["name"], r["default"], r["killed"], r["env"] or "(无)"))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
