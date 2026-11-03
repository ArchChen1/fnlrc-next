"""编码嗅探与乱码识别。

库里有一批"占位文本被错误转码"的文件（`此歌曲为没有填词的纯音乐，请您欣赏`
被读成 `森貉Ж峈羶衄沓棵腔曾秞氈…`），这类文件必须能被识别出来并重取。
"""
from __future__ import annotations

from conftest import fixture_bytes, fixture_json
from fnlrc import library as lib


def test_utf8():
    text, enc = lib.decode_text("我在抽屉里找到你的信".encode("utf-8"))
    assert text == "我在抽屉里找到你的信"
    # utf-8-sig 解普通 UTF-8 也能成功（BOM 是可选的），所以只断言"是 UTF-8 家族"
    assert enc.startswith("utf-8")


def test_utf8_bom():
    text, enc = lib.decode_text("中文歌词".encode("utf-8-sig"))
    assert text == "中文歌词"
    assert not text.startswith("﻿")


def test_gbk():
    raw = fixture_bytes("lrc_gbk.lrc")
    text, enc = lib.decode_text(raw)
    assert "我在抽屉里找到你的信" in text
    assert enc in ("gbk", "utf-8")   # 只要解得对就行，具体编码名不作硬约束


def test_utf16():
    text, _ = lib.decode_text("测试歌词".encode("utf-16"))
    assert "测试歌词" in text


def test_替换字符判为损坏():
    assert lib.looks_broken("[00:01.00]abc�def") == "replacement_char"


def test_乱码占位文本被判为损坏():
    text = fixture_bytes("lrc_broken_mojibake.lrc").decode("utf-8")
    reason = lib.looks_broken(text)
    assert reason is not None and reason.startswith("mojibake:")


def test_正常占位文本不算乱码():
    """合法写着的"纯音乐"提示不是损坏 —— 但它也不算有歌词，应当重取。"""
    assert lib.looks_broken("[00:01.00]此歌曲为没有填词的纯音乐，请您欣赏") is None


def test_空文件需要重做(tmp_path):
    p = tmp_path / "empty.lrc"
    p.write_text("   \n\n", encoding="utf-8")
    assert lib.is_broken_lrc(str(p)) is True


def test_缺失文件算损坏(tmp_path):
    assert lib.is_broken_lrc(str(tmp_path / "nope.lrc")) is True


def test_完好的文件不算损坏(tmp_path):
    p = tmp_path / "ok.lrc"
    p.write_text("[00:01.00]line\n", encoding="utf-8")
    assert lib.is_broken_lrc(str(p)) is False


# ---------------------------------------------------------------- 文件名兜底
def test_文件名解析_带序号与破折号():
    assert lib.parse_name("/m/日语/01 - Artist - Title.flac") == ("Artist", "Title")


def test_文件名解析_纯标题():
    assert lib.parse_name("/m/日语/Title.flac") == (None, "Title")


# ---------------------------------------------------------------- 预期语言来源
def test_标签优先于目录():
    assert lib.expected_language({"language": "jpn"}, "英语") == ("ja", "tag")


def test_目录兜底标成_dir():
    assert lib.expected_language({"language": None}, "日语") == ("ja", "dir")


def test_未认识的标签码会落到目录推断_且来源正确():
    """`nob` 不在 LANG_MAP 里 → 会落到目录推断，来源必须标成 dir。

    所以"标签非空"不能当作"来源可信"的判据，必须看返回值。
    """
    assert lib.expected_language({"language": "nob"}, "英语") == ("latin", "dir")
    assert lib.expected_language({"language": "nob"}, "小语种") == (None, None)


def test_zxx_是纯音乐的权威判据():
    assert lib.expected_language({"language": "zxx"}, "纯音乐") == ("instrumental", "tag")


def test_GBK被按Big5解码的乱码要被抓出来():
    """真机实测到的形态：GBK 的中文被按 Big5 解，存成了**合法 UTF-8**。

    它既没有替换字符，也不是单字节那类乱码 —— 只查 latin-1 是抓不到的，
    所以必须把「原编码 → 被当成什么解」这一对一起试。
    """
    text = fixture_bytes("lrc_broken_gbk_as_big5.lrc").decode("utf-8")
    assert "\ufffd" not in text          # 表面上看不出坏
    assert lib.looks_broken(text) == "mojibake:gbk-as-big5"


def test_正常中文歌词不会被误判成乱码():
    """反推检测不能误伤 —— 正常的中文歌词必须判为"没坏"。"""
    normal = "[00:01.00]越过山川大海\n[00:05.00]只为见你一面\n[00:09.00]春风十里不如你\n"
    assert lib.looks_broken(normal) is None


def test_正常日文与英文歌词也不会被误判():
    for text in ("[00:01.00]残酷な天使のように\n[00:05.00]少年よ 神話になれ\n",
                 "[00:01.00]Take me to your heart\n[00:05.00]Take me to your soul\n"):
        assert lib.looks_broken(text) is None
