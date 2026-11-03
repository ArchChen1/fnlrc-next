"""飞牛双行规则的边界表 —— 这一层的正确性决定歌词在飞牛里到底会不会双行显示。

用例来自 `docs/TECH-NOTES.md` 1.3 的实测记录，与 `docs/TESTING.md` 第二章一一对应。
这里同时钉死了一处文档矛盾：`TECH-NOTES` 曾把「5 行前奏单语 + 成对」记成并行组 10，
实测是 **5**（`paired_groups` 数的是"有 ≥2 行的时间戳"，不是行数）。
"""
from __future__ import annotations

from conftest import grouped, interleaved, karaoke, leading_mono, tail_mono
from fnlrc import lrc as L


def test_交错成对_4组():
    v = L.validate(interleaved(4))
    assert v["ok"] is True
    assert v["paired_groups"] == 4


def test_分段_原文在前译文在后():
    v = L.validate(grouped(4))
    assert v["ok"] is True
    assert v["paired_groups"] == 4


def test_5行前奏单语_之后5组成对():
    v = L.validate(leading_mono(5, pairs=5))
    assert v["ok"] is True
    assert v["paired_groups"] == 5      # 实测 5，不是 10
    assert v["leading_mono"] == 5


def test_前奏9行单语_超过阈值():
    v = L.validate(leading_mono(9, pairs=5))
    assert v["ok"] is False             # 前奏单语上限 8 行


def test_20对加尾部3行单语():
    v = L.validate(tail_mono(pairs=20, tail=3))
    assert v["ok"] is True
    assert v["paired_groups"] == 20


def test_前10对加后20行单语_比例不足():
    v = L.validate(tail_mono(pairs=10, tail=20))
    assert v["ok"] is False
    assert v["paired_groups"] == 10


def test_只有3对_不识别():
    v = L.validate(interleaved(3))
    assert v["ok"] is False
    assert v["paired_groups"] == 3


def test_逐字卡拉OK标签_与双行互斥():
    v = L.validate(karaoke())
    assert v["karaoke_tags"] is True
    assert v["ok"] is False


def test_没有时间戳():
    v = L.validate("just text\nno timestamps here\n")
    assert v["ok"] is False
    assert v["total_ts"] == 0


def test_全角括号时间戳():
    v = L.validate(interleaved(5, style="fullwidth"))
    assert v["ok"] is True
    assert v["paired_groups"] == 5


def test_mmss_无小数():
    v = L.validate(interleaved(5, style="minute_only"))
    assert v["ok"] is True
    assert v["paired_groups"] == 5


def test_冒号分隔毫秒():
    v = L.validate(interleaved(5, style="colon_frac"))
    assert v["ok"] is True


def test_单语文件_validate恒为False():
    """这是个陷阱：单语文件的 paired_groups 是 0，所以 ok 永远为 False。

    pipeline 里"写单语"那条分支必须**绕过**校验（直接返回），
    任何"构造两种产物都跑 validate 取通过的那个"的写法都会把无译文的歌
    判成一无所获，反而一个文件都不写。
    """
    body = "".join(f"[00:{i:02d}.00]line {i}\n" for i in range(10))
    v = L.validate(body)
    assert v["ok"] is False
    assert v["paired_groups"] == 0


# ---------------------------------------------------------------- 解析细节
def test_制作名单行被剔除():
    """制作名单是"单语行"，几行就能把成对比例踢出双行判定。"""
    body = ("[00:00.00] 作词 : 某某人\n"
            "[00:00.00] 作曲 : 另一个人\n"
            "[00:00.00] Produced by Someone\n" + "\n".join(
                f"[00:{i:02d}.00]lyric line {i}" for i in range(5)))
    entries = L.parse_lrc(body)
    texts = [t for _, t in entries]
    assert not any("作词" in t for t in texts)
    assert not any("Produced by" in t for t in texts)
    assert len(entries) == 5


def test_空行与纯空格行被剔除():
    entries = L.parse_lrc("[00:01.00]a\n\n   \n[00:02.00]b\n")
    assert [t for _, t in entries] == ["a", "b"]


def test_同一时间戳同文本去重():
    entries = L.parse_lrc("[00:01.00]same\n[00:01.00]same\n")
    assert len(entries) == 1


