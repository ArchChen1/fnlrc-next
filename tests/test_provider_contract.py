"""Provider 契约测试（M2）。

对**每一个已注册的源**跑同一组检查：无论内置的还是社区插件的，只要注册进来
就必须满足这些约定。这样新增一个源时，它要么通过契约、要么在 CI 里红出来。

设计要点：
- 用假的 `fetch` 结果喂给契约校验器，不联网（真实源的网络行为由 e2e 测试覆盖）。
- 单独验证「注册表优先级排序」「黑/白名单筛选」「坏插件隔离」这些编排层行为。
"""
from __future__ import annotations

import pytest

from fnlrc import pipeline as pl
from fnlrc.providers import base, loader, registry
from fnlrc.providers.builtin import register_builtins


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """每个用例前后都重置注册表，避免相互污染。

    同时把内置源的取词函数打桩成 None —— `fetch_lyrics` 会自动注册内置源，
    不打桩的话这些编排用例会真的联网（幂等注册是刻意设计，见 fetch_lyrics）。
    """
    monkeypatch.setattr("fnlrc.providers.netease_fetch", lambda *a, **k: None)
    monkeypatch.setattr("fnlrc.providers.lrclib_fetch", lambda *a, **k: None)
    registry.reset()
    yield
    registry.reset()


# ---------------------------------------------------------------- 契约校验器
def _contract_problems(res: base.LyricResult) -> list[str]:
    bad: list[str] = []
    if not res.matched:
        bad.append("matched 为空")
    if not isinstance(res.score, (int, float)):
        bad.append(f"score 不是数字：{res.score!r}")
    last = -1
    for row in res.pairs:
        if not (isinstance(row, tuple) and len(row) == 4):
            bad.append(f"pair 不是四元组：{row!r}")
            break
        ms, orig, trans, roman = row
        if not isinstance(ms, int):
            bad.append(f"毫秒不是整数：{ms!r}")
            break
        if ms <= last:
            bad.append(f"毫秒未严格递增：{last} → {ms}")
            break
        last = ms
        if not orig or not isinstance(orig, str):
            bad.append(f"原文不是非空字符串：{orig!r}")
            break
        if trans is not None and not isinstance(trans, str):
            bad.append(f"译文既不是 None 也不是 str：{trans!r}")
            break
        if trans == "":
            bad.append("译文是空串（约定用 None 表示没有译文）")
            break
    return bad


def _good_result() -> base.LyricResult:
    return base.LyricResult(
        source="x", matched="艺人 - 歌名", score=8.0, lyric_lang="ja",
        pairs=[(1000, "ああ", "译文", "aa"), (2000, "いい", None, None)])


def test_契约校验器_接受合规结果():
    assert _contract_problems(_good_result()) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: setattr(r, "matched", ""), "matched"),
    (lambda r: setattr(r, "score", "high"), "score"),
    (lambda r: setattr(r, "pairs", [(1000, "a", "t", None), (1000, "b", None, None)]), "递增"),
    (lambda r: setattr(r, "pairs", [(1000, "a", None, None), (500, "b", None, None)]), "递增"),
    (lambda r: setattr(r, "pairs", [(1000, "", "t", None)]), "原文"),
    (lambda r: setattr(r, "pairs", [(1000, "a", 5, None)]), "译文"),
    (lambda r: setattr(r, "pairs", [(1000, "a", "", None)]), "空串"),
    (lambda r: setattr(r, "pairs", [("1000", "a", None, None)]), "整数"),
])
def test_契约校验器_能抓出各类违规(mutate, needle):
    r = _good_result()
    mutate(r)
    assert any(needle in m for m in _contract_problems(r))


# ---------------------------------------------------------------- 内置源合规
def test_内置源全部注册且优先级有序():
    register_builtins()
    provs = registry.all_providers()
    assert [p.name for p in provs] == ["netease", "lrclib"]
    assert provs[0].priority < provs[1].priority
    assert all(isinstance(p, base.Provider) for p in provs)   # runtime_checkable 协议


def test_内置源满足协议形状():
    register_builtins()
    for p in registry.all_providers():
        assert isinstance(p.name, str) and p.name
        assert isinstance(p.label, str) and p.label
        assert isinstance(p.priority, int)
        assert isinstance(p.needs_key, bool)
        assert p.available() == (True, "")


def test_内置适配器_网易云结果归一成pairs(monkeypatch):
    from fnlrc.providers.builtin import netease as ne

    monkeypatch.setattr(
        "fnlrc.providers.netease_fetch",
        lambda *a, **k: {"source": "netease", "matched": "m / m", "score": 8.0,
                         "lyric_lang": "ja", "song_id": 7,
                         "pairs": [(1, "a", "t", None)], "candidates": [], "alternatives": {}})
    res = ne.NeteaseProvider().fetch(_song(), pl.Config(), deadline=None)
    assert res is not None and res.source == "netease"
    assert _contract_problems(res) == []


def test_内置适配器_lrclib只给原词译文为None(monkeypatch):
    from fnlrc.providers.builtin import lrclib as lr

    monkeypatch.setattr(
        "fnlrc.providers.lrclib_fetch",
        lambda *a, **k: {"source": "lrclib", "matched": "a - t", "score": 6.0,
                         "lyric_lang": "ja",
                         "entries": [(1000, "原词一"), (2000, "原词二")],
                         "candidates": []})
    res = lr.LrclibProvider().fetch(_song(), pl.Config(), deadline=None)
    assert res is not None
    assert all(p[2] is None for p in res.pairs)     # 译文恒为 None
    assert _contract_problems(res) == []


