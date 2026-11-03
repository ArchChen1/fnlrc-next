"""LRC 解析 / 规整 / 双语生成 / 飞牛规则校验。

飞牛音乐双行显示的硬规则（从应用 bundle 的 lrc-parser 实测得出）：
  1. 至少 4 个时间戳成对
  2. 从第一对开始，成对比例 >= 80%
  3. 前奏最多 8 行单语
  4. 不能与逐字卡拉OK标签 <mm:ss.xx> 混用（混用则走逐字着色，双行失效）
"""
import re
from collections import defaultdict

# [mm:ss.xx] [mm:ss:xx] [mm:ss] [mm:ss.xxx] 以及全角括号
TS = r"(?:\[|\uff3b)(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?(?:\]|\uff3d)"
TS_RE = re.compile(TS)
LINE_RE = re.compile(r"^(?:" + TS + r"\s*)+")
META_RE = re.compile(r"^\s*(?:\[|\uff3b)(ti|ar|al|by|offset|re|ve|length|kana)\s*:(.*?)(?:\]|\uff3d)\s*$", re.I)
KARAOKE_RE = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")

# 制作人员/版权等非歌词行（会破坏成对比例，必须剔除）
CREDIT_WORDS = (
    "作词", "作曲", "编曲", "制作人", "混音", "母带", "录音", "和声", "监制", "出品", "企划",
    "翻译", "校对", "字幕", "歌词", "伴奏", "吉他", "贝斯", "鼓", "键盘", "弦乐", "制作",
    "词曲", "词：", "曲：",
    "produced by", "written by", "composed by", "lyrics by", "music by", "arranged by",
    "mixed by", "mastered by", "recorded by", "vocal", "guitar", "bass", "drum", "piano",
    "strings", "engineer", "studio", "op :", "sp :", "ed :", "op:", "sp:", "ed:",
)


def to_ms(mm: str, ss: str, frac: str | None) -> int:
    ms = int(mm) * 60000 + int(ss) * 1000
    if frac:
        ms += int(frac.ljust(3, "0")[:3]) if len(frac) <= 3 else int(frac[:3])
    return ms


def is_credit(text: str) -> bool:
    t = text.strip().lower().lstrip("*·-— ")
    if not t:
        return True
    for w in CREDIT_WORDS:
        if t.startswith(w.lower()) or w.lower() in t[:14]:
            return True
    return False


def parse_lrc(text: str, drop_credits: bool = True) -> list[tuple[int, str]]:
    """→ [(毫秒, 行文本)]，已排序、去空行、去制作名单、去卡拉OK标签。"""
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw.strip() or META_RE.match(raw):
            continue
        stamps = TS_RE.findall(raw)
        if not stamps:
            continue
        content = KARAOKE_RE.sub("", TS_RE.sub("", raw)).strip()
        content = content.strip("|｜").strip()
        if not content:
            continue
        if drop_credits and is_credit(content):
            continue
        ms = to_ms(*stamps[0])
        out.append((ms, content))
    # 去重（同一时间戳同一文本），按时间排序
    seen = set()
    uniq = []
    for ms, txt in sorted(out, key=lambda x: x[0]):
        k = (ms, txt)
        if k in seen:
            continue
        seen.add(k)
        uniq.append((ms, txt))
    return uniq


def has_karaoke(text: str) -> bool:
    return bool(KARAOKE_RE.search(text))


KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")
KANJI = re.compile(r"[\u4e00-\u9fff]")
CYRILLIC = re.compile(r"[\u0400-\u04ff]")
LATIN = re.compile(r"[A-Za-z]")


def detect_lang(text: str) -> str:
    """歌词脚本语言判定 → zh | ja | ko | ru | latin | unknown。

    关键：假名必须优先判定，否则日文歌词（大量汉字）会被误判成中文。
    """
    if KANA.search(text):
        return "ja"
    if HANGUL.search(text):
        return "ko"
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "unknown"
    n = len(letters)
    if len(KANJI.findall(text)) / n > 0.5:
        return "zh"
    if len(CYRILLIC.findall(text)) / n > 0.3:
        return "ru"
    if len(LATIN.findall(text)) / n > 0.5:
        return "latin"
    return "other"


def fmt_ms(ms: int) -> str:
    return f"[{ms // 60000:02d}:{ms % 60000 / 1000:05.2f}]"


