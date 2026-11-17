# KB Hub · Android 版

把 KB Hub 门户打进 Android APK：内嵌 Python 解释器 + 精简 HTTP 服务 + 前端页面，
安装后离线即可浏览示例知识库门户（系统卡片 / 技能卡 / 热度榜）。

## 形态与边界

- **完整自带后端**：p4a `webview` bootstrap 在手机内起一个 Python HTTP 服务（127.0.0.1:8799），
  App 内嵌 WebView 直接访问，不依赖电脑、不联网。
- **数据**：内置 `inventory.example.json`（全部虚构示例）。要换成自己的库，需重新构建时替换该文件。
- **移动端裁剪**：仅保留门户展示 API（`/`、`/api/inventory`、`/api/health`、`/api/heat`、`/api/record`）。
  需要调用本机脚本的功能（RAG 检索、视频下载、微信搜索、二维码服务、打开本机文件/目录）
  在手机上无意义，返回友好提示，不会导致崩溃。
- **热度记录**：存在内存中，App 重启后清零（不写 SD 卡，避免权限问题）。

## 构建

在 Linux（GitHub Actions `ubuntu-22.04`）上：

```bash
# 1. 系统依赖 + Android SDK/NDK（见 .github/workflows/build-apk.yml）
# 2. 导出 ANDROIDSDK / ANDROIDNDK / ANDROIDAPI / NDKAPI
pip install python-for-android cython==3.0.11
bash kb-hub/android/build_apk.sh
```

产物：`kb-hub/dist/kb-hub-android.apk`（arm64-v8a，debug 签名）。

## 与桌面版的关系

| 产物 | 平台 | 后端 |
|---|---|---|
| `kb-hub.exe` | Windows | 完整（`kb_hub_server.py`） |
| `kb-hub-linux` | Linux | 完整（`kb_hub_server.py`） |
| `kb-hub-macos` | macOS | 完整（`kb_hub_server.py`） |
| `kb-hub-android.apk` | Android | 精简（`android_server.py`） |

许可：KBL-1.0 ｜ 作者：落花飞雪· ｜ 项目：knowledge-base-from-zero
