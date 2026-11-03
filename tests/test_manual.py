"""人工翻译（M3）：导出待译 → 人工填 → 导回。

重点覆盖 `plan/03-manual-translation.md` 的验收标准：
1. 导出 CSV 用 utf-8-sig（Excel 中文正常）；
2. 填一半行 → 导回 → 填过的有译文、未填的保持单语；
3. 导出后改一行 original → 导回被拦下；
4. 导回的时间戳与原始**完全一致**（不漂移）。
"""
from __future__ import annotations

import csv
import io

import pytest

from fnlrc import library as lib
from fnlrc import lrc as L
from fnlrc import manual
from fnlrc import providers as P


# ---------------------------------------------------------------- 夹具
def make_song(tmp_path, rel="日语/test.flac", lrc=None, **kw) -> lib.Song:
    root = tmp_path
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    if lrc is not None:
        (root / rel).with_suffix(".lrc").write_text(lrc, encoding="utf-8")
    kw.setdefault("title", "Sample Song")
    kw.setdefault("artist", "Original Artist")
    kw.setdefault("expected_lang", "ja")
    return lib.load_song(str(p), str(root))


def mono_lrc(n=6, start=10):
    return "".join(f"[00:{start + i:02d}.00]ああ サンプル{i}\n" for i in range(n))


def _exported(song):
    """导出再解析回那份「待译清单」（按行），给导回类测试用。"""
    text = manual.export([song], fmt="csv")
    rows = next(iter(manual.parse(text, fmt="csv").values()))
    return rows


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(P, "netease_fetch", lambda *a, **k: None)
    monkeypatch.setattr(P, "lrclib_fetch", lambda *a, **k: None)


# ---------------------------------------------------------------- 收集
def test_收集外文单语_每行都待译(tmp_path):
    s = make_song(tmp_path, lrc=mono_lrc(6))
    got = manual.collect([s])
    assert len(got) == 1
    assert [ln.original for ln in got[0].lines] == [f"ああ サンプル{i}" for i in range(6)]
    assert all(ln.translation is None for ln in got[0].lines)


def test_中文歌词不收集(tmp_path):
    s = make_song(tmp_path, expected_lang="zh",
                  lrc="".join(f"[00:{10+i:02d}.00]我在抽屉里找到你的信{i}\n" for i in range(6)))
    assert manual.collect([s]) == []


def test_没有歌词文件不收集(tmp_path):
    s = make_song(tmp_path)
    assert manual.collect([s]) == []


def test_已是双语的文件_只收缺译文的行(tmp_path):
    """双语文件里已经有译文的行不该再出现在待译清单。"""
    body = "".join(f"[00:{10+i:02d}.00]ああ サンプル{i}\n[00:{10+i:02d}.00]译文{i}\n"
                   for i in range(6))
    s = make_song(tmp_path, lrc=body)
    assert manual.collect([s]) == []          # 全有译文 → 无待译


def test_单语里混入罗马音_罗马音不算待译(tmp_path):
    """罗马音不是要翻的东西，导出时应当被摘掉。"""
    entries = []
    for i in range(6):
        entries.append(f"[00:{10+i*2:02d}.00]ああ サンプル{i}\n")
        entries.append(f"[00:{10+i*2+1:02d}.00]aa sanpuru{i}\n")   # 交替出现 → 判为罗马音
    s = make_song(tmp_path, lrc="".join(entries))
    got = manual.collect([s])
    assert len(got) == 1
    assert all("sanpuru" not in ln.original for ln in got[0].lines)


# ---------------------------------------------------------------- 导出格式
def test_导出CSV带utf8sig且列齐全(tmp_path):
    s = make_song(tmp_path, lrc=mono_lrc(3))
    text = manual.export([s], fmt="csv", root=str(tmp_path))
    assert len(text) > 0
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 3
    assert list(rows[0].keys()) == manual.CSV_COLUMNS
    assert rows[0]["index"] == "1"
    assert rows[0]["original"] == "ああ サンプル0"
    assert rows[0]["translation"] == ""
    assert rows[0]["time"] == "00:10.00"


def test_导出JSON结构完整(tmp_path):
    import json

    s = make_song(tmp_path, lrc=mono_lrc(2))
    d = json.loads(manual.export([s], fmt="json", root=str(tmp_path)))
    assert d["root"] == str(tmp_path)
    assert len(d["songs"]) == 1
    song = d["songs"][0]
    assert song["file"] == "日语/test.flac"
    assert song["lines"][0] == {"index": 1, "time": "00:10.00",
                                "original": "ああ サンプル0", "translation": "",
                                "context_before": "", "context_after": ""}


def test_上下文开关(tmp_path):
    import json

    s = make_song(tmp_path, lrc=mono_lrc(3))
    d = json.loads(manual.export([s], fmt="json", with_context=True))
    lines = d["songs"][0]["lines"]
    assert lines[0]["context_before"] == ""
    assert lines[0]["context_after"] == "ああ サンプル1"
    assert lines[1]["context_before"] == "ああ サンプル0"


def test_导出文件写出UTF8SIG(tmp_path):
    s = make_song(tmp_path, lrc=mono_lrc(3))
    out = tmp_path / "待译.csv"
    meta = manual.export_files([s], str(out), root=str(tmp_path))
    raw = out.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf"          # BOM → Excel 认得中文
    assert meta["lines"] == 3 and meta["files"] == 1


