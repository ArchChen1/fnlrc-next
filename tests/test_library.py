"""库扫描 / 标签读取 / 统计。

用临时目录 + 占位文件跑 —— 标签读不出来时（不是真音频）正好验证文件名兜底路径。
"""
from __future__ import annotations

import os
from pathlib import Path

from fnlrc import library as lib


def make_library(root: Path) -> None:
    (root / "日语").mkdir(parents=True, exist_ok=True)
    (root / "英语").mkdir(parents=True, exist_ok=True)
    (root / "纯音乐").mkdir(parents=True, exist_ok=True)
    (root / "@eaDir").mkdir(parents=True, exist_ok=True)       # 群晖缩略图目录，要跳过
    (root / ".hidden").mkdir(parents=True, exist_ok=True)
    (root / "#recycle").mkdir(parents=True, exist_ok=True)

    (root / "日语" / "01 - Artist - Title.flac").write_bytes(b"x")
    (root / "日语" / "02 - Another - Track.mp3").write_bytes(b"x")
    (root / "英语" / "Song.m4a").write_bytes(b"x")
    (root / "纯音乐" / "piano.ogg").write_bytes(b"x")
    (root / "@eaDir" / "thumb.flac").write_bytes(b"x")          # 要跳过
    (root / ".hidden" / "hidden.flac").write_bytes(b"x")        # 要跳过
    (root / "cover.jpg").write_bytes(b"x")                      # 非音频，要跳过
    # 一份合规双语（5 个**不同**时间戳、每个两行 —— 同时间戳重复 5 次只算 1 组）
    (root / "日语" / "01 - Artist - Title.lrc").write_text(
        "".join(f"[00:{i:02d}.00]原文{i}\n[00:{i:02d}.00]译文{i}\n" for i in range(5)),
        encoding="utf-8")
    # 一份乱码
    (root / "英语" / "Song.lrc").write_bytes(b"\xff\xfe\x00bad\x00")


def test_iter_audio_跳过系统目录与非音频(tmp_path):
    make_library(tmp_path)
    rels = {os.path.relpath(p, tmp_path).replace(os.sep, "/")
            for p in lib.iter_audio(str(tmp_path))}
    # 集合比较：遍历顺序取决于文件系统，不该被断言钉死
    assert rels == {
        "日语/01 - Artist - Title.flac",
        "日语/02 - Another - Track.mp3",
        "英语/Song.m4a",
        "纯音乐/piano.ogg",
    }


def test_scan_统计(tmp_path):
    make_library(tmp_path)
    songs = lib.scan(str(tmp_path))
    assert len(songs) == 4
    assert all(not s.rel.startswith(("@eaDir", ".hidden")) for s in songs)


def test_load_song_文件名兜底(tmp_path):
    make_library(tmp_path)
    song = lib.load_song(str(tmp_path / "日语" / "01 - Artist - Title.flac"), str(tmp_path))
    # 标签读不出来（不是真音频），应退回文件名解析："01 - " 剥掉、" - " 拆开
    assert song.title == "Title"
    assert song.artist == "Artist"
    assert song.rel.replace(os.sep, "/") == "日语/01 - Artist - Title.flac"
    assert song.lrc_path.endswith(".lrc")


def test_load_song_无艺人时用上级目录名(tmp_path):
    root = tmp_path / "音乐"
    (root / "MyArtist").mkdir(parents=True)
    (root / "MyArtist" / "song.flac").write_bytes(b"x")
    song = lib.load_song(str(root / "MyArtist" / "song.flac"), str(root))
    assert song.artist == "MyArtist"


def test_load_song_语言目录名不当艺人(tmp_path):
    """`日语` 这类分类目录名不是艺人，不能被当成 artist。"""
    make_library(tmp_path)
    song = lib.load_song(str(tmp_path / "英语" / "Song.m4a"), str(tmp_path))
    assert song.artist != "英语"
    assert song.expected_lang == "latin"          # 但目录名可以推断语言
    assert song.expected_lang_source == "dir"


def test_expected_lang_source_区分标签与目录(tmp_path):
    make_library(tmp_path)
    song = lib.load_song(str(tmp_path / "日语" / "02 - Another - Track.mp3"), str(tmp_path))
    assert song.expected_lang == "ja"
    assert song.expected_lang_source == "dir"


def test_summarize(tmp_path):
    make_library(tmp_path)
    songs = lib.scan(str(tmp_path))
    s = lib.summarize(songs)
    assert s["total"] == 4
    assert s["bilingual"] == 1                    # 那份 5 组成对的
    assert s["broken"] == 1                       # 那份 UTF-16 乱码
    assert s["missing"] == 2
    assert "日语" in s["by_top"]
    assert s["by_top"]["日语"]["audio"] == 2


def test_top_属性(tmp_path):
    make_library(tmp_path)
    songs = lib.scan(str(tmp_path))
    tops = {s.top for s in songs}
    assert tops == {"日语", "英语", "纯音乐"}


