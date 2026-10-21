#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_hub_server.py —— 知识库体系门户（后端）
================================================
一个零依赖（仅标准库）的本地 Web 服务，把零散的系统/技能卡片/项目/资源
统一聚合到一个仪表盘，并提供「每日热度启动台」：点用即记录，按使用频率
与热度每日排序，按需选取点用。

能力：
  GET  /                  仪表盘前端（web/index.html）
  GET  /api/health        健康检查
  GET  /api/inventory     知识库清单（扫描产物 inventory.json）
  GET  /api/usage         热度原始数据
  GET  /api/heat?limit=N  按热度排序的 Top 列表（默认 20）
  POST /api/launch        记录一次点用 + 执行打开动作 {id,name,kind,target,action}
  POST /api/record        仅记录一次点用（不打开）{id,name,kind,target}

打开动作（action）由后端在用户本机执行，因此只能本机访问：
  open_folder   打开目录
  open_file     打开文件（README / wiki md / 技能工作台 html）
  open_portal   启动 8787 知识库后台（start-kb8787.bat）
  copy_command  返回参考命令文本，前端写入剪贴板

用法：
  python kb_hub_server.py [--kb-root 路径] [--port 8799] [--no-browser]
"""
import os
import sys
import json
import datetime
import subprocess
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
# PyInstaller 冻结后资源在 _MEIPASS（只读临时目录），但可写数据要落在 exe 同级
BASE = getattr(sys, "_MEIPASS", HERE)
DATA_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else HERE
# 知识库根的默认值：源码运行 = 门户目录的父目录；exe 运行 = exe 所在目录
# （exe 应放在知识库根目录下，否则会错指向 PyInstaller 的临时解包目录）
DEFAULT_KB_ROOT = DATA_DIR if getattr(sys, "frozen", False) else os.path.dirname(HERE)


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load_inventory():
    # 依次找：可写目录（exe 同级 / 门户目录）→ 打包资源；缺失回退示例
    for base in (DATA_DIR, HERE):
        for name in ("inventory.json", "inventory.example.json"):
            p = os.path.join(base, name)
            if os.path.isfile(p):
                try:
                    return json.load(open(p, encoding="utf-8"))
                except Exception:
                    continue
    return {"summary": {}, "systems": [], "skills": [], "projects": [], "resources": [], "file_stats": {}}


class UsageStore:
    """热度记录：每个条目存使用时间戳列表，便于按时间衰减算热度。"""

    def __init__(self, path):
        self.path = path
        self.data = {"items": {}}
        if os.path.isfile(path):
            try:
                self.data = json.load(open(path, encoding="utf-8"))
                self.data.setdefault("items", {})
            except Exception:
                self.data = {"items": {}}

    def record(self, item_id, name, kind, target, max_uses=300):
        it = self.data["items"].setdefault(item_id, {"uses": [], "name": name, "kind": kind, "target": target})
        it["uses"].append(now_iso())
        it["name"] = name
        it["kind"] = kind
        it["target"] = target
        if len(it["uses"]) > max_uses:
            it["uses"] = it["uses"][-max_uses:]
        self.save()

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("[WARN] 保存 usage.json 失败:", e)

    def heat(self, uses):
        now = datetime.datetime.now()
        s = 0.0
        for t in uses:
            try:
                dt = datetime.datetime.fromisoformat(t)
                age = (now - dt).total_seconds() / 86400.0
                if age < 0:
                    age = 0
                s += max(0.0, 1.0 - age / 30.0)  # 30 天线性衰减
            except Exception:
                pass
        return round(s, 2)

    def top(self, limit=20, path_map=None):
        rows = []
        for iid, it in self.data["items"].items():
            uses = it.get("uses", [])
            last = uses[-1] if uses else None
            today = datetime.date.today().isoformat()
            used_today = any(u.startswith(today) for u in uses)
            target = it.get("target", "")
            # 历史记录里可能存了早期前端传坏的反斜杠路径；按 id 回查清单里的真实路径
            if path_map:
                for key in (iid, it.get("name", "")):
                    if key in path_map:
                        target = path_map[key]
                        break
            rows.append({
                "id": iid,
                "name": it.get("name", iid),
                "kind": it.get("kind", ""),
                "target": target,
                "count": len(uses),
                "heat": self.heat(uses),
                "last": last,
                "used_today": used_today,
            })
        rows.sort(key=lambda r: (-r["heat"], -r["count"]))
        return rows[:limit]


def normalize_target(target, kb_root=None):
    """把前端传来的各种形态的路径归一化成真实绝对路径。

    溯源：早期版本把 Windows 反斜杠路径直接拼进 onclick 的 JS 字符串，
    反斜杠被当成转义序列吃掉，导致「任何打开文件夹都提示找不到路径」。
    服务端这层做兜底：剥离引号/空白、斜杠统一、相对路径按知识库根解析。
    """
    kb_root = kb_root or DEFAULT_KB_ROOT
    t = "" if target is None else str(target)
    t = t.strip().strip('"').strip("'")
    if t in ("", "[object Object]", "undefined", "null", "None"):
        return ""
    # 统一成 Windows 反斜杠（Windows 也接受正斜杠，但 normpath 更稳）
    if os.sep == "\\":
        t = t.replace("/", "\\")
    if not os.path.isabs(t):
        t = os.path.join(kb_root, t)
    return os.path.normpath(t)


def path_hint(p, kb_root=None):
    """路径不存在时给可读诊断，而不是干巴巴一句「找不到路径」。"""
    hints = [f"路径不存在：{p}"]
    parent = os.path.dirname(p)
    if os.path.isdir(parent):
        base = os.path.basename(p)
        try:
            sibs = os.listdir(parent)
        except Exception:
            sibs = []
        near = [s for s in sibs if base and (base[:3] in s or s[:3] in base)][:5]
        hints.append("上级目录存在" + (f"，相近项：{'、'.join(near)}" if near else "，但目录下没有该项"))
    else:
        hints.append(f"上级目录也不存在：{parent}")
    hints.append("提示：可在「文件」页签手动浏览定位")
    return "｜".join(hints)


def open_path(path, kb_root=None):
    """打开文件或文件夹。返回 (ok, msg)。"""
    p = normalize_target(path, kb_root)
    if not p:
        return False, "未提供路径"
    # 展开 %USERPROFILE% 等（清单里可能带环境变量）
    p = os.path.expandvars(os.path.expanduser(p))
    if not os.path.exists(p):
        return False, path_hint(p, kb_root)
    try:
        if sys.platform.startswith("win"):
            os.startfile(p)  # noqa
        elif sys.platform == "darwin":
            subprocess.run(["open", p], check=False)
        else:
            subprocess.run(["xdg-open", p], check=False)
        return True, "已调用系统打开：" + p
    except Exception as e:
        return False, f"{e}｜{p}"


# ---------------- 工具页后端（视频下载 / 微信记录 / 二维码） ----------------
VIDEO_TASKS = {}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def python_cmd():
    """跑知识库脚本用的解释器。源码运行=当前解释器；exe 运行=找 PATH 里的 python。"""
    if not getattr(sys, "frozen", False):
        return [sys.executable]
    import shutil as _sh
    for name in ("python", "python3", "py"):
        p = _sh.which(name)
        if p:
            return [p]
    return []


def _script_dir(*parts):
    return os.path.join(DEFAULT_KB_ROOT, "modules", *parts)


def start_video_download(url):
    """后台跑 sph_download.py，日志实时累计到 VIDEO_TASKS[tid]。返回 (ok, tid|msg)。"""
    url = (url or "").strip().strip('"').strip("'")
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, "链接必须以 http(s):// 开头"
    py = python_cmd()
    if not py:
        return False, "未找到 Python 解释器（exe 模式需要系统已装 Python）"
    script = _script_dir("视频下载", "sph_download.py")
    if not os.path.isfile(script):
        return False, "未找到脚本：" + script

    tid = datetime.datetime.now().strftime("%H%M%S%f")
    rec = {"status": "running", "log": "", "started": now_iso(), "url": url}
    VIDEO_TASKS[tid] = rec

    def work():
        try:
            p = subprocess.Popen(
                py + [script, url], cwd=os.path.dirname(script),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=_NO_WINDOW,
            )
            for line in p.stdout:
                rec["log"] += line
                if len(rec["log"]) > 80000:
                    rec["log"] = rec["log"][-80000:]
            rc = p.wait()
            rec["status"] = "done" if rc == 0 else "error"
            rec["log"] += "\n[退出码 %s]\n" % rc
        except Exception as e:
            rec["status"] = "error"
            rec["log"] += "\n[异常] %s\n" % e

    threading.Thread(target=work, daemon=True).start()
    return True, tid


def run_wx_search(q, limit=20):
    """调 query_chatlog.py search 查本机 FTS5 库，返回 (ok, text)。"""
    q = (q or "").strip()
    if not q:
        return False, "关键词为空"
    py = python_cmd()
    if not py:
        return False, "未找到 Python 解释器"
    script = _script_dir("微信聊天记录库", "query_chatlog.py")
    if not os.path.isfile(script):
        return False, "未找到脚本：" + script
    try:
        limit = max(1, min(int(limit or 20), 100))
    except Exception:
        limit = 20
    try:
        r = subprocess.run(
            py + [script, "search", "--q", q, "--limit", str(limit)],
            cwd=os.path.dirname(script), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
            creationflags=_NO_WINDOW,
        )
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        return r.returncode == 0, (out.strip() or "（无输出）")[-9000:]
    except subprocess.TimeoutExpired:
        return False, "检索超时（>120s），换更具体的关键词"
    except Exception as e:
        return False, str(e)


def run_rag(q, mode="fused", topk=8, ask=False):
    """三链路统一检索：调 kb_rag.py（知识库 modules/知识库索引/）做
    FTS5 关键词 + BGE 向量 + 字级 substring 融合，或 ask 组装 RAG 上下文包。
    解释器与脚本路径从门户目录 rag.json 或环境变量取——本文件是开源模板，
    不写死任何本机路径；本地实例用 rag.json 配置（见 kb-hub/README）。"""
    q = (q or "").strip()
    if not q:
        return False, "empty query"
    cfg = {}
    cfg_path = os.path.join(DATA_DIR, "rag.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:
            cfg = {}
    py = cfg.get("python") or os.environ.get("KB_RAG_PYTHON")
    script = cfg.get("script") or os.environ.get("KB_RAG_SCRIPT")
    if not py or not script:
        return False, "RAG 未配置：在门户目录放 rag.json（{python, script}）或设环境变量 KB_RAG_PYTHON / KB_RAG_SCRIPT，详见 kb-hub/README"
    if not os.path.isfile(script):
        return False, "未找到检索脚本：" + script
    try:
        topk = max(1, min(int(topk or 8), 50))
    except Exception:
        topk = 8
    cmd = [py, script, "ask" if ask else "search", "--query", q,
           "--mode", mode or "fused", "--topk", str(topk), "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180,
                           creationflags=_NO_WINDOW)
        out = (r.stdout or "").strip()
        if r.returncode != 0:
            return False, ((r.stderr or out) or "kb_rag 执行失败")[-2000:]
        # kb_rag --json 只往 stdout 打一段 JSON；进度/警告在 stderr，直接截首尾大括号
        i, j = out.find("{"), out.rfind("}")
        if i < 0 or j <= i:
            return False, "kb_rag 输出不是 JSON：" + out[-500:]
        data = json.loads(out[i:j + 1])
        return True, data
    except subprocess.TimeoutExpired:
        return False, "检索超时（>180s），换更具体的关键词"
    except Exception as e:
        return False, str(e)


def port_open(port, host="127.0.0.1", timeout=0.5):
    import socket
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, int(port))) == 0
    finally:
        s.close()


def http_ok(port, path="/api/health", timeout=2.0):
    """裸 socket 发一次 GET，判断是否 200。
    不用 urllib/curl：本机可能配了 http_proxy，走代理会把「端口没起」误判成 502。"""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", int(port)), timeout=timeout)
    except Exception:
        return False
    try:
        s.settimeout(timeout)
        s.sendall(("GET %s HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n" % path).encode())
        first = b""
        while b"\r\n" not in first and len(first) < 200:
            chunk = s.recv(200)
            if not chunk:
                break
            first += chunk
        return b" 200 " in first.split(b"\r\n")[0]
    except Exception:
        return False
    finally:
        s.close()


def ensure_8787():
    """确保 8787 后台在跑（真活着=健康检查 200）；没跑就拉一个**独立进程**。"""
    if http_ok(8787):
        return True, "8787 已在运行"
    py = python_cmd()
    if not py:
        return False, "未找到 Python 解释器，无法启动 8787"
    script = _script_dir("知识库管理", "kb_manager.py")
    if not os.path.isfile(script):
        return False, "未找到 kb_manager.py"
    # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP：脱离调用方控制台/进程组，
    # 否则门户或工具会话一结束，8787 会被一起回收（症状：点二维码没反应）。
    flags = 0x00000008 | 0x00000200 | _NO_WINDOW
    try:
        subprocess.Popen(
            py + [script, "--port", "8787", "--no-browser"],
            cwd=os.path.dirname(script),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=flags,
        )
    except Exception as e:
        return False, "启动失败：%s" % e
    import time as _t
    for _ in range(50):
        _t.sleep(0.3)
        if http_ok(8787):
            return True, "8787 已启动"
    return False, "8787 启动超时，请手动双击 start-kb8787.bat 看报错"


def open_url(url):
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, "只允许打开 http(s) 地址"
    try:
        webbrowser.open(url)
        return True, "已在浏览器打开：" + url
    except Exception as e:
        return False, str(e)


def run_bat(path, kb_root=None):
    """启动 .bat（如 8787 知识库后台），在工作目录下静默起新窗口。"""
    p = normalize_target(path, kb_root)
    if not p or not os.path.isfile(p):
        return False, path_hint(p, kb_root)
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["cmd", "/c", "start", "", p], cwd=os.path.dirname(p), shell=False)
        else:
            subprocess.Popen([p], cwd=os.path.dirname(p))
        return True, "已启动：" + os.path.basename(p)
    except Exception as e:
        return False, str(e)


def allowed_root(p):
    """浏览/打开的安全边界：知识库根 与 ~/.workbuddy 两棵树之内。"""
    try:
        rp = os.path.realpath(p)
    except Exception:
        return False
    roots = [os.path.realpath(DEFAULT_KB_ROOT),
             os.path.realpath(os.path.join(os.path.expanduser("~"), ".workbuddy"))]
    for r in roots:
        if rp == r or rp.startswith(r + os.sep):
            return True
    return False


def browse_dir(path, kb_root=None, limit=600):
    """列目录，供前端内置文件浏览器使用。"""
    p = normalize_target(path, kb_root) or kb_root or DEFAULT_KB_ROOT
    if not os.path.isdir(p):
        return {"ok": False, "msg": path_hint(p, kb_root), "path": p}
    if not allowed_root(p):
        return {"ok": False, "msg": "超出可访问范围（仅知识库根与 ~/.workbuddy）", "path": p}
    dirs, files = [], []
    try:
        names = sorted(os.listdir(p))
    except Exception as e:
        return {"ok": False, "msg": str(e), "path": p}
    for name in names:
        if name.startswith(".") and name not in (".workbuddy",):
            continue
        fp = os.path.join(p, name)
        try:
            st = os.stat(fp)
        except Exception:
            continue
        if os.path.isdir(fp):
            dirs.append({"name": name, "path": fp, "mtime": int(st.st_mtime)})
        else:
            files.append({
                "name": name, "path": fp,
                "size": st.st_size,
                "ext": os.path.splitext(name)[1].lower(),
                "mtime": int(st.st_mtime),
            })
        if len(dirs) + len(files) > limit:
            break
    parent = os.path.dirname(p)
    kb = os.path.realpath(kb_root or DEFAULT_KB_ROOT)
    parent = parent if (os.path.realpath(parent).startswith(kb) or allowed_root(parent)) else ""
    return {"ok": True, "path": p, "parent": parent, "dirs": dirs, "files": files}


def open_portal(kb_root):
    bat = os.path.join(kb_root, "modules", "知识库管理", "start-kb8787.bat")
    if not os.path.isfile(bat):
        return False, "未找到启动器：" + bat
    return run_bat(bat, kb_root)


INVENTORY = load_inventory()
USAGE = UsageStore(os.path.join(DATA_DIR, "usage.json"))


def build_path_map(inv):
    """id / name → 真实路径。用于修正热度记录里存过的历史坏路径。"""
    m = {}
    for sec, idk, pk in (("systems", "id", "abs_path"), ("projects", "name", "abs_path"),
                         ("resources", "name", "abs_path"), ("zones", "id", "abs_path"),
                         ("entries", "id", "path"), ("skills", "id", "path")):
        for x in inv.get(sec, []) or []:
            k, p = x.get(idk), x.get(pk)
            if k and p:
                m[k] = p
    return m


PATH_MAP = build_path_map(INVENTORY)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默日志，避免刷屏

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path in ("/", "/index.html"):
            fp = os.path.join(BASE, "web", "index.html")
            if os.path.isfile(fp):
                self._send(200, open(fp, encoding="utf-8").read(), "text/html; charset=utf-8")
            else:
                self._send(404, "前端文件缺失：web/index.html")
            return
        if path == "/api/health":
            self._send(200, {"status": "up", "time": now_iso()})
            return
        if path == "/api/inventory":
            self._send(200, INVENTORY)
            return
        if path == "/api/usage":
            self._send(200, USAGE.data)
            return
        if path == "/api/heat":
            qs = urllib.parse.parse_qs(u.query)
            limit = int(qs.get("limit", ["20"])[0])
            self._send(200, {"items": USAGE.top(limit, PATH_MAP)})
            return
        if path == "/api/browse":
            qs = urllib.parse.parse_qs(u.query)
            target = qs.get("path", [""])[0]
            self._send(200, browse_dir(target, DEFAULT_KB_ROOT))
            return
        if path == "/api/config":
            self._send(200, {
                "kb_root": DEFAULT_KB_ROOT,
                "usage_path": USAGE.path,
                "inventory_generated_at": INVENTORY.get("generated_at", ""),
                "port": self.server.server_address[1],
                "python": python_cmd(),
            })
            return
        if path == "/api/tool/video/log":
            qs = urllib.parse.parse_qs(u.query)
            tid = qs.get("tid", [""])[0]
            rec = VIDEO_TASKS.get(tid)
            if not rec:
                self._send(404, {"ok": False, "msg": "任务不存在（服务可能重启过）"})
            else:
                self._send(200, {"ok": True, "status": rec["status"], "log": rec["log"], "url": rec.get("url", "")})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        body = self._read_json()
        if path == "/api/record":
            iid = body.get("id") or body.get("name")
            if iid:
                USAGE.record(iid, body.get("name", iid), body.get("kind", ""), body.get("target", ""))
            self._send(200, {"ok": True, "top": USAGE.top(20, PATH_MAP)})
            return
        if path == "/api/tool/video":
            ok, r = start_video_download(body.get("url", ""))
            self._send(200, {"ok": ok, "tid": r if ok else "", "msg": "" if ok else r})
            return
        if path == "/api/tool/wx":
            ok, text = run_wx_search(body.get("q", ""), body.get("limit", 20))
            self._send(200, {"ok": ok, "text": text if ok else "", "msg": "" if ok else text})
            return
        if path == "/api/tool/qr":
            ok, msg = ensure_8787()
            self._send(200, {"ok": ok, "msg": msg, "url": "http://127.0.0.1:8787/qr" if ok else ""})
            return
        if path == "/api/rag":
            ok, data = run_rag(body.get("q", ""), body.get("mode", "fused"),
                               body.get("topk", 8), bool(body.get("ask")))
            if ok:
                data["ok"] = True
                self._send(200, data)
            else:
                self._send(200, {"ok": False, "msg": data})
            return
        if path == "/api/launch":
            iid = body.get("id") or body.get("name")
            name = body.get("name", iid)
            kind = body.get("kind", "")
            target = body.get("target", "")
            action = body.get("action", "open_folder")
            if iid:
                USAGE.record(iid, name, kind, target)
            if action == "open_tools":
                ok, msg = open_url("http://127.0.0.1:%d/#tools" % self.server.server_address[1])
            elif action == "open_url":
                ok, msg = open_url(target)
            elif action == "open_portal":
                ok, msg = open_portal(DEFAULT_KB_ROOT)
            elif action == "run_bat":
                ok, msg = run_bat(target, DEFAULT_KB_ROOT)
            elif action == "open_folder":
                ok, msg = open_path(target, DEFAULT_KB_ROOT)
            elif action == "open_file":
                ok, msg = open_path(target, DEFAULT_KB_ROOT)
            elif action == "browse":
                ok, msg = True, "浏览：" + (normalize_target(target, DEFAULT_KB_ROOT) or DEFAULT_KB_ROOT)
            elif action == "copy_command":
                ok, msg = True, str(target) or "（无参考命令）"
            else:
                ok, msg = open_path(target, DEFAULT_KB_ROOT)
            self._send(200, {"ok": ok, "msg": msg, "top": USAGE.top(20, PATH_MAP)})
            return
        self._send(404, {"error": "not found"})


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb-root", default=None)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    global DEFAULT_KB_ROOT  # 声明后赋值，避免函数内提前引用
    if args.kb_root:
        DEFAULT_KB_ROOT = os.path.abspath(os.path.expanduser(args.kb_root))
    else:
        # 用清单里记录的真实知识库根（支持 ~ 展开；不存在则用默认值）
        cand = os.path.expanduser(INVENTORY.get("kb_root") or "")
        if cand and os.path.isdir(cand):
            DEFAULT_KB_ROOT = cand

    # 端口被占用时自动顺延，避免「页面打不开」
    server = None
    last_err = None
    for port in range(args.port, args.port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            args.port = port
            break
        except OSError as e:
            last_err = e
            print(f"[WARN] 端口 {port} 被占用，尝试下一个…")
    if server is None:
        print(f"[ERROR] 启动失败：{last_err}")
        print("        请关闭占用端口的程序，或用 --port 指定其他端口。")
        sys.exit(1)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"知识库体系门户已启动：{url}")
    print(f"  知识库根：{DEFAULT_KB_ROOT}")
    print(f"  清单摘要：systems={INVENTORY.get('summary', {}).get('systems')} "
          f"skills={INVENTORY.get('summary', {}).get('skills')} "
          f"projects={INVENTORY.get('summary', {}).get('projects')}")
    print("  关闭：Ctrl+C")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
