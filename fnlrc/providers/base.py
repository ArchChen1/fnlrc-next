"""歌词源协议（Provider）—— 插件作者只需读这一个文件。

**职责边界**（别混淆）：

| 类别 | 例子 | 协议 |
|---|---|---|
| **歌词源 Provider** | 网易云、LRCLIB、QQ、酷狗…… | 本文件的 `Provider`，产出 `LyricResult` |
| **翻译后端 Translator** | 源站译词、DeepSeek / OpenAI 兼容 | 见 `providers.remote_translate`，不在这里 |

一个 Provider 要回答两件事：「现在能不能用？」（`available`）和
「这首歌的歌词是什么？」（`fetch`）。产出统一归一化成 `LyricResult.pairs`：

    [(ms, 原词, 译文 | None, 罗马音 | None), …]

**实现者必须遵守的三条**（否则会破坏全站约定）：

1. **尊重 `deadline`**：超过这个绝对时间点就尽早放弃（返回已拿到的或 None）。
2. **通过 `providers.base.req()` 发请求**：它带全局节流（同类源最小间隔）与凭据脱敏，
   绕过去会打爆源站、或在日志里泄露 key。
3. **失败返回 `None`，不要抛**：编排层会捕获异常继续下一个源，但"正常没查到"
   应当是 None，不要用异常表达。

最小实现骨架见 `docs/provider-template.py`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# 公共工具在这里再导出，插件作者 import 一个模块就够。
# （这些函数真正住在本包的顶层 __init__ 里，与既有代码/测试共用同一份实现。）
from . import (  # noqa: F401
    LANG_BONUS,
    duration_ok,
    lang_ok,
    mask_key,
    mask_secret,
    norm,
    redact_url,
    register_source_interval,
    req,
    score,
    sim,
    throttle,
    title_sim,
    version_penalty,
)

# 一对歌词行的标准形状：(毫秒, 原词, 译文, 罗马音)
Pair = tuple[int, str, "str | None", "str | None"]


@dataclass
class Candidate:
    """一个候选匹配的元信息（用于打分回显 / 报告 / UI 手动换版本）。"""

    matched: str                 # "艺人 - 歌名"，给用户核对用
    score: float                 # 归一化前的原始分（0-10）
    song_id: "str | int | None" = None       # 源内 id，便于二次取词
    duration_ms: int | None = None
    lyric_lang: str | None = None
    lines: int = 0
    effective: float | None = None           # 含语言加权的分（排序用）

    def as_dict(self) -> dict:
        d = {"matched": self.matched, "score": round(self.score, 2)}
        if self.song_id is not None:
            d["song_id"] = self.song_id
        if self.duration_ms:
            d["duration_ms"] = self.duration_ms
        if self.lyric_lang:
            d["lyric_lang"] = self.lyric_lang
        if self.lines:
            d["lines"] = self.lines
        if self.effective is not None:
            d["effective"] = round(self.effective, 2)
        return d


@dataclass
class LyricResult:
    """一次取词的归一化结果。**所有源都产出这个结构**。"""

    source: str                                  # 源标识，如 "netease" / "lrclib"
    pairs: list[Pair] = field(default_factory=list)
    matched: str = ""
    score: float = 0.0
    song_id: "str | int | None" = None
    lyric_lang: str | None = None
    instrumental: bool = False                   # 源站显式标记为纯音乐
    candidates: list[dict] = field(default_factory=list)   # 供 UI 手动换版本
    alternatives: dict = field(default_factory=dict)       # {ms: [其它译文候选]}

    def as_dict(self) -> dict:
        """转成 pipeline 期望的旧式 dict（保持向后兼容，不破坏既有调用方）。"""
        return {
            "source": self.source,
            "song_id": self.song_id,
            "matched": self.matched,
            "score": self.score,
            "lyric_lang": self.lyric_lang,
            "pairs": list(self.pairs),
            "candidates": self.candidates,
            "alternatives": self.alternatives,
            "has_translation": any(p[2] for p in self.pairs),
            # 兼容旧字段名（LRCLIB 时代用 entries）：编排层只读 pairs
            "entries": [(ms, o) for ms, o, _, _ in self.pairs],
            "instrumental": self.instrumental,
        }


@runtime_checkable
class Provider(Protocol):
    """歌词源协议。用 Protocol 而不是基类：插件不必 import 本项目的基类，鸭子类型即可。"""

    name: str                    # 唯一标识，如 "qq"
    label: str                   # 显示名，如 "QQ 音乐"
    priority: int                # 越小越先试（网易云=10, lrclib=20）
    needs_key: bool              # 是否需要 API key / cookie

    def available(self) -> "tuple[bool, str]":
        """这个源现在能不能用？→ (可用, 原因)。缺 key / 未配置 → (False, 原因)。"""
        ...

    def fetch(self, song, cfg, *, deadline=None) -> "LyricResult | None":
        """给定一首歌，返回归一化结果；拿不到返回 None。"""
        ...