def test_iter_audio_空目录(tmp_path):
    assert list(lib.iter_audio(str(tmp_path))) == []


def test_scan_limit(tmp_path):
    make_library(tmp_path)
    assert len(lib.scan(str(tmp_path), limit=2)) == 2


# ---------------------------------------------------------------- 标签读取
def test_能读出ID3标签(tmp_path):
    """回归：mutagen 对 ID3 容器暴露的键是 `TIT2`/`TPE1`，不是 `title`。

    以前只按友好名查 → MP3/WAV 的标签**全部静默读不出来**、一路退到文件名兜底，
    匹配准确率白掉一截。
    """
    from conftest import make_tagged_wav
    p = make_tagged_wav(tmp_path / "日语" / "track.wav", title="测试标题",
                        artist="测试艺人", album="测试专辑", language="jpn")
    tags = lib.read_tags(str(p))
    assert tags["title"] == "测试标题"
    assert tags["artist"] == "测试艺人"
    assert tags["album"] == "测试专辑"
    assert tags["language"] == "jpn"
    assert tags["duration"] and tags["duration"] > 0


def test_能读出instrumental标记(tmp_path):
    from conftest import make_tagged_wav
    p = make_tagged_wav(tmp_path / "纯音乐" / "piano.wav", title="Piano", instrumental=True)
    assert lib.read_tags(str(p))["instrumental"] is True


def test_普通TXXX帧不会被误判成纯音乐(tmp_path):
    """TXXX 是用户自定义帧，'replaygain' 之类不该被当成纯音乐标记。"""
    from mutagen.id3 import TXXX
    from mutagen.wave import WAVE
    from conftest import make_tagged_wav
    p = make_tagged_wav(tmp_path / "x" / "t.wav", title="T")
    f = WAVE(str(p))
    f.tags.add(TXXX(encoding=3, desc="replaygain_track_gain", text="-6.0 dB"))
    f.save()
    assert lib.read_tags(str(p))["instrumental"] is False


def test_读标签的曲目能定出预期语言(tmp_path):
    from conftest import make_tagged_wav
    root = tmp_path
    p = make_tagged_wav(root / "任意目录" / "t.wav", title="T", artist="A", language="jpn")
    song = lib.load_song(str(p), str(root))
    assert song.title == "T"
    assert song.artist == "A"
    assert song.expected_lang == "ja"
    assert song.expected_lang_source == "tag"      # 来自标签而不是目录名


def test_标签缺失时退回文件名(tmp_path):
    from conftest import make_tagged_wav
    p = make_tagged_wav(tmp_path / "日语" / "01 - Artist - Title.wav")
    song = lib.load_song(str(p), str(tmp_path))
    assert song.title == "Title"
    assert song.artist == "Artist"


def test_损坏文件读标签不抛异常(tmp_path):
    """解析不了也不能抛 —— 退回文件名兜底，duration 为空。

    artist 会退到上级目录名（这里就是 pytest 的临时目录名），所以不在这里断言。
    """
    p = tmp_path / "broken.flac"
    p.write_bytes(b"not audio at all")
    tags = lib.read_tags(str(p))
    assert tags["title"] == "broken"        # 文件名兜底
    assert tags["duration"] is None
    assert tags["language"] is None


def test_不存在的文件读标签不抛异常(tmp_path):
    tags = lib.read_tags(str(tmp_path / "nope.mp3"))
    assert tags["title"] == "nope"
    assert tags["duration"] is None


# ---------------------------------------------------------------- 统计细节
def test_summarize_单语计数(tmp_path):
    """单语 .lrc（不是乱码、也不是双语）要单独计入 mono。"""
    root = tmp_path
    (root / "日语").mkdir(parents=True)
    (root / "日语" / "a.flac").write_bytes(b"x")
    (root / "日语" / "a.lrc").write_text(
        "".join(f"[00:{i:02d}.00]単語{i}\n" for i in range(6)), encoding="utf-8")
    s = lib.summarize(lib.scan(str(root)))
    assert s["mono_or_single"] == 1
    assert s["bilingual"] == 0
    assert s["broken"] == 0


def test_summarize_读取失败计入broken(tmp_path, monkeypatch):
    root = tmp_path
    (root / "日语").mkdir(parents=True)
    (root / "日语" / "a.flac").write_bytes(b"x")
    (root / "日语" / "a.lrc").write_text("[00:01.00]x\n", encoding="utf-8")

    def boom(_):
        raise OSError("simulated I/O error")
    monkeypatch.setattr(lib, "read_text", boom)
    monkeypatch.setattr(lib, "is_broken_lrc", lambda p: False)
    s = lib.summarize(lib.scan(str(root)))
    assert s["broken"] == 1


def test_summarize_纯音乐计数(tmp_path):
    from conftest import make_tagged_wav
    root = tmp_path
    make_tagged_wav(root / "纯音乐" / "p.wav", title="P", language="zxx")
    s = lib.summarize(lib.scan(str(root)))
    assert s["instrumental"] == 1
