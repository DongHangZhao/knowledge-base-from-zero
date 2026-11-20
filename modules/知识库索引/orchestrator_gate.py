#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W19 · orchestrator 双控开关（环境变量 + 注册表）与漂移报警。

🔴 为什么独立成模块、不塞进 rag_config.py：
  rag_config._FALLBACK 是「改造前硬编码基准」，verify_registry() 遍历它做等价性守卫。
  orchestrator 是 W19 **新增**能力（无「改造前硬编码」基准），塞进 _FALLBACK 会
  被误判成「改了生产默认」而触发守卫报警；且 W19 独占 kb_rag.py，独立模块让
  diff 只落在「新文件 + kb_rag.py 新增参数/分支」，既有 16 项参数零改动。

口径（00 §一裁决3 / §五末 / W19 卡 §二 B）：
  · 默认 classic（接入 ≠ 切默认）；agentic 只在本模块显式开启时生效。
  · 双控优先级：**环境变量 KB_RETRIEVE_MODE > 注册表 default**。
    env 为空 → 读注册表；两者都给且取值不同 → 报冲突告警，env 胜（保证「一行 env 回滚」有效）。
  · 注册表漂移（yaml 的 default 非法 / env_map 指错）→ 报警 + 回落 classic，不改注册表。

作者：FallenPetal ｜ 许可：KBL-1.0
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

VALID = ("classic", "agentic")
ENV_NAME = "KB_RETRIEVE_MODE"
DEFAULT = "classic"
# 注册表登记项（独立 namespace，不进 rag_config._FALLBACK）
REG_KEY = "orchestrator"
REG_DEFAULT = DEFAULT

_WARNED = False


def _warn(msg):
    print("[orchestrator_gate] %s" % msg, file=sys.stderr)


def _registry_value():
    """读 rag_params.yaml 里 orchestrator.default；读不到返回 None（非致命）。"""
    try:
        import yaml
        with open(os.path.join(HERE, "rag_params.yaml"), encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        orch = doc.get("orchestrator")
        if not isinstance(orch, dict):
            return None
        return orch.get("default")
    except Exception as e:  # noqa: BLE001 - 注册表读坏不能让检索挂
        _warn("注册表读取失败，回落默认 classic：%s" % str(e)[:100])
        return None


def verify_registry():
    """漂移守卫：注册表 default 必须是合法值且等于登记默认。返回 (ok, reason)。"""
    val = _registry_value()
    if val is None:
        return True, "未登记（用内嵌默认 classic，不报警）"
    if val not in VALID:
        return False, "注册表 orchestrator.default=%r 非法（应属 %s）" % (val, VALID)
    if val != REG_DEFAULT:
        return False, "注册表 orchestrator.default=%r 与登记默认 %r 不一致" % (val, REG_DEFAULT)
    return True, "ok"


def resolve(env=None):
    """当前有效 orchestrator。env 优先，否则注册表 default，否则 classic。

    返回 dict：value / source / conflict。conflict 非空表示 env 与注册表取值冲突。
    """
    global _WARNED
    env = os.environ if env is None else env
    ok, reason = verify_registry()
    if not ok and not _WARNED:
        _warn("漂移告警：%s；已回落默认 classic（不改注册表）" % reason)
        _WARNED = True
        return {"value": DEFAULT, "source": "fallback-drift", "conflict": reason}

    reg = _registry_value() or DEFAULT
    raw = env.get(ENV_NAME)
    raw = raw.strip().lower() if isinstance(raw, str) else None
    conflict = ""
    if raw and raw not in VALID:
        _warn("%s=%r 非法，回落注册表默认 %r" % (ENV_NAME, raw, reg))
        return {"value": reg, "source": "registry", "conflict": "env 非法: %s" % raw}
    if raw and reg and raw != reg:
        conflict = "%s=%s 与注册表 default=%s 冲突，按 env 生效（保证一行回滚）" % (ENV_NAME, raw, reg)
        _warn(conflict)
    value = raw if raw else reg
    if value not in VALID:
        value = DEFAULT
    return {"value": value, "source": "env" if raw else "registry", "conflict": conflict}


if __name__ == "__main__":
    print(resolve())
