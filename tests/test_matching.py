"""匹配打分、版本消歧、行角色分类。

这里覆盖的每一条都是实测踩出来的：
  · 字符集重合率把 `Hello`/`Halo` 打成 0.75
  · 时长只扣分挡不住 Live 版
  · 版本标记先被 `base_title` 剥掉、导致降权静默失效
  · 罗马音混排被当成正文
断言的是**关系与行为**（排序、淘汰、角色），不是"歌词等于某段固定文字"——
第三方内容会变，快照式断言没有意义（见 tests/fixtures/README.md）。
"""
from __future__ import annotations

import pytest

from conftest import fixture_json
from fnlrc import library as lib
from fnlrc import lrc as L
from fnlrc import providers as P


# ---------------------------------------------------------------- 标题清洗
@pytest.mark.parametrize("raw,want", [
    ("Song (Cover)", "Song (Cover)"),          # 翻唱标记不是噪声，要留着给降权用
    ("Song (Live)", "Song (Live)"),
    ("Bella ciao (啊朋友再见)", "Bella ciao (啊朋友再见)"),   # 合法括号标题，绝不能剥
    ("告白の夜 (告白之夜)", "告白の夜 (告白之夜)"),
    ("Violet Snow (Short Size)", "Violet Snow"),
    ("みちしるべ (Short Size)", "みちしるべ"),
    ("Album - Track (TV Size)", "Album - Track"),
    ("X (Director's Edit. Version)", "X"),
    ("Z (Album Ver.)", "Z"),
    ("Y - Remastered 2011", "Y"),
    ("Song_hires", "Song"),
    ("Song_H", "Song"),
    ("心拍数＃0822", "心拍数＃0822"),
])
def test_base_title(raw, want):
    assert lib.base_title(raw) == want


def test_base_title_不会剥成空():
    assert lib.base_title("(Short Size)") == "(Short Size)"


# ---------------------------------------------------------------- 相似度
def test_标题相似度_完全相同():
    assert P.title_sim("Teeth", "Teeth") == 1.0
    assert P.title_sim("心拍数＃0822", "心拍数#0822") == 1.0   # 全半角归一
    assert P.title_sim("心拍数♯0822", "心拍数#0822") == 1.0


def test_标题相似度_短标题必须完全相等():
    """三个字符以内的"高重合率"基本都是巧合（`爱` ⊂ `爱如潮水`）。"""
    assert P.title_sim("爱", "爱如潮水") == 0.0
    assert P.title_sim("爱", "爱") == 1.0


def test_标题相似度_带版本后缀仍然是同一首():
    assert P.title_sim("残酷な天使のテーゼ", "残酷な天使のテーゼ (TV Size)") == 1.0
    assert P.title_sim("群青", "群青 (Album Ver.)") == 1.0


def test_标题相似度_不同歌不该拿高分():
    """旧的字符集重合率给这个案例打 0.75，两首不同的歌拿到高分。"""
    assert P.title_sim("Hello", "Halo") < 0.75


def test_艺人比对不受短标题规则影响():
    """艺人名本来就短（H△G 归一化后 3 字符），套短标题规则会把 2.0 的艺人项归零。"""
    assert P.sim("H△G/某人", "H△G") > 0.5
    assert P.sim("2CELLOS", "2CELLOS") == 1.0


# ---------------------------------------------------------------- 版本消歧
@pytest.mark.parametrize("query,cand,want", [
    ("告白の夜", "告白の夜 (live version)", -2.5),   # ← 实测漏网的那一条
    ("Song", "Song (Cover)", -2.5),
    ("Song", "Song（翻自 某某）", -2.5),
    ("Song", "Song (Remix)", -2.5),
    ("Song", "Song", 0.0),
    ("Song (Live)", "Song (Live)", 0.0),            # 库里存的就是 Live 版，不扣
    ("Song", "Song - Remastered 2011", 0.0),        # 重制版唱同一首词，不扣
])
def test_version_penalty(query, cand, want):
    assert P.version_penalty(query, cand) == want


