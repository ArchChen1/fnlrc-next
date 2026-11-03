"""共用夹具：LRC 文本构造器 + 路径。

用例直接对应 `docs/TESTING.md` 的边界表与 `docs/TECH-NOTES.md` 1.3 的实测记录。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ---------------------------------------------------------------- 时间戳
def ts(sec: int, frac: str = "00", style: str = "std") -> str:
    """按不同写法生成时间戳。"""
    mm, ss = divmod(sec, 60)
    if style == "std":
        return f"[{mm:02d}:{ss:02d}.{frac}]"
    if style == "fullwidth":
        return f"［{mm:02d}:{ss:02d}.{frac}］"
    if style == "minute_only":
        return f"[{mm:02d}:{ss:02d}]"
    if style == "colon_frac":
        return f"[{mm:02d}:{ss:02d}:{frac}]"
    raise ValueError(style)


# ---------------------------------------------------------------- 构造器
def interleaved(n: int, style: str = "std") -> str:
    """交错：原文一行紧跟同时间戳译文一行。"""
    out = []
    for i in range(n):
        t = ts(10 + i * 10, style=style)
        out += [f"{t}orig {i}", f"{t}trans {i}"]
    return "\n".join(out) + "\n"


def grouped(n: int) -> str:
    """分段：原文整段在前、译文整段在后（时间戳仍然相同）。"""
    orig = [f"{ts(10 + i * 10)}orig {i}" for i in range(n)]
    trans = [f"{ts(10 + i * 10)}trans {i}" for i in range(n)]
    return "\n".join(orig + trans) + "\n"


def leading_mono(count: int, pairs: int) -> str:
    """前奏 count 行单语，之后 pairs 组成对。"""
    out = [f"{ts(i)}intro {i}" for i in range(1, count + 1)]
    base = count + 5
    for i in range(pairs):
        t = ts(base + i * 10)
        out += [f"{t}orig {i}", f"{t}trans {i}"]
    return "\n".join(out) + "\n"


def tail_mono(pairs: int, tail: int) -> str:
    """pairs 组成对，之后 tail 行单语。"""
    out = []
    for i in range(pairs):
        t = ts(10 + i * 10)
        out += [f"{t}orig {i}", f"{t}trans {i}"]
    base = 10 + pairs * 10 + 10
    out += [f"{ts(base + i * 10)}tail {i}" for i in range(tail)]
    return "\n".join(out) + "\n"


def karaoke(pairs: int = 6) -> str:
    """带逐字卡拉OK标签 —— 与双行互斥。"""
    out = []
    for i in range(pairs):
        t = ts(10 + i * 10)
        out += [f"{t}<00:10.00>orig {i}", f"{t}trans {i}"]
    return "\n".join(out) + "\n"


def make_tagged_wav(path: Path, *, title=None, artist=None, album=None,
                    language=None, instrumental=False) -> Path:
    """生成一个**真的能被 mutagen 读出标签**的 WAV。

    占位字节文件走不到 `read_tags` 的成功路径（mutagen 解析失败 → 一路退到文件名兜底），
    而这条路径正是容器差异 bug 的藏身处：ID3 的键是 `TIT2`，不是 `title`。
    """
    import struct
    import wave

    from mutagen.id3 import TALB, TIT2, TLAN, TPE1, TXXX
    from mutagen.wave import WAVE

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<8000h", *([0] * 8000)))     # 1 秒静音

    f = WAVE(str(path))
    f.add_tags()
    if title:
        f.tags.add(TIT2(encoding=3, text=title))
    if artist:
        f.tags.add(TPE1(encoding=3, text=artist))
    if album:
        f.tags.add(TALB(encoding=3, text=album))
    if language:
        f.tags.add(TLAN(encoding=3, text=language))
    if instrumental:
        f.tags.add(TXXX(encoding=3, desc="instrumental", text="1"))
    f.save()
    return path
