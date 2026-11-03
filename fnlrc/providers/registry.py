"""歌词源注册表。

内置源与用户插件都往这里注册，编排层只认注册表、不认具体源。
线程安全（监听后台线程可能并发触发取词）。
"""
from __future__ import annotations

import threading

from .base import LyricResult, Provider

_lock = threading.RLock()
_REGISTRY: "dict[str, Provider]" = {}


def register(p: Provider, *, replace: bool = False) -> None:
    """注册一个源。同名默认**不覆盖**（内置源先注册，用户插件不静默顶掉它）。

    想让插件顶替同名内置源，注册时传 replace=True。
    """
    name = getattr(p, "name", None)
    if not name or not isinstance(name, str):
        raise ValueError("Provider 必须有一个非空的字符串 name")
    with _lock:
        if name in _REGISTRY and not replace:
            return
        _REGISTRY[name] = p


def get(name: str) -> "Provider | None":
    with _lock:
        return _REGISTRY.get(name)


def all_providers() -> "list[Provider]":
    """按 priority 升序返回（越小越先试）。priority 相同则按 name 稳定排序。"""
    with _lock:
        items = list(_REGISTRY.values())
    return sorted(items, key=lambda p: (getattr(p, "priority", 999), getattr(p, "name", "")))


def names() -> list[str]:
    with _lock:
        return list(_REGISTRY.keys())


def reset() -> None:
    """清空注册表。仅测试用。"""
    with _lock:
        _REGISTRY.clear()


__all__ = ["register", "get", "all_providers", "names", "reset",
           "Provider", "LyricResult"]
