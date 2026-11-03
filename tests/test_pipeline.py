"""状态机：stub 掉 provider，断言每个分支产出的 status 与**副作用**。

「不写文件」这类断言比「写了什么」更重要 —— 那是这个项目的安全底线
（`docs/TESTING.md` 第五章）。
"""
from __future__ import annotations

import pytest

from fnlrc import library as lib
from fnlrc import lrc as L
from fnlrc import pipeline as pl


# ---------------------------------------------------------------- 夹具
def make_song(tmp_path, rel="日语/test.flac", **kw) -> lib.Song:
    kw.setdefault("title", "Sample Song")
    kw.setdefault("artist", "Original Artist")
    kw.setdefault("duration", 200.0)
    kw.setdefault("expected_lang", "ja")
    kw.setdefault("expected_lang_source", "tag")
    return lib.Song(path=str(tmp_path / "test.flac"),
                    lrc_path=str(tmp_path / "test.lrc"), rel=rel, **kw)


def ne_pairs(n=8, with_trans=True, frac=1.0):
    """构造 (ms, 原文, 译文, 罗马音)。原文用假名，确保被判成日语而不是中文。"""
    out = []
    for i in range(n):
        trans = f"中文译文{i}" if (with_trans and i < int(round(n * frac))) else None
        out.append(((i + 1) * 10000, f"ああ サンプル{i}", trans, f"aa sanpuru{i}"))
    return out


def stub_netease(pairs):
    def f(title, artist, duration, expected=None, deadline=None, debug=False):
        if pairs is None:
            return None
        return {"source": "netease", "song_id": 1, "matched": "stub / stub",
                "score": 8.0, "lyric_lang": "ja", "lang_mismatch": False,
                "pairs": pairs, "candidates": [], "alternatives": {},
                "has_translation": any(p[2] for p in pairs), "raw_lrc": ""}
    return f


@pytest.fixture
def stub_providers(monkeypatch):
    """默认：两个源都查不到。具体用例再覆盖。"""
    monkeypatch.setattr(pl.P, "netease_fetch", lambda *a, **k: None)
    monkeypatch.setattr(pl.P, "lrclib_fetch", lambda *a, **k: None)
    return monkeypatch


def cfg(tmp_path, **kw):
    kw.setdefault("write", False)
    kw.setdefault("dump_dir", str(tmp_path))
    return pl.Config(**kw)


def dumped(tmp_path, song) -> str:
    """读回预览产物。"""
    p = (tmp_path / song.rel).with_suffix(".lrc")
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 状态分支
def test_网易云原词加译词_写双语(stub_providers, tmp_path):
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, True, 1.0)))
    res = pl.process_song(make_song(tmp_path), cfg(tmp_path))
    assert res["status"] == "dumped"
    assert res["pairs"] == 8
    assert res["validation"]["ok"] is True
    assert res["backend"] == "source"


def test_源站没译词且后端可用_写双语(stub_providers, tmp_path):
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, False)))
    stub_providers.setattr(pl.P, "remote_translate",
                           lambda lines, *a, **k: ({i: "译文" for i in range(1, len(lines) + 1)}, None))
    res = pl.process_song(make_song(tmp_path), cfg(
        tmp_path, translate="remote", remote_base_url="https://api.deepseek.com/v1",
        remote_model="deepseek-chat", remote_api_key="sk-x"))
    assert res["status"] == "dumped"
    assert res["backend"] == "remote"
    assert res["validation"]["ok"] is True


def test_源站没译词且不翻译_写单语(stub_providers, tmp_path):
    """关键回归：无译文时必须是 written_mono，**不能**变成 validate_failed。

    validate() 对单语恒为 False（paired_groups 为 0），所以"写单语"这条分支
    必须绕过校验。任何"构造两种产物都跑 validate 取通过的那个"的写法都会让
    这首歌一个文件都写不出来。
    """
    song = make_song(tmp_path)
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, False)))
    res = pl.process_song(song, cfg(tmp_path, translate="source"))
    assert res["status"] == "written_mono"
    body = dumped(tmp_path, song)
    assert L.validate(body)["ok"] is False      # 单语就是不过校验，这是事实
    assert res["elapsed_s"] >= 0


