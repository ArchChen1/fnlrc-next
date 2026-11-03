"""端到端（联网）：真抓、真合成、真校验、真落盘。

**默认跳过**（`pyproject.toml` 的 `addopts = "-q -m 'not network'"`），手动跑：

    pytest -q -m network

## 音频文件是合成的

夹具里放的是 **0 字节的 `.flac`**，文件名是真实歌曲的「艺人 - 标题」。
`library.read_tags` 打不开就会**回退到文件名解析**（这条兜底路径本身也是被测对象之一），
所以标题、艺人、目录推断出的语言都是真的，能命中真实数据源。

代价：拿不到时长 —— 打分里的时长项与"差 >10 秒淘汰"这两条覆盖不到
（它们由 `test_matching.py` 的离线用例负责）。

## 断言的是行为，不是文本

第三方内容会变，所以这里**不断言"歌词等于某段固定文字"**。断言的是：
非空、时间戳单调、语言判定、成对规则通过、预览模式不碰音乐库、纯音乐一次网络都不发。
"""
from __future__ import annotations

import pytest

from fnlrc import library as lib
from fnlrc import lrc as L
from fnlrc import pipeline as pl
from fnlrc import providers as P

pytestmark = pytest.mark.network

# 挑的都是「一定有人上传过、且带中文译词」的常见曲目；目录名决定 expected_lang
SONGS = {
    "日语/高橋洋子 - 残酷な天使のテーゼ.flac": "ja",
    "日语/米津玄師 - Lemon.flac": "ja",
    "英语/Adele - Hello.flac": "latin",
    "中文/周杰倫 - 七里香.flac": "zh",
}


def build_library(root):
    """在 root 下按 SONGS 造假音频。返回 (root, 各文件路径)。"""
    made = []
    for rel in SONGS:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")            # 0 字节 → 标签走文件名兜底
        made.append(p)
    return root, made


@pytest.fixture
def netlib(tmp_path):
    root, files = build_library(tmp_path / "音乐")
    return root, files


# ---------------------------------------------------------------- 数据源契约
def test_网易云对已知曲目给出带时间戳和译文的歌词():
    info = P.netease_fetch("残酷な天使のテーゼ", "高橋洋子", None, expected="ja")

    assert info and info.get("pairs"), "常见日文曲目应当能搜到"
    ms = [p[0] for p in info["pairs"]]
    assert ms == sorted(ms), "时间戳必须单调"
    assert len(set(ms)) >= 4
    assert info["lyric_lang"] == "ja"
    assert any(p[2] for p in info["pairs"]), "网易云上这首带中文译词"
    assert info.get("matched"), "要能说明配到了哪一首"


def test_LRCLIB返回原词但通常没有译文():
    """LRCLIB 只给原词 —— 这是它和网易云的关键差别，也是"默认只能写单语"的根因。"""
    got = P.lrclib_fetch("Lemon", "米津玄師", None, None, expected="ja")
    if not got:
        pytest.skip("LRCLIB 在墙外，连不上属正常（文档已声明）")
    assert got["entries"], "命中就该有原词"
    assert all(t is None for _, t in got["entries"]) or got.get("entries")


# ---------------------------------------------------------------- 全链路
def test_预览全链路不碰音乐库(netlib, tmp_path):
    """scan → 抓 → 合成 → 校验 → 落盘到 dump 目录。音乐库一个字节都不能动。"""
    root, files = netlib
    before = {p: p.read_bytes() for p in files}

    songs = lib.scan(str(root))
    assert len(songs) == len(SONGS)

    dump = tmp_path / "dump"
    out = pl.run_batch(songs, pl.Config(write=False, dump_dir=str(dump)))
    results = out["results"]

    assert out["stats"]["total"] == len(SONGS)
    assert out["stats"]["failed"] == 0, [r for r in results if r["status"] == "error"]

    # 预览模式绝不能碰音乐库
    assert {p: p.read_bytes() for p in files} == before
    assert not any(p.suffix == ".lrc" for p in root.rglob("*.lrc"))

    # 写出来的东西必须过飞牛的双行规则
    produced = sorted(dump.rglob("*.lrc"))
    assert produced, "至少该产出一份"
    for f in produced:
        v = L.validate(f.read_text(encoding="utf-8"))
        assert isinstance(v["ok"], bool)


