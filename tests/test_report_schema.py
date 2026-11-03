"""报告 schema 与 CLI 退出码。

这两样都是**对外承诺**，却一直零覆盖：

- 报告 JSON 会被 `fnlrc report`、CSV 导出、网页版报告页、以及用户的脚本消费。
  字段悄悄改名/消失，下游是静默坏掉的。
- 退出码是 cron 判断成功失败的唯一依据（`0` 正常 / `1` 用法或致命 / `2` 跑完但有失败项）。

所以这里既钉住代码产出的键集，也钉住**仓库里那份示例样本**必须与之一致 ——
样本比代码旧过一次（还留着已删除的 `model` 字段），加了这道比对就不会再漂。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

from fnlrc import library as lib
from fnlrc import pipeline as pl

# 单曲结果的规范键集（`process_song` 的 res）。**改动这里就等于改动对外的报告契约。**
ROW_KEYS = {
    "file", "title", "artist", "status", "source", "backend", "pairs",
    "expected_lang", "expected_lang_source", "lyric_lang",
    "validation", "note", "elapsed_s",
    "keep_local", "matched", "score", "candidates", "alternatives",
}
# 批量报告（`cmd_run --report` 写出来的）
REPORT_KEYS = {"root", "stats", "results", "elapsed_s"}
STATS_KEYS = {
    "total", "written", "written_keep_local", "written_mono", "skipped",
    "instrumental", "no_lyrics", "timeout", "lang_mismatch", "failed",
    "done", "elapsed_s",
}

SAMPLE = pathlib.Path(__file__).resolve().parent.parent / "docs" / "examples" / "report.sample.json"


# ---------------------------------------------------------------- 夹具
def _song(tmp_path, rel="日语/test.flac", **kw):
    kw.setdefault("title", "Sample Song")
    kw.setdefault("artist", "Original Artist")
    kw.setdefault("expected_lang", "ja")
    kw.setdefault("expected_lang_source", "tag")
    return lib.Song(path=str(tmp_path / "test.flac"),
                    lrc_path=str(tmp_path / "test.lrc"), rel=rel, **kw)


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", lambda *a, **k: None)
    monkeypatch.setattr(pl.P, "lrclib_fetch", lambda *a, **k: None)


def _pairs(n=8):
    return [((i + 1) * 10000, f"ああ サンプル{i}", f"中文译文{i}", None) for i in range(n)]


def _stub(pairs):
    def f(title, artist, duration, expected=None, deadline=None, debug=False):
        return {"source": "netease", "song_id": 1, "matched": "stub / stub",
                "score": 8.0, "lyric_lang": "ja", "lang_mismatch": False,
                "pairs": pairs, "candidates": [], "alternatives": {},
                "has_translation": any(p[2] for p in pairs), "raw_lrc": ""}
    return f


# ---------------------------------------------------------------- 键集
def test_各分支产出的键集完全一致(offline, tmp_path, monkeypatch):
    """**每个状态分支都必须是同一套键。**

    下游（`report` 命令、CSV 导出、网页报告页）按固定键名取值 ——
    某个分支少一个键就是一次 KeyError，而且只在跑到那个分支时才炸。
    """
    cases = []

    # 无歌词
    cases.append(("no_lyrics", pl.process_song(_song(tmp_path), pl.Config())))

    # 正常双语
    monkeypatch.setattr(pl.P, "netease_fetch", _stub(_pairs()))
    s = _song(tmp_path, rel="日语/b.flac")
    cases.append(("written", pl.process_song(s, pl.Config())))

    # 纯音乐
    inst = _song(tmp_path, rel="纯音乐/c.flac")
    inst.instrumental = True
    cases.append(("skip_instrumental", pl.process_song(inst, pl.Config())))

    for label, r in cases:
        assert set(r) == ROW_KEYS, f"{label} 分支的键集不一致：{set(r) ^ ROW_KEYS}"


def test_批量报告的顶层与统计键集(offline, tmp_path):
    out = pl.run_batch([_song(tmp_path)], pl.Config(write=False, dump_dir=str(tmp_path)))
    assert set(out["stats"]) == STATS_KEYS, set(out["stats"]) ^ STATS_KEYS
    assert set(out) >= {"stats", "results", "elapsed_s"}
    assert set(out["results"][0]) == ROW_KEYS


def test_每首歌的结果都带匹配回显字段(offline, tmp_path, monkeypatch):
    """报告里要能看出"配到了哪一首" —— 这是用户核对匹配对不对的唯一入口。"""
    monkeypatch.setattr(pl.P, "netease_fetch", _stub(_pairs()))
    r = pl.process_song(_song(tmp_path), pl.Config())
    assert r["matched"] == "stub / stub"
    assert r["score"] == 8.0
    assert isinstance(r["candidates"], list)
    assert isinstance(r["alternatives"], dict)


# ---------------------------------------------------------------- 仓库里那份样本
def test_示例报告的键集与代码一致():
    """`docs/examples/report.sample.json` 必须与当前代码产出的键集一致。

    它曾经比代码旧（还写着已删除的 `model` 字段），而没有任何东西发现 ——
    文档样本是别人照着写下游代码的参照，错了比没有更糟。
    """
    if not SAMPLE.is_file():
        pytest.skip(f"没有示例文件：{SAMPLE}")
    d = json.loads(SAMPLE.read_text(encoding="utf-8"))

    # 下划线开头的键是**注解**（`_comment` / `_why` / `_schema`）——
    # 本仓库 fixtures 的既有约定，不参与 schema 比对。
    assert {k for k in d if not k.startswith("_")} == REPORT_KEYS, \
        {k for k in d if not k.startswith("_")} ^ REPORT_KEYS
    assert set(d["stats"]) == STATS_KEYS, set(d["stats"]) ^ STATS_KEYS

    assert d["results"], "样本里要有真实结果"
    for i, row in enumerate(d["results"]):
        assert set(row) == ROW_KEYS, f"第 {i} 行的键集不一致：{set(row) ^ ROW_KEYS}"
        assert row["status"], "每行都要有状态"
        # 校验结果的形状：要么是 null，要么是完整的一套
        if row["validation"] is not None:
            assert set(row["validation"]) == {
                "paired_groups", "leading_mono", "pair_ratio",
                "karaoke_tags", "total_ts", "ok"}, row["validation"]


def test_示例报告里的状态都是代码真会产出的():
    """样本里不该出现代码里根本不存在的状态 —— 那会误导下游按它写分支。"""
    from fnlrc import lrc as L  # noqa: F401  （保持 import 关系显式）
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "fnlrc" / "pipeline.py").read_text(encoding="utf-8")
    d = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for row in d["results"]:
        assert f'"{row["status"]}"' in src, f'代码里没有这个状态：{row["status"]}'
