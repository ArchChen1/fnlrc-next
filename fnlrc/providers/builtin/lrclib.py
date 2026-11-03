"""内置源：LRCLIB（纯原词库，多数条目没有译文）。

跟 `netease.py` 一样是**薄适配器**：真实逻辑仍是 `providers.lrclib_fetch`，
并且用函数体内 `from .. import …` 延迟取属性，保证测试里的 monkeypatch 有效。

LRCLIB 只给原词，且可能把罗马音混排进来 —— 用一个私有辅助把 `entries` 归一成
`pairs`（译文一律 None、罗马音从混排里拆出来）。
"""
from __future__ import annotations

from ..base import LyricResult


class LrclibProvider:
    name = "lrclib"
    label = "LRCLIB"
    priority = 20          # 网易云之后：它基本没有中文译文
    needs_key = False

    def available(self) -> tuple[bool, str]:
        return True, ""

    def fetch(self, song, cfg, *, deadline=None) -> LyricResult | None:
        from .. import lrclib_fetch   # 延迟取属性（见 netease.py docstring）

        got = lrclib_fetch(song.title, song.artist, song.album, song.duration,
                           expected=song.expected_lang, deadline=deadline)
        if not got or not got.get("entries"):
            return None
        if got.get("instrumental"):
            # 源站显式标记纯音乐 → 透传，让编排层走 skip_instrumental
            return LyricResult(source="lrclib", instrumental=True,
                               matched=got.get("matched") or "",
                               score=got.get("score") or 0.0,
                               lyric_lang=got.get("lyric_lang"))
        return LyricResult(
            source="lrclib",
            pairs=entries_to_pairs(got["entries"]),
            matched=got.get("matched") or "",
            score=got.get("score") or 0.0,
            lyric_lang=got.get("lyric_lang"),
            candidates=got.get("candidates") or [],
        )


def entries_to_pairs(entries: list[tuple[int, str]]) -> list[tuple[int, str, None, "str | None"]]:
    """LRCLIB 的 `entries`（[(ms, 文本)]）→ 标准 `pairs`。

    原文/罗马音混排的条目先用 `split_orig_roles` 拆开：原文进第 2 位、
    罗马音进第 4 位，译文恒为 None（源站不提供译文）。
    """
    from .. import split_orig_roles

    orig_lines, roman_lines = split_orig_roles(entries)
    roman_map: dict[int, str] = {}
    for ms, t in roman_lines:
        roman_map.setdefault(ms, t)
    return [(ms, o, None, roman_map.get(ms)) for ms, o in orig_lines]