def test_version_penalty_只看原始标题():
    """版本标记本身就写在噪声清单里 —— 先剥再找等于永远找不到。"""
    assert P.version_penalty("告白の夜", "告白の夜 (live version)") != 0.0


def test_live版会被压到门槛以下():
    live = P._score("告白の夜 (live  version)", "Ayasa", 0, "告白の夜", "Ayasa绚沙", None)
    assert live < 4.0


# ---------------------------------------------------------------- 时长硬闸
@pytest.mark.parametrize("cand_ms,dur,want", [
    (246000, 246.0, True),
    (250000, 246.0, True),
    (256000, 246.0, True),           # 差 10.0s，恰好在容差上（含等号）
    (257000, 246.0, False),          # 差 11s，越界淘汰
    (300000, 246.0, False),          # 加长版/Live 版的典型差距
    (None, 246.0, True),             # 任一侧缺时长 → 不判断
    (246000, None, True),
])
def test_duration_ok(cand_ms, dur, want):
    assert P.duration_ok(cand_ms, dur) is want


# ---------------------------------------------------------------- 行角色
def test_罗马音混排_同时间戳对齐():
    entries = [(0, "残酷な天使のように"), (0, "zankoku na tenshi no you ni"),
               (2000, "少年よ"), (2000, "shounen yo")]
    orig, roman = P.split_orig_roles(entries)
    assert [t for _, t in orig] == ["残酷な天使のように", "少年よ"]
    assert [t for _, t in roman] == ["zankoku na tenshi no you ni", "shounen yo"]


def test_罗马音混排_交替递增时间戳():
    entries = [(0, "残酷な天使のように"), (1000, "zankoku na tenshi no you ni"),
               (2000, "少年よ"), (3000, "shounen yo")]
    orig, roman = P.split_orig_roles(entries)
    assert len(orig) == 2 and len(roman) == 2


def test_孤立的英文短语不算罗马音():
    """副歌里冒一个 `Yeah` 就把它当罗马音摘掉，会平白少一行歌词。"""
    entries = [(0, "残酷な天使のように"), (1000, "少年よ"),
               (2000, "Yeah"), (3000, "神話になれ")]
    orig, roman = P.split_orig_roles(entries)
    assert len(orig) == 4 and roman == []


def test_整段英文翻译不算罗马音():
    entries = [(0, "残酷な天使のように"), (1000, "少年よ"),
               (2000, "Like a cruel angel"), (3000, "Young man become a legend")]
    orig, roman = P.split_orig_roles(entries)
    assert len(orig) == 4 and roman == []


def test_纯拉丁歌不做罗马音拆分():
    orig, roman = P.split_orig_roles([(0, "I found your letter"), (1, "in the drawer")])
    assert len(orig) == 2 and roman == []


# ---------------------------------------------------------------- 译文择一
def test_译文角色判定():
    assert P.translation_role("就像那残酷的天使一样", "ja") == "trans"
    assert P.translation_role("zankoku na tenshi no you ni", "ja") == "roman"
    assert P.translation_role("我在抽屉里找到你的信", "latin") == "trans"


def test_多译文择一_优先真译文():
    picked = P.pick_translation(
        ["zankoku na tenshi no you ni", "就像那残酷的天使一样"], "残酷な天使のように")
    assert picked == "就像那残酷的天使一样"


def test_多译文择一_剔掉原文的副本():
    """有些上传把原文再抄一遍当"译文"。不剔的话，英文歌会因为"英文更长"而选中原文自己。"""
    picked = P.pick_translation(["I found your letter", "我在抽屉里找到你的信"],
                                "I found your letter")
    assert picked == "我在抽屉里找到你的信"


def test_只有原文副本时不算有译文():
    assert P.pick_translation(["I found your letter"], "I found your letter") is None
    assert P.pick_translation([], "x") is None