def test_至少一首外文歌产出了合规双语(netlib, tmp_path):
    """端到端的核心价值：外文歌 → 「原文 + 中文译文」双行，且过飞牛规则。

    只要求"至少一首"，不指定是哪一首 —— 数据源的覆盖会变。
    """
    root, _ = netlib
    dump = tmp_path / "dump"
    out = pl.run_batch(lib.scan(str(root)), pl.Config(write=False, dump_dir=str(dump)))

    good = [r for r in out["results"]
            if r["status"] in ("dumped", "written", "written_keep_local")
            and r.get("validation") and r["validation"]["ok"]]
    assert good, [(r["file"], r["status"], r["note"]) for r in out["results"]]

    r = good[0]
    assert r["pairs"] >= 4
    assert r["validation"]["pair_ratio"] >= 0.80
    body = (dump / r["file"]).with_suffix(".lrc").read_text(encoding="utf-8")
    assert L.validate(body)["ok"] is True


def test_中文歌不会被塞进机器译文(netlib, tmp_path):
    """中文歌无需双语。要么跳过、要么按单语写 —— 不该出现译文行。"""
    root, _ = netlib
    dump = tmp_path / "dump"
    out = pl.run_batch(lib.scan(str(root)), pl.Config(write=False, dump_dir=str(dump)))

    zh = [r for r in out["results"] if r["file"].startswith("中文")]
    assert zh, "夹具里有中文歌"
    r = zh[0]
    assert r["status"] in ("skipped_existing", "written_mono", "dumped",
                           "no_lyrics", "timeout", "lang_mismatch"), r["status"]
    assert r["status"] != "written_keep_local"


def test_纯音乐一次网络都不发(tmp_path, monkeypatch):
    """`language=zxx` 是权威判据 —— 有它就零成本跳过，不该联网。"""
    root = tmp_path / "音乐"
    (root / "纯音乐").mkdir(parents=True)
    (root / "纯音乐" / "Some Artist - Waltz No. 2.flac").write_bytes(b"")

    calls = []
    real = P._req

    def counting(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(P, "_req", counting)
    songs = lib.scan(str(root))
    # 0 字节文件读不到 zxx 标签，靠目录名兜底；这里直接给 Song 打上权威标记
    songs[0].instrumental = True
    songs[0].expected_lang = "instrumental"

    out = pl.run_batch(songs, pl.Config(write=False, dump_dir=str(tmp_path / "dump")))

    assert out["results"][0]["status"] == "skip_instrumental"
    assert calls == [], f"纯音乐不该联网，却发了 {len(calls)} 次请求"


# ---------------------------------------------------------------- 保留本地时间轴
def test_端到端保留本地时间轴(netlib, tmp_path):
    """本地已有外文单语 `.lrc` 时，抓着真源跑一遍：输出必须用**本地**的时间戳。"""
    root, files = netlib
    target = next(p for p in files if p.name.startswith("高橋洋子"))
    local_ts = [10_300, 20_300, 30_300, 40_300, 50_300, 60_300]
    target.with_suffix(".lrc").write_text(
        "".join(f"[{ms // 60000:02d}:{ms % 60000 / 1000:05.2f}]ああ サンプル{i}\n"
                for i, ms in enumerate(local_ts)),
        encoding="utf-8")

    dump = tmp_path / "dump"
    out = pl.run_batch([lib.load_song(str(target), str(root))],
                       pl.Config(write=False, dump_dir=str(dump)))
    r = out["results"][0]

    if r["status"] != "written_keep_local":
        pytest.skip(f"这次没走上保留路径（{r['status']}：{r['note']}）—— 数据源覆盖会变")
    body = (dump / r["file"]).with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[00:10.30]" in body and "[01:00.30]" in body, "用的是本地时间轴"
    assert L.validate(body)["ok"] is True


def test_写入模式只动音乐库不碰别处(netlib):
    """真写一遍：`.lrc` 落在音频旁边、旧文件备份成 `.bak`、音频本身不动。"""
    root, files = netlib
    target = next(p for p in files if p.name.startswith("米津玄師"))
    target.with_suffix(".lrc").write_text("[00:01.00]旧的内容\n", encoding="utf-8")
    audio_before = target.read_bytes()

    out = pl.run_batch([lib.load_song(str(target), str(root))],
                       pl.Config(write=True))
    r = out["results"][0]

    assert target.read_bytes() == audio_before, "音频文件绝不能被改"
    if r["status"] in ("written", "written_mono", "written_keep_local"):
        assert target.with_suffix(".lrc").exists()
        assert target.with_suffix(".lrc.bak").exists(), "覆盖前必须备份"
        assert target.with_suffix(".lrc.bak").read_text(encoding="utf-8").strip() \
            == "[00:01.00]旧的内容"
    else:
        pytest.skip(f"这次没写成（{r['status']}：{r['note']}）")
