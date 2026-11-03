"""入口分发：桌面窗口 / 网页服务 / 命令行。

    fnlrc                    → 有 pywebview 就开窗口，否则起服务并打开浏览器
    fnlrc --server            → 网页版（默认只绑 127.0.0.1）
    fnlrc --desktop           → 桌面窗口（需要 pywebview + 系统 WebView2）
    fnlrc scan|run|report|…   → 命令行
"""
from __future__ import annotations

import sys


def _auto() -> int:
    """双击 exe / 不带参数时的默认行为。"""
    from . import cli
    try:
        # ⚠️ 模块名是 `webview`，不是 `pywebview` —— 后者是 PyPI 上的**包名**。
        # 写成 `import pywebview` 会永远 ImportError，于是双击 exe（= 不带参数启动）
        # 永远退回浏览器模式，原生窗口根本起不来。这个错误只在"没参数"这条路上，
        # 而 `--desktop` 走 desktop.run()，所以两条路的 import 名必须一致。
        import webview  # noqa: F401
        from . import desktop
        return desktop.run()
    except Exception:
        # 没有 pywebview（或系统缺 WebView2）→ 退回网页版，至少还能用
        return cli.serve(host="127.0.0.1", port=8080, open_browser=True)


def main(argv: list[str] | None = None) -> int:
    from . import cli
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _auto()
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
