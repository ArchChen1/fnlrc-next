"""库扫描 / 标签读取 / 编码嗅探 / 纯音乐判定。

这里放的是跟"文件系统与音频元数据"打交道的活，不碰网络也不碰 UI。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from mutagen import File as MutaFile

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".ape", ".wma", ".dsf", ".aac", ".opus"}
SKIP_DIRS = {".", "..", "@eaDir", "#recycle", ".stfolder", ".Trash", "System Volume Information"}
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "big5", "utf-16")

# 占位文本：不算"有歌词"，但要能识别出来（很多是转码坏掉的）
PLACEHOLDER_MARKERS = (
    "纯音乐，请您欣赏", "此歌曲为没有填词的纯音乐", "此歌曲为纯音乐", "没有填词", "请您欣赏",
    "暂无歌词", "纯音乐 请欣赏",
)
# 「原编码 → 被当成另一种编码解码」的组合。真机上实测到的是 GBK 被按 Big5 解。
MISDECODE_PAIRS = (
    ("gbk", "big5"), ("big5", "gbk"),
    ("gbk", "latin-1"), ("big5", "latin-1"), ("shift_jis", "latin-1"),
)
INSTRUMENTAL_HINT = re.compile(
    r"(off ?vocal|instrumental|inst\.|piano ?version|カラオケ|伴奏|纯音乐|纯音|karaoke|演奏)", re.I)

# 搜索前要从标题里剥掉的已知噪声。**只按清单匹配，绝不"剥掉所有括号"** ——
# 库里存在 `Bella ciao (啊朋友再见)` 这类合法标题，通用剥离会把它改坏。
# 剥掉的只是「歌名之后挂的规格/版本后缀」，剥完仍保留原曲名本体。
TITLE_NOISE = (
    re.compile(r"(?i)[\s_\-]+hi?res\b"),                                  # _hires / - hres
    re.compile(r"(?i)[\s_\-]+h$"),                                        # 结尾的 _H
    re.compile(r"(?i)\s*[\(\[（【][^\)\]）】]*\d+\s*k?hz[^\)\]）】]*[\)\]）】]"),  # 【192kHz 24bit】
    # (Short Size) / (TV Size) / (Album Ver.) / (Director's Edit. Version) / (Remastered 2011)
    # 每个关键词都要词边界：没有 \b 的话 `ver\.?` 会在 `(Cover)` 里匹配到 "Co|ver"，
    # 把一个正常的标题剥成空。
    re.compile(
        r"(?i)\s*[\(\[（【][^\)\]）】]{0,24}?"
        r"(?:\bsize\b|\bver\b\.?|\bversion\b|\bedit\b\.?|\bmix\b|\bcut\b|"
        r"\bremaster(?:ed)?\b(?:\s*\d{4})?)"
        r"\s*[\)\]）】]"),
    re.compile(                                                           # - Remastered 2011 / - Short Ver.
        r"(?i)\s*[-–—]\s*(?:remaster(?:ed)?(?:\s*\d{4})?|"
        r"(?:short|tv|original)\s*(?:ver\.?|version|size|mix))\s*$"),
)

# 版本分歧标记：候选标题带了、而原曲名没带的，几乎一定是**另一个版本**
# （Live / 翻唱 / 伴奏 / 重混），歌词可能完全不同。
# 刻意与 INSTRUMENTAL_HINT 放在同一个模块：两张表词汇高度重叠，分开放就会各自演化、
# 慢慢对不上（曾经就漏掉过「演奏」）。同源判定的两个词表要么共用一张，要么挨着放。
# 注意 remaster 不在此列：重制版唱的是同一首词，降权反而会拒掉唯一的正确匹配。
VERSION_MARKERS = re.compile(
    r"(?i)(?:\blive\b|\bcover\b|\bcovered\b|\bremix\b|\bacoustic\b|\bunplugged\b|"
    r"\binstrumental\b|\boff ?vocal\b|\bpiano ?version\b|\bkaraoke\b|"
    r"カラオケ|伴奏|纯音乐|纯音|演奏|翻唱|翻自)")


def base_title(title: str) -> str:
    """剥掉已知的规格/版本噪声后缀，得到用于**匹配**的基名。

    只按 TITLE_NOISE 清单剥；剥不动、或会剥成空串时原样返回 ——
    原始标题永远是第一个查询式，清洗只用来补一次机会。
    """
    s = (title or "").strip()
    for _ in range(3):
        before = s
        for pat in TITLE_NOISE:
            new = pat.sub("", s).strip()
            if new:
                s = new
        s = s.strip(" -–—_:·")
        if s == before:
            break
    return s or (title or "").strip()


# ISO 639-2/1 → 脚本语言（用于"曲目预期语言"与"歌词实际语言"的交叉校验）
LANG_MAP = {
    "zho": "zh", "chi": "zh", "cmn": "zh", "yue": "zh",
    "jpn": "ja", "jp": "ja",
    "kor": "ko", "ko": "ko",
    "eng": "latin", "en": "latin", "fra": "latin", "fre": "latin", "deu": "latin", "ger": "latin",
    "spa": "latin", "ita": "latin", "por": "latin", "nld": "latin", "swe": "latin", "dan": "latin",
    "rus": "ru", "ukr": "ru", "bel": "ru",
    "zxx": "instrumental",
}
DIR_HINT = {"中文": "zh", "日语": "ja", "英语": "latin"}


@dataclass
class Song:
    path: str
    lrc_path: str
    rel: str
    title: str = ""
    artist: str = ""
    album: str = ""
    language: str | None = None      # 标签原文（如 jpn/eng/rus）
    duration: float | None = None
    instrumental: bool = False
    embedded_lyrics: str | None = None
    expected_lang: str | None = None  # zh / ja / latin / ru / instrumental / None
    # 预期语言从哪来的："tag"(language 标签) | "dir"(目录名猜测) | None。
    # 两者的可信度差很多（实测 `英语/` 目录里躺着日语歌），报告与 UI 要分开标注。
    expected_lang_source: str | None = None

    @property
    def has_lrc(self) -> bool:
        return os.path.isfile(self.lrc_path)

    @property
    def top(self) -> str:
        return self.rel.split(os.sep)[0] if os.sep in self.rel else "(root)"


def decode_text(raw: bytes) -> tuple[str, str]:
    """按常见编码依次尝试，返回 (文本, 采用的编码)。"""
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), "utf-8(replace)"


def read_text(path: str) -> tuple[str, str]:
    with open(path, "rb") as fh:
        return decode_text(fh.read())


def looks_broken(text: str) -> str | None:
    """判断歌词文件是不是坏的（乱码/替换字符/错误转码的占位文本）。"""
    if "\ufffd" in text:
        return "replacement_char"
    for m in PLACEHOLDER_MARKERS:
        if m in text:
            return None  # 是"合法占位"，不是乱码
    # 错误转码：原文按 `true_enc` 编码，却被当成 `wrong_enc` 解码后存成了 UTF-8。
    # 反推一遍就能还原：text.encode(wrong_enc) → 原始字节 → decode(true_enc)。
    #
    # 为什么不能只试 latin-1：真机上实测到的形态是 **GBK 的中文被按 Big5 解**，
    # 结果仍然是一串合法中文（"看着像中文、其实全是别的字"），而且是合法 UTF-8 ——
    # 既没有替换字符、也不是单字节那类乱码，只查 latin-1 抓不到。
    probe = text.strip()[:400]
    for true_enc, wrong_enc in MISDECODE_PAIRS:
        try:
            fixed = probe.encode(wrong_enc, "ignore").decode(true_enc, "ignore")
        except (UnicodeError, LookupError):
            continue
        if any(m in fixed for m in PLACEHOLDER_MARKERS):
            return f"mojibake:{true_enc}-as-{wrong_enc}"
    return None


def is_broken_lrc(path: str) -> bool:
    try:
        text, _ = read_text(path)
        if looks_broken(text):
            return True
        return not text.strip()
    except Exception:
        return True


def parse_name(path: str) -> tuple[str | None, str | None]:
    """从文件名兜底解析 (artist, title)。"""
    base = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"^\s*\d{1,3}\s*[-.、]\s*(.+)$", base)
    stem = m.group(1) if m else base
    if " - " in stem:
        a, _, b = stem.partition(" - ")
        return a.strip() or None, b.strip() or None
    return None, stem.strip() or None


# mutagen 对不同容器暴露的标签键**完全不一样**：
#   Vorbis（FLAC / OGG）→ title / artist / album（小写）
#   ID3（MP3 / WAV）    → TIT2 / TPE1 / TALB
#   MP4（M4A）          → ©nam / ©ART / ©alb
# 只按友好名查会**静默失败** —— 表现是 MP3/M4A 的标签全都读不出来、一路退到文件名兜底，
# 匹配准确率白掉一截。实测踩过：带 ID3 标签的 WAV 读出来 title=None。
TAG_ALIASES = {
    "title": ("title", "tit2", "©nam", "tt2", "inam"),
    "artist": ("artist", "tpe1", "©art", "tp1", "iart", "aart", "tpe2",
               "albumartist", "album_artist"),
    "album": ("album", "talb", "©alb", "tal", "ipal"),
    "language": ("language", "tlan", "©lan", "tla", "ilng"),
    # 注意别写成裸的 "txxx"：TXXX 是用户自定义帧，任何 TXXX 都会被误判成纯音乐
    "instrumental": ("instrumental", "txxx:instrumental", "----:com.apple.itunes:instrumental"),
    "lyrics": ("lyrics", "unsyncedlyrics", "syncedlyrics", "uslt", "©lyr", "ult"),
}


def _normalize_tags(raw) -> dict:
    """标签字典 → 小写键；带描述后缀的帧（`TXXX:instrumental`）也能被前缀命中。"""
    out: dict = {}
    for k, v in (raw or {}).items():
        if v is None:
            continue
        key = str(k).lower()
        out.setdefault(key, v)
        if ":" in key:
            out.setdefault(key.split(":", 1)[0], v)
    return out


def _as_text(v) -> str | None:
    """标签值可能是 str / bytes / 单元素列表，统一成字符串。"""
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, bytes):
        for enc in ("utf-8", "utf-16", "latin-1"):
            try:
                v = v.decode(enc).strip("\x00").strip()
                break
            except Exception:
                continue
        else:
            return None
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def read_tags(path: str) -> dict:
    out = {"title": None, "artist": None, "album": None, "language": None,
           "duration": None, "instrumental": False, "embedded_lyrics": None}
    try:
        f = MutaFile(path)
        if f is not None:
            out["duration"] = getattr(f.info, "length", None)
            tags = _normalize_tags(f.tags)

            def first(*keys):
                for k in keys:
                    txt = _as_text(tags.get(k))
                    if txt:
                        return txt
                return None

            out["title"] = first(*TAG_ALIASES["title"])
            out["artist"] = first(*TAG_ALIASES["artist"])
            out["album"] = first(*TAG_ALIASES["album"])
            out["language"] = (first(*TAG_ALIASES["language"]) or "").lower() or None
            if first(*TAG_ALIASES["instrumental"]):
                out["instrumental"] = True
            found = first(*TAG_ALIASES["lyrics"])
            if found:
                out["embedded_lyrics"] = found
    except Exception:
        pass

    if not out["title"]:
        a, t = parse_name(path)
        out["artist"] = out["artist"] or a
        out["title"] = t
    if not out["artist"]:
        folder = os.path.basename(os.path.dirname(path))
        if folder and folder not in ("音乐", "中文", "日语", "英语", "小语种", "纯音乐"):
            out["artist"] = folder
    return out


def expected_language(tags: dict, top: str) -> tuple[str | None, str | None]:
    """→ (预期脚本语言, 来源)。来源 `tag` = language 标签，`dir` = 目录名猜测。

    来源必须能分辨。注意：标签值不在 LANG_MAP 内时（如 nob/tha）会落到目录推断，
    所以「标签非空」不能当作判据 —— 必须看这个返回值。
    """
    code = (tags.get("language") or "").strip().lower()
    if code in LANG_MAP:
        return LANG_MAP[code], "tag"
    hint = DIR_HINT.get(top)
    return (hint, "dir") if hint else (None, None)


def iter_audio(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1].lower() in AUDIO_EXT:
                yield os.path.join(dirpath, fn)


def load_song(path: str, root: str) -> Song:
    tags = read_tags(path)
    rel = os.path.relpath(path, root)
    top = rel.split(os.sep)[0] if os.sep in rel else "(root)"
    expected_lang, expected_src = expected_language(tags, top)
    return Song(
        path=path,
        lrc_path=os.path.splitext(path)[0] + ".lrc",
        rel=rel,
        title=tags.get("title") or os.path.splitext(os.path.basename(path))[0],
        artist=tags.get("artist") or "",
        album=tags.get("album") or "",
        language=tags.get("language"),
        duration=tags.get("duration"),
        instrumental=bool(tags.get("instrumental")),
        embedded_lyrics=tags.get("embedded_lyrics"),
        expected_lang=expected_lang,
        expected_lang_source=expected_src,
    )


def scan(root: str, limit: int | None = None) -> list[Song]:
    """遍历库。`limit` 是**遍历上限**（走满就停，用于快速预览）。

    **不做路径过滤** —— 要按路径筛，用 `select()`，那里把顺序定死了。
    """
    songs = []
    for p in iter_audio(root):
        songs.append(load_song(p, root))
        if limit and len(songs) >= limit:
            break
    return songs


def select(root: str, *, only: str | None = None, limit: int | None = None) -> list[Song]:
    """按路径片段筛选，**然后**再截断到 `limit` 首。

    **顺序不能反。** 实测踩到过：`scan(limit=3)` 先按遍历顺序截了前 3 个文件，
    再拿 `--only 英语` 去过滤 —— 前 3 个文件根本不在 `英语/` 下，于是空选、
    打印「没有要处理的歌」、退出码 1。单独用两个参数都正常，合起来才坏。

    筛选在**加载标签之前**做（`rel` 只是 `os.path.relpath`，不用读文件），
    所以 `limit` 依然省时间 —— 不能为了改对而把大库拖慢。

    **这条顺序只在这里写一次**：CLI 与网页版都调它，不各自重写一遍。
    """
    if not only:
        return scan(root, limit=limit)
    needle = only.lower()
    keep: list[str] = []
    for p in iter_audio(root):
        if needle in os.path.relpath(p, root).lower():
            keep.append(p)
            if limit and len(keep) >= limit:
                break
    return [load_song(p, root) for p in keep]


def summarize(songs: list[Song]) -> dict:
    """给 UI 用的库概览。"""
    from . import lrc as L

    by_top: dict[str, dict] = {}
    broken = bilingual = mono = missing = 0
    for s in songs:
        d = by_top.setdefault(s.top, {"audio": 0, "lrc": 0, "broken": 0, "bilingual": 0, "mono": 0, "none": 0})
        d["audio"] += 1
        if not s.has_lrc:
            d["none"] += 1
            missing += 1
            continue
        d["lrc"] += 1
        if is_broken_lrc(s.lrc_path):
            d["broken"] += 1
            broken += 1
            continue
        try:
            text, _ = read_text(s.lrc_path)
            v = L.validate(text)
        except Exception:
            broken += 1
            d["broken"] += 1
            continue
        if v["ok"]:
            d["bilingual"] += 1
            bilingual += 1
        else:
            d["mono"] += 1
            mono += 1
    return {
        "total": len(songs),
        "bilingual": bilingual,
        "mono_or_single": mono,
        "missing": missing,
        "broken": broken,
        "by_top": by_top,
        "instrumental": sum(1 for s in songs if s.expected_lang == "instrumental"),
    }