def test_译词只覆盖一半_退回单语而不是什么都不写(stub_providers, tmp_path):
    song = make_song(tmp_path)
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(10, True, 0.5)))
    res = pl.process_song(song, cfg(tmp_path, translate="source"))
    assert res["status"] == "written_mono"
    assert res["pairs"] == 5                     # 双语候选的成对组数
    assert (tmp_path / song.rel).with_suffix(".lrc").is_file()


def test_译词覆盖九成且通过校验_写双语(stub_providers, tmp_path):
    """以前这里会整首退回单语、把已经拿到的译词全丢掉。"""
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(10, True, 0.9)))
    res = pl.process_song(make_song(tmp_path), cfg(tmp_path, translate="source"))
    assert res["status"] == "dumped"
    assert res["pairs"] == 9


def test_两个源都空_不写文件(stub_providers, tmp_path):
    song = make_song(tmp_path)
    res = pl.process_song(song, cfg(tmp_path))
    assert res["status"] == "no_lyrics"
    assert not (tmp_path / song.rel).with_suffix(".lrc").exists()


def test_文件名像纯音乐但没标记_归到no_lyrics(stub_providers, tmp_path):
    """报成 skip_instrumental 会让它进不了任何重试队列，匹配失败就永远查不出来。"""
    song = make_song(tmp_path, title="某某 伴奏版", rel="日语/某某 伴奏版.flac")
    res = pl.process_song(song, cfg(tmp_path))
    assert res["status"] == "no_lyrics"
    assert "纯音乐" in res["note"]


def test_外文曲目抓到中文歌词_拦下不写(stub_providers, tmp_path):
    song = make_song(tmp_path, expected_lang="ru")
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(
        [(10000, "我在抽屉里找到你的信", None, None),
         (20000, "还有一张旧照片", None, None)]))
    res = pl.process_song(song, cfg(tmp_path))
    assert res["status"] == "lang_mismatch"
    assert not (tmp_path / song.rel).with_suffix(".lrc").exists()


def test_纯音乐_zxx_一次网络都不发(stub_providers, tmp_path):
    calls = {"n": 0}

    def counted(*a, **k):
        calls["n"] += 1
        return None
    stub_providers.setattr(pl.P, "netease_fetch", counted)
    stub_providers.setattr(pl.P, "lrclib_fetch", counted)

    song = make_song(tmp_path, expected_lang="instrumental")
    res = pl.process_song(song, cfg(tmp_path))
    assert res["status"] == "skip_instrumental"
    assert calls["n"] == 0


def test_源站标记纯音乐_不写文件(stub_providers, tmp_path):
    stub_providers.setattr(pl.P, "lrclib_fetch", lambda *a, **k: {
        "source": "lrclib", "entries": [(1000, "x")], "instrumental": True,
        "matched": "m", "score": 9.0, "lyric_lang": "unknown", "candidates": []})
    song = make_song(tmp_path)
    res = pl.process_song(song, cfg(tmp_path))
    assert res["status"] == "skip_instrumental"


def test_写入模式产生bak且不覆盖原文件(stub_providers, tmp_path):
    song = make_song(tmp_path)
    (tmp_path / song.rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "test.lrc").write_text("[00:01.00]旧内容\n", encoding="utf-8")
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, True, 1.0)))

    res = pl.process_song(song, cfg(tmp_path, write=True))
    assert res["status"] == "written"
    assert (tmp_path / "test.lrc.bak").read_text(encoding="utf-8") == "[00:01.00]旧内容\n"


def test_目标目录不可写_报write_failed(stub_providers, tmp_path, monkeypatch):
    song = make_song(tmp_path)
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, True, 1.0)))

    real_open = open

    def deny(path, *a, **k):
        if str(path).endswith(".lrc") and "w" in (a[0] if a else k.get("mode", "")):
            raise PermissionError("simulated read-only mount")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", deny)
    res = pl.process_song(song, cfg(tmp_path, write=True, backup=False))
    assert res["status"] == "write_failed"
    assert "PermissionError" in res["note"]


def test_provider抛异常被隔离成error(stub_providers, tmp_path):
    def boom(*a, **k):
        raise ValueError("boom")
    stub_providers.setattr(pl.P, "netease_fetch", boom)
    songs = [make_song(tmp_path)]
    out = pl.run_batch(songs, cfg(tmp_path))
    assert out["results"][0]["status"] == "error"