# ---------------------------------------------------------------- 契约：netease
def test_netease_候选排序与淘汰(monkeypatch):
    """用合成响应验证：正确的那首排第一，Live 版 / 时长严重不符的都被压下去。"""
    payload = fixture_json("netease_search.json")
    lyric = fixture_json("netease_lyric.json")
    monkeypatch.setattr(P, "_ne_search", lambda q, limit=8, deadline=None: payload["result"]["songs"])
    monkeypatch.setattr(P, "_ne_lyric", lambda sid, deadline=None: {
        "lrc": lyric["lrc"]["lyric"],
        "tlyric": lyric["tlyric"]["lyric"],
        "romalrc": lyric["romalrc"]["lyric"],
        "nolyric": False,
    })

    info = P.netease_fetch("Sample Song", "Original Artist", 246.0, expected="ja")
    assert info is not None
    assert info["song_id"] == 1001                      # 原曲，不是 Live / 翻唱
    pool = info["candidates"]
    assert pool[0]["song_id"] == 1001
    # 时长严重不符（310s vs 246s）的 1006 必须在收集阶段就被淘汰，进不了候选池
    assert 1006 not in [c["song_id"] for c in pool]
    # Live 版虽然进了池子，但有效分必须低于原曲
    live = next((c for c in pool if c["song_id"] == 1002), None)
    assert live is None or live["effective"] < pool[0]["effective"]


def test_netease_无歌词标记会跳过候选(monkeypatch):
    payload = fixture_json("netease_search.json")
    monkeypatch.setattr(P, "_ne_search", lambda q, limit=8, deadline=None: payload["result"]["songs"])
    monkeypatch.setattr(P, "_ne_lyric",
                        lambda sid, deadline=None: {"lrc": "", "tlyric": "", "romalrc": "", "nolyric": True})
    assert P.netease_fetch("Sample Song", "Original Artist", 246.0) is None


def test_netease_异常不外泄(monkeypatch):
    """provider 抛异常绝不能让整轮崩 —— 降级成 None，由上层换源。"""
    def boom(*a, **k):
        raise RuntimeError("simulated network failure")
    monkeypatch.setattr(P, "_ne_search", boom)
    assert P.netease_fetch("x", "y", 100.0) is None


def test_返回字典保留了脚本依赖的键(monkeypatch):
    """scripts/smoke_sources.py 与 scripts/debug_song.py 直接读这些键，改名即崩。"""
    payload = fixture_json("netease_search.json")
    lyric = fixture_json("netease_lyric.json")
    monkeypatch.setattr(P, "_ne_search", lambda q, limit=8, deadline=None: payload["result"]["songs"])
    monkeypatch.setattr(P, "_ne_lyric", lambda sid, deadline=None: {
        "lrc": lyric["lrc"]["lyric"], "tlyric": lyric["tlyric"]["lyric"],
        "romalrc": lyric["romalrc"]["lyric"], "nolyric": False})
    info = P.netease_fetch("Sample Song", "Original Artist", 246.0)
    for key in ("source", "song_id", "matched", "score", "lyric_lang",
                "lang_mismatch", "pairs", "has_translation", "candidates"):
        assert key in info, f"缺少脚本依赖的键：{key}"


def test_制作名单不进正文(monkeypatch):
    """制作名单行会把成对比例拉低，必须在配对前就剔掉。"""
    payload = fixture_json("netease_search.json")
    lyric = fixture_json("netease_lyric.json")
    monkeypatch.setattr(P, "_ne_search", lambda q, limit=8, deadline=None: payload["result"]["songs"])
    monkeypatch.setattr(P, "_ne_lyric", lambda sid, deadline=None: {
        "lrc": lyric["lrc"]["lyric"], "tlyric": lyric["tlyric"]["lyric"],
        "romalrc": lyric["romalrc"]["lyric"], "nolyric": False})
    info = P.netease_fetch("Sample Song", "Original Artist", 246.0)
    joined = " ".join(o for _, o, _, _ in info["pairs"])
    assert "作词" not in joined and "作曲" not in joined


