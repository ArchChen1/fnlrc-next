"""内置源：网易云音乐。

**薄适配器，不重写逻辑** —— 真正的实现仍是 `providers.netease_fetch`（那套打分/
罗马音/译文挑选逻辑有大量测试与实测依据）。这里只把它包成 `Provider` 协议。

⚠️ 用**函数体内**的 `from .. import netease_fetch`（而不是顶层 import）：
顶层 import 会在加载时把函数对象固定住，之后 `monkeypatch.setattr(P, "netease_fetch", …)`
（测试里大量这么打桩）就失效了。函数体内的 import 每次调用都重新取模块属性，
打桩才会生效。
"""
from __future__ import annotations

from ..base import LyricResult


class NeteaseProvider:
    name = "netease"
    label = "网易云"
    priority = 10          # 先试它：中文译词质量最好
    needs_key = False

    def available(self) -> tuple[bool, str]:
        return True, ""

    def fetch(self, song, cfg, *, deadline=None) -> LyricResult | None:
        from .. import netease_fetch          # 见模块 docstring：延迟取属性，兼容打桩

        got = netease_fetch(song.title, song.artist, song.duration,
                            expected=song.expected_lang, deadline=deadline)
        if not got:
            return None
        pairs = list(got.get("pairs") or [])
        if not pairs:
            return None                       # 匹配到但没有可用行 → 让下一个源试
        return LyricResult(
            source="netease",
            pairs=pairs,
            matched=got.get("matched") or "",
            score=got.get("score") or 0.0,
            song_id=got.get("song_id"),
            lyric_lang=got.get("lyric_lang"),
            candidates=got.get("candidates") or [],
            alternatives=got.get("alternatives") or {},
        )