def build_bilingual(title: str, artist: str, album: str, pairs: list[tuple[int, str, str]],
                    by: str = "Hermes", original_first: bool = True) -> str:
    """pairs: [(ms, 原词, 译文)] → LRC 文本（同一时间戳两行，交错排列）。"""
    head = [f"[ti:{title}]", f"[ar:{artist}]"]
    if album:
        head.append(f"[al:{album}]")
    head.append(f"[by:{by}]")
    body = []
    for ms, orig, trans in pairs:
        ts = fmt_ms(ms)
        if original_first:
            body.append(f"{ts}{orig}")
            if trans:
                body.append(f"{ts}{trans}")
        else:
            if trans:
                body.append(f"{ts}{trans}")
            body.append(f"{ts}{orig}")
    return "\n".join(head + body) + "\n"


def build_mono(title: str, artist: str, album: str, entries: list[tuple[int, str]],
               by: str = "Hermes") -> str:
    head = [f"[ti:{title}]", f"[ar:{artist}]"]
    if album:
        head.append(f"[al:{album}]")
    head.append(f"[by:{by}]")
    return "\n".join(head + [f"{fmt_ms(ms)}{txt}" for ms, txt in entries]) + "\n"


# 输出顺序：原文 → 译文 → 罗马音
_ROLE_ORDER = {"orig": 0, "trans": 1, "roman": 2}


def build_bilingual_roles(title: str, artist: str, album: str,
                          rows: list[tuple[int, str, str | None, str | None]],
                          by: str = "Hermes",
                          roles: tuple[str, ...] = ("orig", "trans")) -> str:
    """rows: [(ms, 原词, 译文, 罗马音)] → LRC 文本，按 `roles` 决定输出哪几行。

    默认**只输出「原文 + 译文」**（`roles=("orig","trans")`）：罗马音不写进文件，
    需要时由调用方把它放进 roles 或另存 sidecar。同一时间戳输出多行才会被飞牛
    归为一组 —— 这是双行显示的唯一机制，多输出一行就会变成三行滚动。
    """
    head = [f"[ti:{title}]", f"[ar:{artist}]"]
    if album:
        head.append(f"[al:{album}]")
    head.append(f"[by:{by}]")
    body = []
    for ms, orig, trans, roman in rows:
        ts = fmt_ms(ms)
        picked = {"orig": orig, "trans": trans, "roman": roman}
        for role in sorted(roles, key=lambda r: _ROLE_ORDER.get(r, 9)):
            txt = picked.get(role)
            if txt:
                body.append(f"{ts}{txt}")
    return "\n".join(head + body) + "\n"


def validate(text: str) -> dict:
    """按飞牛 lrc-parser 的实测规则预判是否会双行显示。"""
    lines = [l for l in text.replace("\r\n", "\n").split("\n") if TS_RE.match(l) or LINE_RE.match(l)]
    ts_lines = []
    for l in lines:
        m = LINE_RE.match(l)
        if not m:
            continue
        stamps = TS_RE.findall(l)
        if not stamps:
            continue
        ts_lines.append((to_ms(*stamps[0]), TS_RE.sub("", l).strip()))

    # 同一时间戳是否出现两行 → 成对
    groups = defaultdict(list)
    for ms, txt in ts_lines:
        groups[ms].append(txt)

    paired_ts = [ms for ms, v in groups.items() if len(v) >= 2 and all(x.strip() for x in v)]
    karaoke = has_karaoke(text)

    # 从第一对开始，成对比例
    ordered = sorted(groups)
    first_pair_idx = None
    for i, ms in enumerate(ordered):
        if len(groups[ms]) >= 2:
            first_pair_idx = i
            break
    if first_pair_idx is None:
        ratio = 0.0
        leading = len(ordered)
    else:
        leading = first_pair_idx
        tail = ordered[first_pair_idx:]
        ratio = sum(1 for ms in tail if len(groups[ms]) >= 2) / max(len(tail), 1)

    ok = (len(paired_ts) >= 4 and ratio >= 0.80 and leading <= 8 and not karaoke)
    return {
        "paired_groups": len(paired_ts),
        "leading_mono": leading,
        "pair_ratio": round(ratio, 3),
        "karaoke_tags": karaoke,
        "total_ts": len(ordered),
        "ok": ok,
    }
