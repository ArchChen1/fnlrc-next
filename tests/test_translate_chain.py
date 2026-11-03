"""翻译降级链（M6）的回归用例。

这条链的语义是「**按用户排的顺序逐家问，补不上的行才往下传**」。
和旧版单选后端的区别在于：用户可以决定先问谁，而"源站译词"是一个终点，
不是一次调用 —— 它在任何位置都表示"到此为止，用源站已有的译文"。
"""
from __future__ import annotations

import pytest

from fnlrc import pipeline as pl


@pytest.fixture
def stub_remote(monkeypatch):
    """把远端翻译换成可控的假实现，并记录被调用了几次、要了哪些行。"""
    calls = {"n": 0, "lines": []}

    def fake(lines, *a, **k):
        calls["n"] += 1
        calls["lines"].append(list(lines))
        return ({i + 1: f"AI{i + 1}" for i in range(len(lines))}, None)

    monkeypatch.setattr(pl.P, "remote_translate", fake)
    return calls


def _cfg(order, **kw):
    kw.setdefault("remote_base_url", "https://api.deepseek.com/v1")
    kw.setdefault("remote_model", "deepseek-chat")
    kw.setdefault("remote_api_key", "sk-test")
    return pl.Config(translate_order=order, **kw)


# ---------------------------------------------------------------- 链的基本语义
def test_源站排在第一位时不再调远端(stub_remote):
    """用户把「源站译词」排第一 = 只用源站已有的译文，不该联网。"""
    out, backend = pl._translate_chain(["a", "b"], _cfg(["source", "remote"]))
    assert backend == "source"
    assert stub_remote["n"] == 0


def test_远端排在第一位就先走远端(stub_remote):
    """把「我的 API」拖到最上面，等于不管源站给没给都先问 AI。"""
    out, backend = pl._translate_chain(["a", "b"], _cfg(["remote", "source"]))
    assert backend == "remote"
    assert out == ["AI1", "AI2"]
    assert stub_remote["n"] == 1


def test_链里只有远端也能工作(stub_remote):
    out, backend = pl._translate_chain(["a"], _cfg(["remote"]))
    assert out == ["AI1"] and backend == "remote"


def test_远端没配参数时不会发请求并继续往下(stub_remote):
    """缺 base/model 不该当场报错，而是跳过这一环。"""
    out, backend = pl._translate_chain(
        ["a"], pl.Config(translate_order=["remote", "source"],
                         remote_base_url="", remote_model=""))
    assert stub_remote["n"] == 0
    assert backend == "source"


def test_远端返回部分结果时只补缺的那些(stub_remote, monkeypatch):
    """第一环补了一半，剩下的才进下一环 —— 不整首重翻。"""
    monkeypatch.setattr(pl.P, "remote_translate",
                        lambda lines, *a, **k: ({1: "只有第一行"}, None))
    out, _ = pl._translate_chain(["a", "b"], _cfg(["remote"]))
    assert out[0] == "只有第一行"
    assert out[1] == "", "没补上的行留空，由调用方当单语处理"


def test_空列表直接返回(stub_remote):
    out, backend = pl._translate_chain([], _cfg(["remote"]))
    assert out == [] and backend == "none"
    assert stub_remote["n"] == 0


def test_未知后端名被忽略(stub_remote):
    """配置里混进旧值（比如已删除的 none）不该炸，也不该挡住后面的项。"""
    out, backend = pl._translate_chain(["a"], _cfg(["bogus", "remote"]))
    assert out == ["AI1"] and backend == "remote"


# ---------------------------------------------------------------- 与旧路径的兼容
def test_没有排序配置时回落到旧版单后端(monkeypatch):
    """老配置 / 老前端不带 translate_order，行为必须一字不差。"""
    called = {"n": 0}

    def fake(*a, **k):
        called["n"] += 1
        return ({1: "x"}, None)

    monkeypatch.setattr(pl.P, "remote_translate", fake)
    out, backend = pl._translate_chain(["a"], pl.Config(translate="remote",
                                                       remote_base_url="u",
                                                       remote_model="m"))
    assert called["n"] == 1 and backend == "remote"


def test_source单后端仍然不出网(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(pl.P, "remote_translate",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ({}, None))
    out, backend = pl._translate_chain(["a"], pl.Config(translate="source"))
    assert out is None and backend == "source" and called["n"] == 0