# ---------------------------------------------------------------- 跳过已合规
def test_已有合规双语_直接跳过(stub_providers, tmp_path):
    song = make_song(tmp_path)
    (tmp_path / song.rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "test.lrc").write_text(
        L.build_bilingual_roles("t", "a", "", ne_pairs(8, True, 1.0)), encoding="utf-8")
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, True, 1.0)))

    out = pl.run_batch([song], cfg(tmp_path, write=True))
    assert out["results"][0]["status"] == "skipped_existing"
    assert out["stats"]["skipped"] == 1


def test_force会重做已有双语(stub_providers, tmp_path):
    song = make_song(tmp_path)
    (tmp_path / song.rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "test.lrc").write_text(
        L.build_bilingual_roles("t", "a", "", ne_pairs(8, True, 1.0)), encoding="utf-8")
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, True, 1.0)))

    out = pl.run_batch([song], cfg(tmp_path, write=True, force=True))
    assert out["results"][0]["status"] == "written"
    assert (tmp_path / "test.lrc.bak").is_file()


def test_中文单语文件跳过(stub_providers, tmp_path):
    song = make_song(tmp_path, expected_lang="zh")
    (tmp_path / song.rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "test.lrc").write_text(
        "".join(f"[00:{i:02d}.00]我在抽屉里找到你的信{i}\n" for i in range(6)),
        encoding="utf-8")
    out = pl.run_batch([song], cfg(tmp_path, write=True))
    assert out["results"][0]["status"] == "skipped_existing"


# ---------------------------------------------------------------- 批量行为
def test_优雅停止(stub_providers, tmp_path):
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, True, 1.0)))
    songs = [make_song(tmp_path, rel=f"日语/s{i}.flac") for i in range(10)]
    seen = {"n": 0}

    def on_event(ev):
        if ev.get("type") == "song":
            seen["n"] += 1
    out = pl.run_batch(songs, cfg(tmp_path), on_event=on_event,
                       should_stop=lambda: seen["n"] >= 2)
    assert out["stats"]["done"] == 2


def test_结果字典带匹配回显(stub_providers, tmp_path):
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, True, 1.0)))
    res = pl.process_song(make_song(tmp_path), cfg(tmp_path))
    assert res["matched"] and res["score"] is not None
    assert res["expected_lang_source"] == "tag"


def test_结果里不出现api_key(stub_providers, tmp_path):
    """报告会被导出 CSV、贴进 issue，绝不能带出凭据。"""
    res = pl.process_song(make_song(tmp_path), cfg(
        tmp_path, translate="remote", remote_api_key="sk-SECRET-DO-NOT-LEAK",
        remote_base_url="https://api.deepseek.com/v1", remote_model="deepseek-chat"))
    assert "sk-SECRET-DO-NOT-LEAK" not in str(res)


# ---------------------------------------------------------------- 翻译后端
def test_source不调任何后端(stub_providers):
    """默认后端是"只用源站译词"——不该为了翻译去联网。"""
    called = {"n": 0}
    stub_providers.setattr(pl.P, "remote_translate",
                           lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ({}, None))
    trans, backend = pl._translate_missing(["a"], pl.Config(translate="source"))
    assert trans is None and backend == "source" and called["n"] == 0


def test_none也不调后端(stub_providers):
    trans, backend = pl._translate_missing(["a"], pl.Config(translate="none"))
    assert trans is None and backend == "source"


def test_remote没配参数时不发请求(stub_providers):
    called = {"n": 0}
    stub_providers.setattr(pl.P, "remote_translate",
                           lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ({}, None))
    trans, backend = pl._translate_missing(["a"], pl.Config(translate="remote"))
    assert trans is None and backend == "none" and called["n"] == 0


def test_remote返回部分结果也逐行保留(stub_providers):
    """翻了一半的行不该被丢回单语。"""
    stub_providers.setattr(pl.P, "remote_translate",
                           lambda lines, *a, **k: ({1: "译文"}, None))
    cfg_ = pl.Config(translate="remote", remote_base_url="https://api.deepseek.com/v1",
                     remote_model="deepseek-chat", remote_api_key="sk-x")
    trans, backend = pl._translate_missing(["a", "b"], cfg_)
    assert trans == ["译文", ""] and backend == "remote"