def test_输出只有原文与译文两行(monkeypatch):
    """用户明确要求：只显示源语言 + 翻译，不要罗马音挤进来。"""
    payload = fixture_json("netease_search.json")
    lyric = fixture_json("netease_lyric.json")
    monkeypatch.setattr(P, "_ne_search", lambda q, limit=8, deadline=None: payload["result"]["songs"])
    monkeypatch.setattr(P, "_ne_lyric", lambda sid, deadline=None: {
        "lrc": lyric["lrc"]["lyric"], "tlyric": lyric["tlyric"]["lyric"],
        "romalrc": lyric["romalrc"]["lyric"], "nolyric": False})
    info = P.netease_fetch("Sample Song", "Original Artist", 246.0)
    for ms, orig, trans, roman in info["pairs"]:
        assert orig                       # 每行都有原文
        assert roman is not None          # 罗马音被单独放着
    body = L.build_bilingual_roles("t", "a", "", info["pairs"])
    assert "sanpuru" not in body          # 默认输出里没有罗马音
    assert body.count("[00:02.00]") == 2  # 每个时间戳恰好两行


def test_lrc里混排罗马音_会被摘出去(monkeypatch):
    payload = fixture_json("netease_search.json")
    mixed = fixture_json("netease_lyric_roman_mixed.json")
    monkeypatch.setattr(P, "_ne_search", lambda q, limit=8, deadline=None: payload["result"]["songs"])
    monkeypatch.setattr(P, "_ne_lyric", lambda sid, deadline=None: {
        "lrc": mixed["lrc"]["lyric"], "tlyric": "", "romalrc": "", "nolyric": False})
    info = P.netease_fetch("Sample Song", "Original Artist", 246.0)
    assert info is not None
    texts = [o for _, o, _, _ in info["pairs"]]
    assert "aa sanpuru" not in texts      # 罗马音没混进原文
    assert any("サンプル" in t for t in texts)
    romans = [r for _, _, _, r in info["pairs"] if r]
    assert romans, "罗马音应当被单独保留下来"


# ---------------------------------------------------------------- 译文对齐（保留本地时间轴）
def test_对齐优先用时间戳():
    """本地时间轴是基准：译文必须挂到本地的毫秒上，不是源站的。"""
    local = [(10000, "ああ サンプル0"), (20000, "ああ サンプル1"),
             (30000, "ああ サンプル2"), (40000, "ああ サンプル3")]
    fetched = [(10300, "ああ サンプル0", "译0", None), (20300, "ああ サンプル1", "译1", None),
               (30300, "ああ サンプル2", "译2", None), (40300, "ああ サンプル3", "译3", None)]
    rows, hits = P.align_translation(local, fetched)
    assert [ms for ms, _, _ in rows] == [10000, 20000, 30000, 40000]
    assert hits == 4


def test_对齐时优先挑有译文的候选():
    """同一时刻附近常同时有原文行和罗马音行 —— 罗马音那行没译文，别挑它。"""
    local = [(10000, "ああ サンプル0")]
    fetched = [(10000, "ああ サンプル0", None, "aa sanpuru0"),        # 罗马音那行
               (10100, "ああ サンプル0", "译0", None)]                 # 有译文
    rows, hits = P.align_translation(local, fetched)
    assert rows == [(10000, "ああ サンプル0", "译0")]
    assert hits == 1


def test_对齐时靠文本兜底():
    """时间戳完全对不上（两边时间轴来源不同）时，按原文文本相似度配。"""
    local = [(5000, "ああ サンプル0"), (6000, "ああ サンプル1")]
    fetched = [(1000, "ああ サンプル0", "译0", None), (2000, "ああ サンプル1", "译1", None)]
    rows, hits = P.align_translation(local, fetched)
    assert hits == 2
    assert [ms for ms, _, _ in rows] == [5000, 6000]


def test_对齐配不上就留空而不是乱配():
    """配错的代价比少配一行大得多 —— 文本不像、时间又远，就老实留 None。"""
    local = [(5000, "ああ サンプル0"), (6000, "ああ サンプル1")]
    fetched = [(90000, "全然違う歌詞です", "译0", None),
               (91000, "まるで別の曲", "译1", None)]
    rows, hits = P.align_translation(local, fetched)
    assert hits == 0
    assert all(t is None for _, _, t in rows)


