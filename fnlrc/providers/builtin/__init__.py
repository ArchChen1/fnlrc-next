"""内置歌词源。`register_builtins()` 幂等：重复调用不会重复注册（注册表默认不覆盖）。"""
from __future__ import annotations

from .lrclib import LrclibProvider
from .netease import NeteaseProvider


def register_builtins() -> None:
    """把内置源注册进注册表。已经存在同名源时静默跳过（`register` 的默认行为）。"""
    from ..registry import register

    register(NeteaseProvider())
    register(LrclibProvider())


__all__ = ["NeteaseProvider", "LrclibProvider", "register_builtins"]
