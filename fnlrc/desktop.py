"""pywebview 桌面壳。

主线程开原生窗口，后台线程起 uvicorn，绑 **127.0.0.1 的随机空闲端口**：

  · 随机端口：避免和用户机器上别的东西抢 8080
  · 就绪探测：窗口加载前先轮询 `/api/version`，成功再导航 —— 直接加载会白屏，
    这是打包后最常被报上来的故障
  · 原生文件夹对话框：通过 JS bridge 暴露 `pick_folder()`，前端优先用它，
    拿不到才退回 `/api/fs`

没装 pywebview（或系统缺 WebView2）时由 `__main__._auto()` 退回浏览器模式。
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("fnlrc")

WINDOW_TITLE = "fnlrc · 飞牛歌词补完器"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server(threading.Thread):
    """后台跑 uvicorn，窗口关掉时能优雅停。"""

    def __init__(self, port: int):
        super().__init__(daemon=True, name="fnlrc-server")
        self.port = port
        self._server = None

    def run(self) -> None:
        import uvicorn
        from .server import app

        config = uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        try:
            self._server.run()
        except Exception:
            log.exception("本地服务异常退出")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


def _wait_ready(port: int, timeout: float = 20.0) -> bool:
    """轮询 /api/version，就绪才让窗口导航。"""
    url = f"http://127.0.0.1:{port}/api/version"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    return False


class _Api:
    """暴露给前端的 JS bridge。只有原生能力走这里，业务一律走 HTTP。"""

    def __init__(self) -> None:
        self._window = None

    def bind(self, window) -> None:
        self._window = window

    def pick_folder(self) -> str:
        """弹原生目录选择框。返回空串表示用户取消。"""
        import webview

        if self._window is None:
            return ""
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return ""
        return result[0] if isinstance(result, (list, tuple)) else str(result)


def _setup_logging() -> None:
    """`console=False` 之后 stdout 不可见，日志一律落盘。"""
    from . import store

    try:
        d = store.data_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(d / "fnlrc.log"), level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s", encoding="utf-8")
    except Exception:
        logging.basicConfig(level=logging.WARNING)


def run() -> int:
    """启动桌面窗口。返回进程退出码。"""
    _setup_logging()
    try:
        import webview
    except ImportError:
        # 别说 `pip install pywebview` 就完事 —— 用 uv 建的 venv 里**没有 pip**，
        # 那条命令会以 "No module named pip" 失败，用户以为是装不上。
        print("没装 pywebview，改用浏览器模式（功能一样，只是没有原生窗口）。"
              "想开原生窗口就装它：\n"
              '  uv pip install --python .venv/Scripts/python.exe "pywebview>=5.0,<6"\n'
              "  （Windows 还需要系统里有 WebView2 Runtime，Win11 一般自带）",
              file=sys.stderr)
        from . import cli
        return cli.serve(host="127.0.0.1", port=8080, open_browser=True)

    from . import store

    store.get_token()          # 桌面版也要有令牌，写操作一样校验
    port = _free_port()
    server = _Server(port)
    server.start()

    if not _wait_ready(port):
        log.error("本地服务未能在 20s 内就绪（端口 %s）", port)
        print("本地服务启动失败，请看日志：", store.data_dir() / "logs" / "fnlrc.log",
              file=sys.stderr)
        server.stop()
        return 1

    api = _Api()
    window = webview.create_window(WINDOW_TITLE, f"http://127.0.0.1:{port}/",
                                   js_api=api, width=1180, height=820,
                                   min_size=(900, 640), background_color="#000000")
    api.bind(window)
    try:
        # 先探再加载：直接给 URL 会在服务没起来时白屏
        webview.start()
    except Exception:
        log.exception("桌面窗口异常")
        return 1
    finally:
        server.stop()
    return 0