def test_对齐不重复消费同一行():
    """本地两行共用同一个源站行时，只有先到的那行能拿到译文。"""
    local = [(10000, "ああ サンプル0"), (10000 + 100, "ああ サンプル0")]
    fetched = [(10000, "ああ サンプル0", "译0", None)]
    rows, hits = P.align_translation(local, fetched)
    assert hits == 1
    assert rows[0][2] == "译0" and rows[1][2] is None


# ---------------------------------------------------------------- LRCLIB 契约
class _Resp:
    """最小响应桩：够 _req 的调用方用（status_code + json()）。"""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _stub_lrclib(monkeypatch, payload, *, search=None):
    """把 _req 换掉：/api/get 返回 payload，/api/search 返回 search（默认空）。

    `payload=None` 模拟真实世界里 `/api/get` **没命中** —— LRCLIB 这时回 404，
    而不是回一个空对象。别用"返回 None 让调用方抛异常"来糊弄，那样测的是异常路径
    而不是我们想测的解析路径。
    """
    seen = []

    def fake(method, url, **kw):
        seen.append((url, kw.get("params")))
        if url.endswith("/api/get"):
            if payload is None:
                return _Resp({}, status=404)
            return _Resp(payload)
        return _Resp(search or [])

    monkeypatch.setattr(P, "_req", fake)
    return seen


def test_lrclib_get命中时解析出时间轴(monkeypatch):
    """`/api/get` 命中 → 用 `syncedLyrics` 解析出 (ms, 原文) 列表。

    这条以前没有测试 —— 夹具 `lrclib_get.json` 一直存在但没人读它，
    文档却声称"LRCLIB 解析有契约测试覆盖"。
    """
    d = fixture_json("lrclib_get.json")
    _stub_lrclib(monkeypatch, d)

    got = P.lrclib_fetch("Sample Song", "Original Artist", "Sample Album", 246.0)

    assert got and got["entries"], "应当解析出歌词行"
    ms_list = [ms for ms, _ in got["entries"]]
    assert ms_list == [2000, 6000, 10000, 14000, 18000, 22000]
    assert got["entries"][0][1] == "line one"
    assert got["source"] == "lrclib"
    assert not got.get("instrumental")


def test_lrclib标记为纯音乐时不返回歌词(monkeypatch):
    """源站标了 `instrumental` 就不能当歌词用 —— 否则器乐曲会被写上"歌词"。"""
    d = dict(fixture_json("lrclib_get.json"), instrumental=True)
    _stub_lrclib(monkeypatch, d)

    got = P.lrclib_fetch("Sample Song", "Original Artist", "Sample Album", 246.0)

    assert got is None or got.get("instrumental") or not got.get("entries")


def test_lrclib没有同步歌词时不当成命中(monkeypatch):
    """只有 `plainLyrics`、没有 `syncedLyrics` 的响应要跳过 —— 没有时间轴就没法做双行。"""
    d = dict(fixture_json("lrclib_get.json"), syncedLyrics=None)
    _stub_lrclib(monkeypatch, d)

    got = P.lrclib_fetch("Sample Song", "Original Artist", "Sample Album", 246.0)
    assert not (got and got.get("entries"))


def test_lrclib搜索结果里时长差太多的候选被淘汰(monkeypatch):
    """时长硬淘汰：标题像但时长差 >10s 的候选直接丢，别让它靠标题分胜出。"""
    good = dict(fixture_json("lrclib_get.json"))
    far = dict(good, id=900002, trackName="Sample Song (Live Version)", duration=520)
    _stub_lrclib(monkeypatch, None, search=[far, good])

    got = P.lrclib_fetch("Sample Song", "Original Artist", "Sample Album", 246.0)

    assert got and got.get("matched")
    assert "Live" not in (got.get("matched") or ""), "时长 520s 的 Live 版不该被选中"