# ---------------------------------------------------------------- 解析
def test_解析CSV(tmp_path):
    s = make_song(tmp_path, lrc=mono_lrc(2))
    text = manual.export([s], fmt="csv")
    parsed = manual.parse(text, fmt="csv")
    assert "日语/test.flac" in parsed
    assert parsed["日语/test.flac"][0]["index"] == 1


def test_解析CSV缺列报错():
    with pytest.raises(manual.ImportError_):
        manual.parse("a,b\n1,2\n", fmt="csv")


def test_解析CSV_index非法报错(tmp_path):
    bad = "file,index,original,translation\n日语/test.flac,x,foo,\n"
    with pytest.raises(manual.ImportError_):
        manual.parse(bad, fmt="csv")


# ---------------------------------------------------------------- 导回 / 合成
def _fill(rows, mapping):
    return [{"index": r["index"], "original": r["original"],
             "translation": mapping.get(r["index"], "")} for r in rows]


def test_导回填一半_只补填过的行(tmp_path):
    """验收标准 2：填一半行 → 填过的有译文、未填的保持原文单语。"""
    s = make_song(tmp_path, lrc=mono_lrc(6))
    exported = _exported(s)
    rows = _fill(exported, {1: "译文一", 2: "译文二"})
    body, problems, n = manual.merge(s, rows)
    assert problems == [] and n == 2
    assert "译文一" in body and "译文二" in body
    assert body.count("[00:10.00]") == 2       # 原文 + 译文两行
    # 未填的行保持单语
    assert body.count("[00:12.00]") == 1


def test_导回时间戳一个不改(tmp_path):
    """验收标准 4：人工翻译不导致时间轴漂移。"""
    s = make_song(tmp_path, lrc=mono_lrc(6))
    before = [ms for ms, _ in L.parse_lrc(mono_lrc(6))]
    exported = _exported(s)
    body, _, _ = manual.merge(s, _fill(exported, {i: f"译{i}" for i in range(1, 7)}))
    after = [ms for ms, _ in L.parse_lrc(body)]
    assert after[::2] == before                # 原文行的时间戳原样


def test_改过的original被拦下(tmp_path):
    """验收标准 3：导出后手动改一行 original → 导回被拦下。"""
    s = make_song(tmp_path, lrc=mono_lrc(3))
    exported = _exported(s)
    rows = _fill(exported, {1: "译文一"})
    rows[0]["original"] = "被人改过的原文"
    body, problems, n = manual.merge(s, rows)
    assert body == "" and problems and n == 0
    assert any("对不上" in m for m in problems)


def test_index不连续被拦下(tmp_path):
    s = make_song(tmp_path, lrc=mono_lrc(4))
    exported = _exported(s)
    rows = _fill(exported[:1] + exported[2:], {1: "译文一"})   # 跳过 index=2
    body, problems, _ = manual.merge(s, rows)
    assert body == "" and any("不连续" in m for m in problems)


def test_index重复被拦下(tmp_path):
    s = make_song(tmp_path, lrc=mono_lrc(3))
    exported = _exported(s)
    rows = exported + [exported[0]]
    body, problems, _ = manual.merge(s, rows)
    assert body == "" and any("重复" in m for m in problems)


def test_一行都没填被拒(tmp_path):
    s = make_song(tmp_path, lrc=mono_lrc(3))
    exported = _exported(s)
    body, problems, _ = manual.merge(s, _fill(exported, {}))
    assert body == "" and any("没有" in m for m in problems)


def test_同文本重复行按顺序各占一条(tmp_path):
    """副歌重复的同一句原文，填不同译文时要落到不同行（不能都堆到第一条）。"""
    lrc = "".join(f"[00:1{i}.00]同じ歌詞\n" for i in range(4))
    s = make_song(tmp_path, lrc=lrc)
    exported = _exported(s)
    assert len(exported) == 4
    rows = _fill(exported, {1: "甲", 2: "乙", 3: "丙", 4: "丁"})
    body, problems, n = manual.merge(s, rows)
    assert problems == [] and n == 4
    for t in ("甲", "乙", "丙", "丁"):
        assert t in body


# ---------------------------------------------------------------- 记忆库
def test_记忆库默认不写_remember才写(tmp_path, monkeypatch):
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path / "data"))
    from fnlrc import store
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path / "data")
    added = manual.remember([("ああ", "啊啊"), ("Hello", "你好")])
    assert added == 2
    mem = manual.load_memory()
    assert mem[manual.norm_key("ああ")] == "啊啊"


def test_记忆库只记中文译向(tmp_path, monkeypatch):
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path / "data"))
    from fnlrc import store
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path / "data")
    # 原文已是中文 → 不记；译文不是中文 → 不记
    assert manual.remember([("你好", "hello"), ("ありがとう", "谢谢")]) == 1
    mem = manual.load_memory()
    assert manual.norm_key("你好") not in mem
    assert mem[manual.norm_key("ありがとう")] == "谢谢"


def test_recall按顺序取(tmp_path, monkeypatch):
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path / "data"))
    from fnlrc import store
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path / "data")
    manual.remember([("ああ", "啊啊")])
    assert manual.recall(["ああ", "知らない"]) == ["啊啊", None]