def test_内置适配器_无结果返回None(monkeypatch):
    from fnlrc.providers.builtin import lrclib as lr

    monkeypatch.setattr("fnlrc.providers.lrclib_fetch", lambda *a, **k: None)
    assert lr.LrclibProvider().fetch(_song(), pl.Config(), deadline=None) is None


def _song():
    return pl.lib.Song(path="x.flac", lrc_path="x.lrc", rel="x.flac",
                       title="t", artist="a", duration=200.0, expected_lang="ja")


# ---------------------------------------------------------------- 注册表行为
class _FakeProvider:
    def __init__(self, name, priority=50, ok=True, pairs=None, boom=False):
        self.name = name
        self.label = name.upper()
        self.priority = priority
        self.needs_key = False
        self._ok = ok
        self._pairs = pairs
        self._boom = boom
        self.calls = 0

    def available(self):
        return self._ok, "" if self._ok else "未配置"

    def fetch(self, song, cfg, *, deadline=None):
        self.calls += 1
        if self._boom:
            raise RuntimeError("boom")
        if self._pairs is None:
            return None
        return base.LyricResult(source=self.name, matched="m", score=1.0, pairs=self._pairs)


def test_同名不覆盖除非replace():
    a = _FakeProvider("dup", priority=1)
    b = _FakeProvider("dup", priority=2)
    registry.register(a)
    registry.register(b)
    assert registry.get("dup") is a
    registry.register(b, replace=True)
    assert registry.get("dup") is b


def test_按优先级排序():
    registry.register(_FakeProvider("c", priority=30))
    registry.register(_FakeProvider("a", priority=10))
    registry.register(_FakeProvider("b", priority=20))
    assert [p.name for p in registry.all_providers()] == ["a", "b", "c"]


def test_register拒绝空name():
    with pytest.raises(ValueError):
        registry.register(_FakeProvider(""))


# ---------------------------------------------------------------- 编排行为
def test_fetch_lyrics_按序降级到第一个有结果的():
    first = _FakeProvider("first", priority=10, pairs=None)
    second = _FakeProvider("second", priority=20, pairs=[(1, "a", None, None)])
    registry.register(first)
    registry.register(second)
    res = pl.P.fetch_lyrics(_song(), pl.Config())
    assert res is not None and res.source == "second"
    assert first.calls == 1 and second.calls == 1


def test_fetch_lyrics_坏源被隔离后续源仍可用():
    bad = _FakeProvider("bad", priority=10, boom=True)
    good = _FakeProvider("good", priority=20, pairs=[(1, "a", None, None)])
    registry.register(bad)
    registry.register(good)
    res = pl.P.fetch_lyrics(_song(), pl.Config())
    assert res is not None and res.source == "good"


def test_fetch_lyrics_不可用源被跳过不调用fetch():
    off = _FakeProvider("off", priority=10, ok=False, pairs=[(1, "a", None, None)])
    registry.register(off)
    assert pl.P.fetch_lyrics(_song(), pl.Config()) is None
    assert off.calls == 0


def test_fetch_lyrics_黑名单优先于白名单():
    a = _FakeProvider("a", priority=10, pairs=[(1, "a", None, None)])
    b = _FakeProvider("b", priority=20, pairs=[(1, "b", None, None)])
    registry.register(a)
    registry.register(b)
    res = pl.P.fetch_lyrics(_song(), pl.Config(
        providers_enabled=["a", "b"], providers_disabled=["a"]))
    assert res is not None and res.source == "b"


def test_fetch_lyrics_白名单为空表示不限制():
    a = _FakeProvider("a", priority=10, pairs=[(1, "a", None, None)])
    registry.register(a)
    assert pl.P.fetch_lyrics(_song(), pl.Config(providers_enabled=[])) is not None


def test_fetch_lyrics_所有源都炸时记录errors():
    registry.register(_FakeProvider("bad", priority=10, boom=True))
    errs: list = []
    res = pl.P.fetch_lyrics(_song(), pl.Config(), errors=errs)
    assert res is None and errs and errs[0][0] == "bad"


# ---------------------------------------------------------------- 插件隔离
def test_loader_坏插件不影响好插件(tmp_path):
    (tmp_path / "bad.py").write_text("raise ValueError('插件自己崩了')\n", encoding="utf-8")
    (tmp_path / "good.py").write_text(
        "from fnlrc.providers import registry, base\n"
        "class P:\n"
        "    name='good'; label='Good'; priority=50; needs_key=False\n"
        "    def available(self): return True, ''\n"
        "    def fetch(self, song, cfg, *, deadline=None): return None\n"
        "registry.register(P())\n", encoding="utf-8")

    logs: list[str] = []
    loaded = loader.load_plugins(tmp_path, log=logs.append)
    assert registry.get("good") is not None          # 好插件被加载
    assert registry.get("bad") is None               # 坏插件被隔离
    assert any("bad" in m for m in logs)             # 有空日志记录
    assert len(loaded) == 1


def test_loader_忽略下划线开头的文件(tmp_path):
    (tmp_path / "_private.py").write_text("print('should not load')\n", encoding="utf-8")
    assert loader.load_plugins(tmp_path) == []