def test_时间戳偏差10ms不成组():
    """双行靠**严格同一毫秒** —— 差 10ms 就不该被算作一组。"""
    body = ("[00:01.00]orig 1\n[00:01.01]trans 1\n"
            "[00:11.00]orig 2\n[00:11.01]trans 2\n"
            "[00:21.00]orig 3\n[00:21.01]trans 3\n"
            "[00:31.00]orig 4\n[00:31.01]trans 4\n")
    v = L.validate(body)
    assert v["ok"] is False
    assert v["paired_groups"] == 0


def test_时间戳规整为两位小数():
    assert L.fmt_ms(17630) == "[00:17.63]"
    assert L.fmt_ms(185000) == "[03:05.00]"


def test_双语合成_同时间戳两行():
    body = L.build_bilingual_roles(
        "t", "a", "", [(1000, "orig", "trans", "romaji")])
    lines = [l for l in body.splitlines() if l.startswith("[00:")]
    assert lines == ["[00:01.00]orig", "[00:01.00]trans"]   # 默认不输出罗马音


def test_罗马音不进默认输出():
    body = L.build_bilingual_roles(
        "t", "a", "", [(1000, "orig", None, "romaji")])
    assert "romaji" not in body
    assert body.count("[00:01.00]") == 1


def test_可显式要求输出罗马音():
    body = L.build_bilingual_roles(
        "t", "a", "", [(1000, "orig", "trans", "romaji")], roles=("orig", "trans", "roman"))
    assert body.count("[00:01.00]") == 3


# ---------------------------------------------------------------- 合成与工具
def test_旧版三行合成仍然可用():
    """`build_bilingual` 是三元组版本的合成分解，与 `build_bilingual_roles` 并存。"""
    body = L.build_bilingual("t", "a", "al", [(1000, "orig", "trans")])
    assert "[ti:t]" in body and "[ar:a]" in body and "[al:al]" in body
    assert body.count("[00:01.00]") == 2


def test_译文在前():
    body = L.build_bilingual("t", "a", "", [(1000, "orig", "trans")], original_first=False)
    lines = [l for l in body.splitlines() if l.startswith("[00:")]
    assert lines == ["[00:01.00]trans", "[00:01.00]orig"]


def test_无译文时只输出原文一行():
    body = L.build_bilingual("t", "a", "", [(1000, "orig", "")])
    assert body.count("[00:01.00]") == 1


def test_单语合成():
    body = L.build_mono("t", "a", "", [(1000, "line")])
    assert "[ti:t]" in body and "[00:01.00]line" in body
    assert "[al:" not in body            # 无专辑就不写 al


def test_单语合成带专辑():
    body = L.build_mono("t", "a", "Album", [(1000, "line")])
    assert "[al:Album]" in body


def test_制作名单判定的边界():
    assert L.is_credit("作词 : 某人") is True
    assert L.is_credit("  produced by Someone") is True
    assert L.is_credit("") is True
    assert L.is_credit("我在抽屉里找到你的信") is False


def test_元信息行被跳过():
    entries = L.parse_lrc("[ti:标题]\n[ar:艺人]\n[by:某人]\n[00:01.00]line\n")
    assert [t for _, t in entries] == ["line"]


def test_卡拉OK标签被剥离():
    # 注意别用含「歌词」二字的文本 —— 它在 CREDIT_WORDS 里，会被当制作名单剔掉
    entries = L.parse_lrc("[00:01.00]<00:01.00>逐字<00:02.00>高亮\n")
    assert [t for _, t in entries] == ["逐字高亮"]


def test_歌词里的竖线被清掉():
    entries = L.parse_lrc("[00:01.00]|line|\n")
    assert [t for _, t in entries] == ["line"]


def test_检测逐字标签():
    assert L.has_karaoke("[00:01.00]<00:01.00>x") is True
    assert L.has_karaoke("[00:01.00]x") is False


def test_无法判定的语言():
    assert L.detect_lang("") == "unknown"
    assert L.detect_lang("♪ ♪ ♪") == "unknown"


def test_其他脚本归到other():
    # 希腊字母：既不是汉字/假名/谚文/西里尔，拉丁占比也不到一半
    assert L.detect_lang("αβγδεζηθ") == "other"
