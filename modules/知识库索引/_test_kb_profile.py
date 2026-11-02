#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_kb_profile — 人设配置矩阵的离线单测（零依赖、不动真实环境）

守什么（D1 的三条铁律，任一条破了这个模块就成了"悄悄改全局配置"的隐患）：
  ① 三档 key 集合完全一致（矩阵无遗漏：新加一个 KB_* 时必须三档都填）
  ② **显式 env 恒胜**：先 `KB_REWRITE=off`，再 `apply("enterprise")` → 仍是 `off`，
     且返回明细里 `from == "env"`（这是最容易写错成 `os.environ[k]=v` 的地方）
  ③ 三档**均不含** `*PRIVACY*` / `*CLOUD*` / `*TERMS*` 键（隐私红线不是可配置项）
  ④ `apply` 幂等：连调两次，环境与返回明细完全相同
  ⑤ **导入期零副作用**：`import kb_profile` 不改动任何环境变量
  ⑥ 非法档名回落 personal，不抛异常（配置文件写错不该让 CLI 崩）

跑法：python _test_kb_profile.py      （纯 stdlib，无 numpy/torch）
退出码：0 = 全 PASS；1 = 有 FAIL
"""
import copy
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    """打一条 [PASS]/[FAIL] —— run_tests.py:70 的正则依赖这个格式，不许改。"""
    global PASS, FAIL
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                         ("  ｜ " + str(detail)) if detail != "" else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


# ---------- ⑤ 导入期零副作用（必须在 import 之前拍快照）----------
_ENV_BEFORE = dict(os.environ)
import kb_profile as P  # noqa: E402
_ENV_AFTER = dict(os.environ)


def main():
    print("== 人设配置矩阵（kb_profile）==")

    # ⑤ 导入不改 env
    added = sorted(set(_ENV_AFTER) - set(_ENV_BEFORE))
    changed = sorted(k for k in _ENV_BEFORE if k in _ENV_AFTER and _ENV_BEFORE[k] != _ENV_AFTER[k])
    check("⑤ import kb_profile 不改动任何环境变量", not added and not changed,
          "新增 %s 变更 %s" % (added, changed))

    # ① 三档 key 集合一致
    base = set(P.PROFILES["personal"])
    same = all(set(m) == base for m in P.PROFILES.values())
    check("① 三档 key 集合一致", same,
          {n: len(m) for n, m in P.PROFILES.items()})
    check("①b 档位名齐全", sorted(P.PROFILES) == ["enterprise", "personal", "team"],
          sorted(P.PROFILES))
    check("①c 自检函数无问题", P.validate() == [], P.validate())

    # ② 显式 env 恒胜（整条铁律的核心断言）
    env = {}
    env["KB_REWRITE"] = "off"
    got = P.apply("enterprise", env=env)
    check("② env 已设 → 不被覆盖", env["KB_REWRITE"] == "off", env["KB_REWRITE"])
    check("②b 明细标记 from=env", got["KB_REWRITE"]["from"] == "env", got["KB_REWRITE"])
    check("②c 明细 value 反映真实生效值", got["KB_REWRITE"]["value"] == "off",
          got["KB_REWRITE"])
    check("②d 未设的键来自 profile",
          got["KB_BACKFILL_M3"] == {"value": "1", "from": "profile"},
          got["KB_BACKFILL_M3"])
    check("②e profile 值真的写进了 env", env.get("KB_BACKFILL_M3") == "1",
          env.get("KB_BACKFILL_M3"))

    # ② f 三档各自的关键差异（矩阵内容对不对，不只是结构对）
    personal = P.apply("personal", env={})
    team = P.apply("team", env={})
    ent = P.apply("enterprise", env={})
    check("②f personal：m3 auto / 无跨语向量 / 备份 3 天",
          (personal["KB_BACKFILL_M3"]["value"], personal["KB_VEC_ROUTE"]["value"],
           personal["KB_BACKUP_DAYS"]["value"]) == ("auto", "0", "3"),
          {k: personal[k]["value"] for k in ("KB_BACKFILL_M3", "KB_VEC_ROUTE", "KB_BACKUP_DAYS")})
    check("②g enterprise：m3=1 强制 / 跨语=1 / 备份 30 天",
          (ent["KB_BACKFILL_M3"]["value"], ent["KB_VEC_ROUTE"]["value"],
           ent["KB_BACKUP_DAYS"]["value"]) == ("1", "1", "30"),
          {k: ent[k]["value"] for k in ("KB_BACKFILL_M3", "KB_VEC_ROUTE", "KB_BACKUP_DAYS")})
    check("②h team 介于两者之间（备份 14 天 / 改写 l1）",
          (team["KB_BACKUP_DAYS"]["value"], team["KB_REWRITE"]["value"]) == ("14", "l1"),
          (team["KB_BACKUP_DAYS"]["value"], team["KB_REWRITE"]["value"]))
    check("②i KB_RERANK_DEVICE 三档一致 auto（有 GPU 用 GPU 策略不变）",
          {personal["KB_RERANK_DEVICE"]["value"], team["KB_RERANK_DEVICE"]["value"],
           ent["KB_RERANK_DEVICE"]["value"]} == {"auto"})
    check("②j KB_KG 三档一律 0（实验期）",
          {personal["KB_KG"]["value"], team["KB_KG"]["value"],
           ent["KB_KG"]["value"]} == {"0"})

    # ③ 隐私 / 云端 / 法务键不得进矩阵
    bad = [(n, k) for n, m in P.PROFILES.items() for k in m
           if any(p in k.upper() for p in P.FORBIDDEN_KEY_PARTS)]
    check("③ 矩阵不含 PRIVACY/CLOUD/TERMS 键", not bad, bad)
    check("③b 三档都不含 PRIVACY 字样（逐档确认）",
          all(not any("PRIVACY" in k.upper() for k in m) for m in P.PROFILES.values()))

    # ④ apply 幂等
    e1 = {}
    r1 = P.apply("team", env=e1)
    snap1 = dict(e1)
    r2 = P.apply("team", env=e1)
    check("④ apply 幂等（环境不变）", e1 == snap1)
    # 🔴 from 的语义要看准：第一次是 profile 铺进去的，第二次那个键**已经在 env 里**了，
    #    所以如实报 from="env"—— 这不是 bug，而是 setdefault 语义的必然结果
    #    （也说明了它为什么不会覆盖用户）。幂等要断言的是**生效值**相同。
    v1 = {k: v["value"] for k, v in r1.items()}
    v2 = {k: v["value"] for k, v in r2.items()}
    check("④b apply 幂等（生效值不变）", v1 == v2, {"first": v1, "second_differs": {
        k: (v1[k], v2[k]) for k in v1 if v1[k] != v2[k]}})
    check("④c 二次调用如实报 from=env（setdefault 语义，不是覆盖）",
          all(v["from"] == "env" for v in r2.values()),
          sorted({v["from"] for v in r2.values()}))

    # ⑥ 非法档名回落 personal，不抛
    fell = P.active({"KB_PROFILE": "no-such-profile"})
    check("⑥ 非法 KB_PROFILE 回落 personal", fell == "personal", fell)
    e2 = {}
    r3 = P.apply("no-such-profile", env=e2)
    check("⑥b apply 非法档名不抛且按 personal 铺",
          r3["KB_BACKUP_DAYS"]["value"] == "3", r3.get("KB_BACKUP_DAYS"))
    check("⑥c active 读 KB_PROFILE", P.active({"KB_PROFILE": "team"}) == "team")

    # ⑦ 真实环境不被本测试污染（只传 env= dict，没碰 os.environ）
    leaked = [k for k in P.PROFILES["personal"] if k in os.environ
              and os.environ.get(k) != _ENV_BEFORE.get(k)]
    check("⑦ 测试未污染真实 os.environ", not leaked, leaked)

    # ⑧ CLI 端到端（子进程，验证 --apply/--json/--show/--check 真能跑）
    script = os.path.join(HERE, "kb_profile.py")
    p = subprocess.run([sys.executable, script, "--apply", "enterprise", "--json"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    ok_json = False
    detail = p.returncode
    if p.returncode == 0:
        try:
            d = json.loads(p.stdout)
            ok_json = d["KB_BACKFILL_M3"]["value"] == "1" and d["KB_BACKFILL_M3"]["from"] == "profile"
            detail = d["KB_BACKFILL_M3"]
        except Exception as e:
            detail = "json 解析失败：%s" % str(e)[:60]
    check("⑧ CLI `--apply enterprise --json` 输出可解析且值正确", ok_json, detail)

    p2 = subprocess.run([sys.executable, script, "--check"],
                        capture_output=True, text=True, encoding="utf-8", timeout=60)
    check("⑧b CLI `--check` rc=0", p2.returncode == 0,
          (p2.stdout or p2.stderr).strip().splitlines()[-1:] or "")

    p3 = subprocess.run([sys.executable, script, "--show"],
                        capture_output=True, text=True, encoding="utf-8", timeout=60)
    check("⑧c CLI `--show` 打印三档表头",
          p3.returncode == 0 and "personal" in p3.stdout and "enterprise" in p3.stdout
          and "KB_BACKFILL_M3" in p3.stdout)

    print("-" * 56)
    print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