def test_remote不可用时返回None并记下原因(stub_providers):
    logs = []
    stub_providers.setattr(pl.P, "remote_translate", lambda *a, **k: ({}, "API key 无效或已过期"))
    cfg_ = pl.Config(translate="remote", remote_base_url="https://api.deepseek.com/v1",
                     remote_model="deepseek-chat", remote_api_key="sk-bad")
    trans, backend = pl._translate_missing(["a"], cfg_, log=logs.append)
    assert trans is None and backend == "remote"
    assert any("API key" in m for m in logs)


def test_远端翻译只拿到缺译文的行(stub_providers, tmp_path):
    """源站已经给了译词的行不该再送去翻 —— 人工译词质量比机器翻译好。"""
    sent = {}

    def fake(lines, *a, **k):
        sent["lines"] = list(lines)
        return ({i: "机器译文" for i in range(1, len(lines) + 1)}, None)

    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(8, True, 0.5)))
    stub_providers.setattr(pl.P, "remote_translate", fake)
    pl.process_song(make_song(tmp_path), cfg(
        tmp_path, translate="remote", remote_base_url="https://api.deepseek.com/v1",
        remote_model="deepseek-chat", remote_api_key="sk-x"))
    assert len(sent["lines"]) == 4           # 只有后 4 行缺译文

# ---------------------------------------------------------------- 保留本地时间轴
def local_lrc(path, rows):
    """写一个 .lrc。rows: [(毫秒, 文本)]。返回正文。"""
    body = "".join(f"[{ms // 60000:02d}:{ms % 60000 / 1000:05.2f}]{t}\n" for ms, t in rows)
    path.write_text(body, encoding="utf-8")
    return body


def local_mono(path, stamps_s, text="サンプル ローカル"):
    """外文单语 .lrc，时间戳按给定秒数生成。"""
    return local_lrc(path, [(int(s * 1000), f"{text}{i}") for i, s in enumerate(stamps_s)])


def test_外文单语歌词保留本地时间轴(stub_providers, tmp_path):
    """核心行为：文件里的时间轴原样保留，译文按**同一时间戳**插进去。

    本地时间戳刻意与源站错开 300ms（在就近匹配窗口内）—— 输出里出现的是本地那组，
    就证明走的不是"整份重抓"。
    """
    song = make_song(tmp_path)
    # 源站是 10.00 / 20.00 / …；本地是 10.30 / 20.30 / …（错开 300ms）
    local_lrc(tmp_path / "test.lrc",
              [((i + 1) * 10000 + 300, f"ああ サンプル{i}") for i in range(6)])
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(6, True, 1.0)))

    res = pl.process_song(song, cfg(tmp_path))

    assert res["status"] == "written_keep_local"
    assert res["keep_local"] is True
    assert res["validation"]["ok"] is True
    out = dumped(tmp_path, song)
    assert "[00:10.30]" in out and "[01:00.30]" in out          # 本地时间轴
    assert "[00:10.00]" not in out and "[01:00.00]" not in out  # 不是源站的
    assert "中文译文0" in out                                    # 译文插进来了
    assert "keep-local" in out


def test_保留本地时间轴时按文本兜底配对(stub_providers, tmp_path):
    """时间戳完全对不上时，靠原文文本相似度配对（两边的时间轴来源不同）。"""
    song = make_song(tmp_path)
    # 本地时间戳与源站差 4 秒（远超 900ms 窗口），但文本一字不差
    local_lrc(tmp_path / "test.lrc",
              [(5000 + i * 1000, f"ああ サンプル{i}") for i in range(5)])
    pairs = [(1000 + i * 1000, f"ああ サンプル{i}", f"中文译文{i}", None) for i in range(5)]
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(pairs))

    res = pl.process_song(song, cfg(tmp_path))

    assert res["status"] == "written_keep_local"
    out = dumped(tmp_path, song)
    assert "[00:05.00]ああ サンプル0" in out and "中文译文0" in out
    assert "[00:09.00]ああ サンプル4" in out and "中文译文4" in out
    assert "[00:01.00]" not in out          # 源站的时间轴没被用上


def test_关掉保留后按源站整份重写(stub_providers, tmp_path):
    """`--rebuild-local` / 取消勾选时的老行为：时间轴用源站的。"""
    song = make_song(tmp_path)
    local_lrc(tmp_path / "test.lrc",
              [((i + 1) * 10000 + 300, f"ああ サンプル{i}") for i in range(6)])
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(6, True, 1.0)))

    res = pl.process_song(song, cfg(tmp_path, keep_local=False))

    assert res["status"] == "dumped"
    assert res["keep_local"] is False
    out = dumped(tmp_path, song)
    assert "[00:10.00]" in out and "[00:10.30]" not in out


