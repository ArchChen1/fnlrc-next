"""PyInstaller 打包入口。

不要直接把 ``fnlrc/__main__.py`` 交给 PyInstaller：它会被当成顶层脚本执行，
``from . import ...`` 会因缺少包上下文而报 ImportError。这里先以正常方式导入
``fnlrc`` 包，再调用包的入口函数。

Windows 的 ``console=False`` 单文件程序没有标准输出/错误流，Python 会把
``sys.stdout`` / ``sys.stderr`` 设为 ``None``。uvicorn 初始化日志时会调用
``sys.stderr.isatty()``，不补上流就会启动即崩。因此这里提供丢弃输出的替代流。
"""
from __future__ import annotations

import io
import sys

from fnlrc.__main__ import main


if __name__ == "__main__":
    # 必须在 uvicorn 配置日志之前补齐。console=True 时这两个流已经存在，
    # 不应替换，以免吞掉正常终端输出。
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, io.TextIOWrapper(io.BytesIO(), encoding="utf-8",
                                                errors="replace", write_through=True))
    sys.exit(main())