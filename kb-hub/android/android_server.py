#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
android_server.py —— KB Hub 门户的 Android 精简后端
=====================================================
kb_hub_server.py 的移动端裁剪版：复用同一份 web/index.html + inventory.example.json，
只保留「门户展示必需」的只读 API，砍掉所有 subprocess / os.startfile / webbrowser /
启动外部工具（rag、视频、wx、qr、open_portal）——手机上既无意义又会崩。

保留路由：
  GET  /                  门户前端
  GET  /api/health        健康检查
  GET  /api/inventory     知识库清单（示例数据）
  GET  /api/usage         热度原始数据
  GET  /api/heat?limit=N  热度 Top
  POST /api/record        记录一次点用（内存态，重启清零）
其余 /api/tool/* /api/rag /api/launch 返回 ok=False + 友好提示，前端不会崩。

设计约束（p4a / Android）：
  - 不调用 sys.stdout.reconfigure（Android stdout 可能无此方法）
  - 不写磁盘（数据内存态），避免 SD 卡权限问题
  - 绑定 127.0.0.1（webview bootstrap 通过本机回环访问）
"""
import os
import io
import sys
import json
import datetime
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Android 上 stdout 可能是精简对象，无 reconfigure —— 安全兜底
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
# p4a 把资源解包到当前工作目录 / private 目录；逐层找前端文件
def _find(name, *subdirs):
    roots = [HERE, os.getcwd(), os.path.dirname(HERE)]
    for root in roots:
        for sub in subdirs:
            p = os.path.join(root, sub, name) if sub else os.path.join(root, name)
            if os.path.isfile(p):
                return p
    return None


INDEX_HTML = _find("index.html", "web", "kb-hub/web", "") or ""
INVENTORY_FILE = _find("inventory.example.json", "", "kb-hub", "kb-hub/android") or ""


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load_inventory():
    if INVENTORY_FILE and os.path.isfile(INVENTORY_FILE):
        try:
            with open(INVENTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # 兜底最小清单，保证前端不空白
    return {
        "kb_root": "~/my-knowledge-base",
        "generated_at": "示例数据（Android 内置）",
        "summary": {"systems": 0, "skills": 0, "skills_openable": 0},
        "systems": [], "skills": [], "skill_categories": [],
        "projects": [], "resources": [], "entries": [], "zones": [], "sessions": [],
    }


INVENTORY = load_inventory()
USAGE = {"items": {}}  # 内存态：{id: {name,kind,target,uses:[iso,...]}}


def usage_top(limit=20):
    rows = []
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now()
    for iid, it in USAGE["items"].items():
        uses = it.get("uses", [])
        heat = 0.0
        for t in uses:
            try:
                age = (now - datetime.datetime.fromisoformat(t)).total_seconds() / 86400.0
                heat += max(0.0, 1.0 - max(0.0, age) / 30.0)
            except Exception:
                pass
        rows.append({
            "id": iid, "name": it.get("name", iid), "kind": it.get("kind", ""),
            "target": it.get("target", ""), "count": len(uses), "heat": round(heat, 2),
            "last": uses[-1] if uses else None,
            "used_today": any(u.startswith(today) for u in uses),
        })
    rows.sort(key=lambda r: (-r["heat"], -r["count"]))
    return rows[:limit]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

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
            if INDEX_HTML and os.path.isfile(INDEX_HTML):
                with open(INDEX_HTML, encoding="utf-8") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            else:
                self._send(200, "<!doctype html><meta charset='utf-8'><h2>KB Hub</h2><p>前端资源未打包</p>", "text/html; charset=utf-8")
            return
        if path == "/api/health":
            self._send(200, {"status": "up", "time": now_iso(), "platform": "android"})
            return
        if path == "/api/inventory":
            self._send(200, INVENTORY)
            return
        if path == "/api/usage":
            self._send(200, USAGE)
            return
        if path == "/api/heat":
            qs = urllib.parse.parse_qs(u.query)
            try:
                limit = int(qs.get("limit", ["20"])[0])
            except Exception:
                limit = 20
            self._send(200, {"items": usage_top(limit)})
            return
        if path == "/api/config":
            self._send(200, {
                "kb_root": INVENTORY.get("kb_root", ""),
                "usage_path": "(memory)",
                "inventory_generated_at": INVENTORY.get("generated_at", ""),
                "port": self.server.server_address[1],
                "python": "android",
            })
            return
        # 移动端不支持的只读类：返回空但结构正确
        if path == "/api/browse":
            self._send(200, {"path": "", "items": [], "note": "移动端不支持浏览本机目录"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        body = self._read_json()
        if path == "/api/record":
            iid = body.get("id") or body.get("name")
            if iid:
                it = USAGE["items"].setdefault(iid, {"name": "", "kind": "", "target": "", "uses": []})
                it["name"] = body.get("name", it["name"])
                it["kind"] = body.get("kind", it["kind"])
                it["target"] = body.get("target", it["target"])
                it["uses"].append(now_iso())
            self._send(200, {"ok": True, "top": usage_top(20)})
            return
        if path == "/api/launch":
            iid = body.get("id") or body.get("name")
            name = body.get("name", iid)
            action = body.get("action", "open_folder")
            if iid:
                it = USAGE["items"].setdefault(iid, {"name": "", "kind": "", "target": "", "uses": []})
                it["name"] = name or it["name"]
                it["uses"].append(now_iso())
            # copy_command 可在前端复制；其它打开动作移动端不支持
            if action == "copy_command":
                self._send(200, {"ok": True, "msg": str(body.get("target", ""))})
            elif action in ("open_tools",):
                self._send(200, {"ok": True, "msg": "已在门户内", "anchor": "#tools"})
            else:
                self._send(200, {"ok": False, "msg": "移动端不支持打开本机文件/目录"})
            return
        # tool/rag 类：明确告知不支持，前端 toast 友好提示
        msg = "该功能需本机知识库服务，Android 版暂不提供"
        if path in ("/api/tool/video", "/api/tool/wx", "/api/tool/qr"):
            self._send(200, {"ok": False, "msg": msg})
        elif path == "/api/tool/video/log":
            self._send(404, {"ok": False, "msg": "任务不存在"})
        elif path == "/api/rag":
            self._send(200, {"ok": False, "msg": msg})
        else:
            self._send(404, {"error": "not found"})


def main():
    port = 8799  # 与 buildozer.spec 的 android.port 一致
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