def test_本地推不出译文时退回重抓并且说明原因(stub_providers, tmp_path):
    """源站没译文（LRCLIB 那种）→ 保留无从谈起，但**必须留下原因**。

    「我以为保住了时间轴、其实被换掉了」是这个功能最容易伤到用户的地方，
    所以原因不能被后面重抓分支的 note 冲掉。
    """
    song = make_song(tmp_path)
    local_mono(tmp_path / "test.lrc", [5, 15, 25, 35, 45, 55])
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(6, False, 0.0)))

    res = pl.process_song(song, cfg(tmp_path))

    assert res["status"] == "written_mono"
    assert "本地时间轴" in res["note"]


def test_已是双语文件时不做保留(stub_providers, tmp_path):
    """本地已经是双行（同一时间戳两行）→ 不能拿它当基底，否则越补越乱。

    `--force` 重做时必须退回重抓。
    """
    song = make_song(tmp_path)
    tmp_path.joinpath("test.lrc").write_text(
        "[ti:x]\n" + "".join(f"[00:0{i}.00]ああ サンプル{i}\n[00:0{i}.00]旧译文{i}\n"
                            for i in range(1, 7)),
        encoding="utf-8")
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(6, True, 1.0)))

    res = pl.process_song(song, cfg(tmp_path, force=True))

    assert res["status"] == "dumped"
    assert res["keep_local"] is False
    assert "旧译文" not in dumped(tmp_path, song)


def test_中文单语歌词不做保留(stub_providers, tmp_path):
    """中文单语本来就无需双语 —— 不该被插进译文行。"""
    song = make_song(tmp_path, expected_lang="zh")
    tmp_path.joinpath("test.lrc").write_text(
        "".join(f"[00:0{i}.00]中文歌词第{i}句\n" for i in range(1, 7)), encoding="utf-8")
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(6, True, 1.0)))

    res = pl.process_song(song, cfg(tmp_path))

    assert res["status"] != "written_keep_local"


def test_行数少于四行不做保留(stub_providers, tmp_path):
    """本地文件太短（<4 行），成对规则本来就过不了，别浪费一次对齐。"""
    song = make_song(tmp_path)
    local_mono(tmp_path / "test.lrc", [5, 15])
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(6, True, 1.0)))

    res = pl.process_song(song, cfg(tmp_path))
    assert res["status"] != "written_keep_local"


def test_批量统计把保留本地轴算成已写入(stub_providers, tmp_path):
    """回归：新增状态如果没同步进 run_batch，会被 else 分支算成 failed。"""
    song = make_song(tmp_path)
    local_mono(tmp_path / "test.lrc", [5, 15, 25, 35, 45, 55])
    stub_providers.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs(6, True, 1.0)))

    out = pl.run_batch([song], cfg(tmp_path))

    assert out["stats"]["written"] == 1
    assert out["stats"]["written_keep_local"] == 1
    assert out["stats"]["failed"] == 0


def test_乱码时说明形态而不是只说重新获取(tmp_path):
    """乱码多半是源头就坏了，重刮救不回来 —— 报告里要说清是哪种乱码。

    真机实测到的形态是 GBK 被按 Big5 解，存成了合法 UTF-8，光看文件是看不出来的。
    """
    song = make_song(tmp_path)
    mojibake = "此歌曲为没有填词的纯音乐，请您欣赏".encode("gbk").decode("big5", "ignore")
    tmp_path.joinpath("test.lrc").write_text(f"[00:00.00]{mojibake}\n", encoding="utf-8")

    d = pl.decide(song, cfg(tmp_path))
    assert d["action"] == "rebuild"
    assert "gbk-as-big5" in d["reason"]
    assert "正确原文" in d["reason"]


def test_空文件的重做原因不与乱码混为一谈(tmp_path):
    """空文件和乱码是两回事，原因不该写成一团。"""
    song = make_song(tmp_path)
    tmp_path.joinpath("test.lrc").write_text("", encoding="utf-8")
    d = pl.decide(song, cfg(tmp_path))
    assert d["action"] == "rebuild"
    assert "空文件" in d["reason"] and "乱码" not in d["reason"]
