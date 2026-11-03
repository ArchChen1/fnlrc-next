"""fnlrc · 飞牛音乐歌词补完器

多源刮削 + 双语合成 + 乱码修复 + 按飞牛规则回填。

模块分工（依赖方向严格单向，核心不知道 UI 的存在）：

    lrc.py        文本层：LRC 解析 / 规整 / 双语合成 / 飞牛双行规则校验
    library.py    文件层：扫描 / 标签 / 编码嗅探 / 纯音乐判定 / 库统计
    providers.py  外部层：网易云 · LRCLIB · OpenAI 兼容远端（DeepSeek）
    pipeline.py   编排层：单曲决策 + 批量执行（三端唯一入口）
    fnos.py       飞牛集成：只读探测它的歌词库（回填待做）
    store.py      持久层：任务/结果/回滚
    server.py     HTTP：FastAPI + SSE 进度 + 内置前端
    desktop.py    桌面壳：pywebview 原生窗口
    cli.py        命令行
"""

__version__ = "0.0.2"
__all__ = ["__version__"]
