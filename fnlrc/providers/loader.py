"""外部插件的发现与加载。

插件目录（默认 `$FNLRC_DATA/plugins/`，可用配置 `plugins_dir` 覆盖）下：

    plugins/
    ├── qq.py              单文件
    └── musixmatch/
        └── __init__.py    包

每个候选模块按两种方式接入（都支持）：

1. 顶层直接 `registry.register(MyProvider())`（import 时的副作用）；
2. 或定义一个 `register()` 函数，由本模块调用。

**失败隔离**：任何单个插件 import 失败 / `register()` 抛异常，都只记日志、
继续加载下一个 —— 一个坏插件绝不能拖垮整个工具（验收标准 2）。不做沙箱，
但加载成功时打印来源路径，便于用户确认「我装的插件到底有没有被读到」。
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

from . import registry


def default_plugins_dir() -> Path:
    """默认插件目录。读取 `plugins_dir` 配置；留空则用 `$FNLRC_DATA/plugins`。"""
    from .. import store

    override = (store.load_config() or {}).get("plugins_dir")
    base = Path(override) if override else (store.data_dir() / "plugins")
    return base


def _candidates(directory: Path) -> list[Path]:
    """目录里可加载的插件入口：`*.py`（不含 `_` 开头）与含 `__init__.py` 的子目录。"""
    out: list[Path] = []
    if not directory.is_dir():
        return out
    for child in sorted(directory.iterdir()):
        name = child.name
        if name.startswith((".", "_")):
            continue
        if child.is_file() and child.suffix == ".py":
            out.append(child)
        elif child.is_dir() and (child / "__init__.py").is_file():
            out.append(child / "__init__.py")
    return out


def _load_one(path: Path, *, log=None) -> str | None:
    """import 一个插件文件并触发注册。成功返回模块名，失败返回 None（只记日志）。"""
    mod_name = f"fnlrc_plugin_{path.parent.name if path.name == '__init__.py' else path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"无法为 {path} 建立 import spec")
        module = importlib.util.module_from_spec(spec)
        # 先进 sys.modules，插件内部自引用 / 相对 import 才找得到自己
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    except Exception as e:
        sys.modules.pop(mod_name, None)
        if log:
            log(f"[provider] 插件加载失败 {path}：{type(e).__name__}: {e}")
            log(traceback.format_exc(limit=1).strip())
        return None

    # 方式 2：显式 register() 钩子（方式 1 的顶层副作用已经发生）
    hook = getattr(module, "register", None)
    if callable(hook):
        try:
            hook()
        except Exception as e:
            if log:
                log(f"[provider] 插件 register() 失败 {path}：{type(e).__name__}: {e}")
            return None

    # 兜底：模块顶层暴露的 Provider 实例，自动注册（register 幂等，不会重复）
    for attr in vars(module).values():
        if isinstance(attr, type):
            continue
        if all(hasattr(attr, a) for a in ("name", "fetch", "available")):
            try:
                registry.register(attr)
            except Exception:
                pass

    if log:
        log(f"[provider] 已加载插件 {path}")
    return mod_name


def load_plugins(directory: Path | str | None = None, *, log=None) -> list[str]:
    """加载目录下全部插件。返回成功加载的模块名列表（顺序即发现顺序）。"""
    d = Path(directory) if directory is not None else default_plugins_dir()
    loaded: list[str] = []
    for path in _candidates(d):
        name = _load_one(path, log=log)
        if name:
            loaded.append(name)
    return loaded
