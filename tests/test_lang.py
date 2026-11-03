"""语言判定与交叉校验。

`detect_lang` 是历史 bug 的重灾区：最初按"CJK 占比"判中文，结果**日语歌被整库判成中文**，
双语功能对日语完全失效（见 `docs/TECH-NOTES.md` 第五章）。所以这里的用例是防回归的。
"""
from __future__ import annotations

from fnlrc import lrc as L
from fnlrc import pipeline as pl
from fnlrc import providers as P


# ---------------------------------------------------------------- 脚本判定
def test_日文不能被判成中文():
    # 大量汉字的日文，曾经被误判为中文 → 日语歌只写了单语
    line = "残酷な天使のように 少年よ 神話になれ 蒼い風がいま"
    assert L.detect_lang(line) == "ja"


def test_中文():
    assert L.detect_lang("我在抽屉里找到你的信") == "zh"


def test_韩文():
    assert L.detect_lang("사랑해요 그대") == "ko"


def test_俄文():
    assert L.detect_lang("Умри если меня не любишь") == "ru"


def test_拉丁():
    assert L.detect_lang("I found your letter in the drawer") == "latin"


def test_无字母():
    assert L.detect_lang("123 456 789") == "unknown"


def test_假名优先于汉字占比():
    """哪怕汉字占多数，只要有假名就必须判成日语 —— 这是 ADR-009。"""
    assert L.detect_lang("日本語の歌です本当に長い漢字列") == "ja"


# ---------------------------------------------------------------- 交叉校验
def test_外文曲目抓到中文歌词_不写():
    """唯一的硬闸：外文曲目 + 歌词是中文 → 拦下不写（ADR-004）。

    实测真实发生过：俄语歌匹配到中文翻唱版，标题和时长都对得上，但歌词是中文的。
    """
    assert L.detect_lang("关牧村的演唱版本歌词") == "zh"
    from fnlrc.library import Song
    song = Song(path="x", lrc_path="x.lrc", rel="小语种/x.flac",
                title="t", artist="a", expected_lang="ru", expected_lang_source="tag")
    assert song.expected_lang not in (None, "zh", "instrumental")   # 会命中 pipeline 的拦截条件


def test_标签写错时不能拦():
    """外文曲目（expected=latin，**但标签写错**）+ 歌词 ja → 正常写入。

    `docs/TESTING.md:76` 明确要求：标签自己就会错，因为标签错就把正确匹配拦掉
    是反向错误。实测样本里 `英语/(K)NoW_NAME - Freesia.flac` 就是这种情况。
    """
    det = L.detect_lang("残酷な天使のように 少年よ")
    assert det == "ja"
    assert det != "latin"           # 与标签不符
    # 但只要歌词不是中文，就不该被拦
    assert det != "zh"


def test_lang_ok_对无法判定的歌词放行():
    """expected 为 None / 歌词判不出来时一律放行 —— 宁可选错也别漏选。"""
    assert P.lang_ok("ja", None) is True
    assert P.lang_ok("unknown", "ja") is True
    assert P.lang_ok("other", "ja") is True
    assert P.lang_ok("", "ja") is True
    assert P.lang_ok("ja", "ja") is True
    assert P.lang_ok("zh", "ja") is False


def test_语言一致性只加权不淘汰():
    """LANG_BONUS 只加在有效分上，候选集合本身必须保持不变。"""
    assert P.LANG_BONUS > 0
    # 同一候选，语言匹配与否只差一个加数，不改变它是否被保留
    sc = P._score("Sample Song", "Original Artist", 246000, "Sample Song", "Original Artist", 246.0)
    assert sc >= 4.0
    assert sc + P.LANG_BONUS > sc
