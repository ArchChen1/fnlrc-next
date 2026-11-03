# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows 单 exe）。

    .venv/Scripts/pyinstaller.exe --clean --noconfirm scripts/fnlrc.spec
    产物：dist/fnlrc.exe

三个必须做对的地方（DEPLOY.md 里也记了）：

1. **`datas` 必须包含 `fnlrc/web`** —— 否则打包后打开是白屏，
   因为服务端找不到 index.html。前端改成多文件之后，整个目录都要带上。
2. **uvicorn 要显式声明 hiddenimports** —— 它按字符串动态加载协议实现，
   冻结环境下自动探测会失败，表现是"启动即崩"。
3. **`console=False`** —— 否则会弹一个黑色控制台窗口。代价是 stdout 不可见，
   所以日志一律走 `%LOCALAPPDATA%\\fnlrc\\logs\\fnlrc.log`（fnlrc/desktop.py 里做了）。

路径一律从本文件位置推导，从仓库根目录或 scripts/ 下执行都能跑对。
"""
import os

from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))          # noqa: F821 (PyInstaller 注入)
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "fnlrc")

# 全是**动态导入**、冻结环境下探测不到的模块：
#   · uvicorn 按字符串加载协议实现，漏了就是"启动即崩"
#   · pywebview 按平台加载后端，漏了就只剩浏览器模式
#     （没装 pywebview 也能打包，只是 exe 会自动退回"打开浏览器"）
HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "fastapi",
    "starlette",
    *collect_submodules("uvicorn"),
    *collect_submodules("anyio"),
    *collect_submodules("httptools"),
    *collect_submodules("webview"),
]

a = Analysis(
    # PyInstaller 会把入口脚本当作顶层模块；必须用它来导入 fnlrc 包，
    # 不能直接执行 fnlrc/__main__.py，否则相对导入会失去包上下文。
    [os.path.join(HERE, "build_entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # ★ 前端必须打进包里，否则白屏
        (os.path.join(PKG, "web"), "web"),
    ],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="fnlrc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX 会显著提高杀软误报率，关掉
    runtime_tmpdir=None,
    console=False,              # ★ 无黑框；日志写文件
    disable_windowed_traceback=False,
    icon=os.path.join(HERE, "fnlrc.ico") if os.path.isfile(os.path.join(HERE, "fnlrc.ico")) else None,
)
