#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py —— p4a webview bootstrap 入口
=====================================
webview bootstrap 会在本机起这个 Python 程序，并在 app 内嵌 WebView 里
自动加载 http://127.0.0.1:<port>。所以这里只需要在后台线程把 HTTP 服务跑起来。
端口必须与 buildozer.spec 的 android.port 一致（默认 8799）。

注意：webview bootstrap 的 main.py 不能阻塞退出——serve_forever 常驻即可，
bootstrap 自己管理 WebView 生命周期。
"""
import threading
from android_server import main as run_server

if __name__ == "__main__":
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    # 保持主线程存活；serve_forever 在子线程里常驻
    t.join()
