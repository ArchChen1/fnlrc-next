"""歌词数据源 + 翻译后端。

降级链：
  A. 网易云  原词 + 中文翻译(tlyric) + 罗马音
  B. LRCLIB  时间轴原词
  C. 用户自己的 OpenAI 兼容端点（目前只做 DeepSeek）补翻源站没给译文的那些行
"""
import difflib
import re
import time
import unicodedata
from collections import defaultdict
from urllib.parse import urlsplit, urlunsplit

import requests

from .. import library as lib
from .. import lrc as L

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Referer": "https://music.163.com/",
}
LRCLIB_UA = {"User-Agent": "HermesLyricsPipeline/1.0 (personal music library)"}
TIMEOUT = 20            # 网易云（实测 0.2-0.3s 很快）
LRCLIB_TIMEOUT = 8      # LRCLIB 偶发 20-35s 慢响应 → 快速失败，别拖垮整轮


# ---------------------------------------------------------------- 凭据脱敏
# 日志、异常说明、报告字段统一走这里。key 会出现在三个地方：服务端返回体、
# 异常字符串、以及用户自己填进 base_url 的查询串（很多网关接受 ?api_key=）。
_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(bearer)\s+\S+"), r"\1 ***"),
    (re.compile(r"\bsk-[A-Za-z0-9._\-]{4,}"), "sk-***"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|key)=([^&\s\"']+)"),
     r"\1=***"),
)


def mask_key(key: str | None) -> str:
    """把一把 key 显示成 sk-abcd***wxyz，供 UI 回显「已配置」状态。绝不返回完整值。

    刻意只用 ASCII —— 这个字符串会进 Windows 控制台（GBK）与日志文件。
    """
    if not key:
        return ""
    if len(key) <= 10:
        return "*" * len(key)
    return f"{key[:6]}***{key[-4:]}"


def mask_secret(text: object, max_len: int = 300) -> str:
    """清洗任意文本里的凭据并截断。异常信息、HTTP 响应体、报告 note 都要过这里。"""
    s = "" if text is None else str(text)
    for pat, repl in _SECRET_PATTERNS:
        s = pat.sub(repl, s)
    s = s.strip()
    return s if len(s) <= max_len else s[:max_len] + "..."


def redact_url(url: str) -> str:
    """去掉 URL 里的查询串与 userinfo —— base_url 是用户可填的，可能被塞进 ?api_key=。"""
    if not url:
        return ""
    try:
        p = urlsplit(url)
    except ValueError:
        return mask_secret(url)
    if not p.query and "@" not in p.netloc:
        return url
    host = p.netloc.rsplit("@", 1)[-1]
    return urlunsplit((p.scheme, host, p.path, "", ""))



_last_call = {"netease": 0.0, "lrclib": 0.0}
_MIN_INTERVAL = {"netease": 0.35, "lrclib": 1.2}   # 限流：LRCLIB 对高频很敏感


def _throttle(kind: str) -> None:
    """全局节流：保证同类请求之间的最小间隔（批量跑时避免被 429 拖死）。"""
    wait = _MIN_INTERVAL[kind] - (time.time() - _last_call[kind])
    if wait > 0:
        time.sleep(wait)
    _last_call[kind] = time.time()


# 给插件用的公开别名：插件**不应**直接碰 `_throttle` / `_req` 这类私有名，
# 用公开名可以让内部实现随意重构。二者都保留原名（既有一堆调用与打桩点依赖它们）。
throttle = _throttle


def _register_source_interval(kind: str, interval: float) -> None:
    """插件声明自己的节流间隔。缺省时新源用 1.0s（保守，先别把源站惹毛）。"""
    _MIN_INTERVAL.setdefault(kind, interval)
    _last_call.setdefault(kind, 0.0)


# 给插件用的公开别名（见 throttle 处的说明）。
register_source_interval = _register_source_interval


def _req(method: str, url: str, *, retries: int = 3, kind: str | None = None,
         deadline: float | None = None, **kw):
    """带指数退避 + 节流 + 总时限的请求。放弃时返回 None（由调用方决定降级）。"""
    if kind:
        _throttle(kind)
    for attempt in range(retries):
        if deadline is not None and time.time() > deadline:
            return None
        try:
            r = requests.request(method, url, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2.0 * (attempt + 1) ** 2)   # 429 退避更狠
                continue
            return r
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return None


# 给插件用的公开别名（见 throttle 的说明）。
req = _req


# ---------------------------------------------------------------- 工具
# 归一化时要丢掉的东西。除常规标点外必须包含 #♯＃ —— 实测同一首歌在库里写作
# `心拍数＃0822`（全角）、网易云写作 `心拍数#0822` 与 `心拍数♯0822`，不统一就
# 会被当成三个不同的标题（`♯` 的 NFKC 分解是它自己，指望不上）。
_PUNCT_RE = re.compile(r"[\s\-_·・'\"()（）\[\]【】,，.。!！?？:：;；/\\#♯＃]+")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return _PUNCT_RE.sub("", s)


def sim(a: str, b: str) -> float:
    """通用相似度：包含关系 + 编辑距离。用于艺人名等不做版本区分的字段。"""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        return 0.75 + 0.25 * (len(short) / len(long_))
    return difflib.SequenceMatcher(None, a, b).ratio()


def title_sim(query: str, cand: str) -> float:
    """标题相似度。与 sim() 有两处刻意的不同，都是实测踩出来的：

    1. 先比 `base_title()` —— `群青` 对 `群青 (Album Ver.)` 本来就是同一个标题，
       直接比归一化原串会被括号内容拉低。
    2. 归一化后短于 4 个字符时必须完全相等 —— 3 个字符以内的"高重合率"基本都是
       巧合（`爱` ⊂ `爱如潮水`）。**只对标题生效，不对艺人**：艺人名本来就短
       （`H△G` 归一化后 3 字符，网易云还会给 `/` 拼接串），套这条会把 2.0 的
       艺人项直接归零，候选跌到 4.0 门槛以下变成 no_lyrics。
    """
    q, c = norm(lib.base_title(query)), norm(lib.base_title(cand))
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        short, long_ = (q, c) if len(q) <= len(c) else (c, q)
        if len(short) < 4:
            return 0.0
        return 0.75 + 0.25 * (len(short) / len(long_))
    if len(q) < 4 or len(c) < 4:
        return 0.0
    return difflib.SequenceMatcher(None, q, c).ratio()


def version_penalty(query: str, cand: str) -> float:
    """候选带了原曲名没有的版本标记 → 扣分（Live / 翻唱 / 伴奏 / 重混）。

    ⚠️ 必须扫**原始标题**，不能先过 `base_title()` —— 版本标记（`(Live)`、
    `(live version)`）本身就写在噪声清单里，先剥再找等于永远找不到，降权会静默失效。
    实测踩过：`告白の夜 (live version)` 因此拿到 4.0 以上、被当成匹配结果写进报告。

    只在"单边有标记"时扣：原曲名自己就带 `live` 时（库里存的就是 Live 版），
    或者两边带同一个标记时，不扣。
    """
    qm = {m.group(0).lower() for m in lib.VERSION_MARKERS.finditer(query or "")}
    cm = {m.group(0).lower() for m in lib.VERSION_MARKERS.finditer(cand or "")}
    return -2.5 if (cm - qm) else 0.0


DURATION_TOLERANCE = 10.0   # 秒


def duration_ok(cand_dur_ms, duration: float | None,
                tol: float = DURATION_TOLERANCE) -> bool:
    """时长硬闸：两侧都有时长且差超过容差 → 直接淘汰候选。

    Live 版、加长版与录音室版的时长差普遍远超 10s，只扣分挡不住它们
    （原来 >8s 仅 −1.5，标题分够高照样胜出）。任一侧缺时长时不做判断 ——
    宁可选错也别漏选。
    """
    if not cand_dur_ms or not duration:
        return True
    return abs(cand_dur_ms / 1000 - duration) <= tol


def _score(cand_title, cand_artists, cand_dur_ms, title, artist, duration):
    s = 0.0
    s += 3.0 * title_sim(title, cand_title)
    s += 2.0 * sim(cand_artists, artist)
    if cand_dur_ms and duration:
        d = abs(cand_dur_ms / 1000 - duration)
        s += 3.0 if d <= 3 else (1.5 if d <= 8 else -1.5)
    s += version_penalty(title, cand_title)
    return s


# 给插件用的公开别名（见 _throttle 处的说明）。
score = _score


# 语言一致性的加权值。**只加权，绝不淘汰候选** ——
# `docs/TECH-NOTES.md:184-186` 与 `docs/TESTING.md:76` 明确要求：标签/目录推断
# 本身就会错（实测「英语/」目录里躺着日语歌），因为标签错就把正确匹配拦掉是
# 反向错误。唯一的硬闸仍然只有「外文曲目 + 歌词是中文」那一条（见 pipeline）。
LANG_BONUS = 1.5



# ---------------------------------------------------------------- 网易云
# 搜索路由。`/api/search/get/web` 在 2026-09 实测已稳定返回
# `{"code":405,"msg":"操作频繁，请稍候再试"}`（HTTP 200 但 songs 为空），
# 静置后复测仍如此，而同一秒内 `/api/search/get` 正常返回 —— 是路由被停用，
# 不是限流。保留旧路由作兜底，主路由换成仍然可用的那个。
NE_SEARCH_URLS = (
    "https://music.163.com/api/search/get",
    "https://music.163.com/api/search/get/web",
)


def _ne_search(q: str, limit: int = 8, deadline: float | None = None):
    """按顺序尝试可用的搜索路由，第一个返回结果的即采用。"""
    for url in NE_SEARCH_URLS:
        r = _req("POST", url, retries=2, kind="netease",
                 deadline=deadline, data={"s": q, "type": 1, "offset": 0, "limit": limit},
                 headers=UA, timeout=TIMEOUT)
        if r is None or r.status_code != 200:
            continue
        try:
            d = r.json()
        except Exception:
            continue
        songs = ((d.get("result") or {}).get("songs")) or []
        if songs:
            return songs
    return []


def lang_ok(detected: str, expected: str | None) -> bool:
    """歌词脚本语言是否符合预期（expected 为 None 时不判断）。"""
    if not expected or not detected or detected in ("unknown", "other"):
        return True
    return detected == expected


# ---------------------------------------------------------------- 行角色
# 每一行都要能回答"它是原文、译文，还是罗马音"。用户要的是**只显示原文 + 译文**，
# 所以罗马音必须被识别出来并单独放，而不是混进译文行里。

def split_orig_roles(entries: list[tuple[int, str]]) -> tuple[list, list]:
    """把「原词」拆成 (真正的原文行, 混进来的罗马音行)。

    网易云的部分上传把原文与罗马音混排在 `lrc` 字段里（这正是它另设 `romalrc`
    的原因，但不是每个上传都规整）。判据刻意保守 —— 只有确认是**成对出现**的
    罗马音才拆，宁可漏拆也不要把正经歌词行扔掉。
    """
    if not entries:
        return [], []
    main = L.detect_lang(" ".join(t for _, t in entries))
    if main in ("latin", "unknown", "other"):
        return entries, []          # 整首本来就是拉丁 / 判不出来 → 没有罗马音可言
    latin_idx = {i for i, (_, t) in enumerate(entries) if L.detect_lang(t) == "latin"}
    if not latin_idx or len(latin_idx) / len(entries) < 0.15:
        return entries, []          # 零星英文短语（副歌里的 "yeah"）不算罗马音
    others = [ms for i, (ms, _) in enumerate(entries) if i not in latin_idx]
    # 强信号：罗马音与原文**对齐**（同一个或 ±300ms 内的时间戳）
    near = sum(1 for i in latin_idx if any(abs(entries[i][0] - m) <= 300 for m in others))
    if near / len(latin_idx) >= 0.6:
        return ([e for i, e in enumerate(entries) if i not in latin_idx],
                [entries[i] for i in sorted(latin_idx)])
    # 弱信号：**交替出现**（原文一行、罗马音一行，时间戳顺序递增）。要求至少 2 行 ——
    # 单个孤立的拉丁行多半是副歌里的 "Yeah"，不是罗马音。
    alt = sum(1 for i in latin_idx
              if (i > 0 and (i - 1) not in latin_idx)
              or (i + 1 < len(entries) and (i + 1) not in latin_idx))
    if len(latin_idx) >= 2 and alt / len(latin_idx) >= 0.6:
        return ([e for i, e in enumerate(entries) if i not in latin_idx],
                [entries[i] for i in sorted(latin_idx)])
    return entries, []              # 证据不足 → 不动，宁可漏拆


def align_translation(
    local: list[tuple[int, str]],
    fetched: list[tuple[int, str, str | None, str | None]],
    *, tol_ms: int = 900, min_text_sim: float = 0.62,
) -> tuple[list[tuple[int, str, str | None]], int]:
    """把源站抓来的译文对到**用户本地已有的时间轴**上。

    ```text
    local   = [(ms, 本地原文)]              用户自己的 .lrc，时间轴与断句要原样保留
    fetched = [(ms, 原文, 译文, 罗马音)]     源站抓到的
    返回    = ([(本地 ms, 本地原文, 译文 | None)], 有译文的行数)
    ```

    **为什么不能按顺序一一对应**：两边的断句常常不同 —— 本地可能把两句并在一行，
    源站可能拆成两行；反过来也一样。行数对不上时，按顺序配会把译文整段错位，
    错位后的双语比单语更难看。所以先按时间戳就近配，再按文本相似度兜底。

    有译文的候选优先：同一时刻附近常常同时存在原文行和罗马音行，罗马音那行没有
    译文，优先挑有译文的才能把覆盖拉满。

    `min_text_sim` 刻意偏高 —— 文本兜底只在时间戳完全对不上时才用，配错的代价
    比少配一行大得多。
    """
    if not local or not fetched:
        return [(ms, t, None) for ms, t in local], 0

    used = [False] * len(fetched)
    out: list[tuple[int, str, str | None]] = []
    hits = 0
    for ms, text in local:
        nt = norm(text)
        best_i, best_key = None, None
        for i, (fms, fo, ft, _) in enumerate(fetched):
            if used[i]:
                continue
            d = abs(fms - ms)
            if d <= tol_ms:
                # (0) 时间戳配上了，再按"有没有译文"和远近排
                key = (0, 0 if ft else 1, d)
            else:
                if not nt or not fo:
                    continue
                s = sim(nt, norm(fo))
                if s < min_text_sim:
                    continue
                key = (1, 0 if ft else 1, -s)
            if best_key is None or key < best_key:
                best_i, best_key = i, key
                if key == (0, 0, 0):
                    break               # 同时间戳且有译文，不用再找了
        if best_i is None:
            out.append((ms, text, None))
            continue
        used[best_i] = True
        tr = fetched[best_i][2]
        out.append((ms, text, tr))
        if tr:
            hits += 1
    return out, hits


def split_orig_with_roman(entries: list[tuple[int, str]]) -> list[tuple[int, str, str | None]]:
    """→ [(ms, 原文, 罗马音 | None)]，把 `lrc` 里混排的罗马音**挂回它对应的原文行**。

    两种混排形态要用不同的挂法：

    · 时间戳对齐（同 ms 或 ±300ms）—— 按时间挂。
    · 交替出现（原文一行、罗马音一行）—— 按**文件顺序**挂到紧邻的前一行。
      不能按时间挂：那种写法的时间戳是顺序递增的，前后可能差上千毫秒，
      只靠时间会把罗马音丢掉（实测踩过，罗马音在 orig 里被摘走、却没挂回来）。
    """
    orig_entries, roman_entries = split_orig_roles(entries)
    if not roman_entries:
        return [(ms, t, None) for ms, t in entries]

    orig_ms = {ms for ms, _ in orig_entries}
    by_ms: dict[int, str] = {}
    for ms, t in roman_entries:
        if ms in orig_ms:
            hit = ms
        else:
            near = [m for m in orig_ms if abs(m - ms) <= 300]
            hit = min(near, key=lambda m: abs(m - ms)) if near else None
        if hit is not None:
            by_ms.setdefault(hit, t)

    latin_idx = {i for i, (_, t) in enumerate(entries) if L.detect_lang(t) == "latin"}
    rows: list[tuple[int, str, str | None]] = []
    for i, (ms, t) in enumerate(entries):
        if i in latin_idx:
            continue                      # 罗马音行本身不进原文列表
        roman = by_ms.get(ms)
        if roman is None and (i + 1) in latin_idx:
            roman = entries[i + 1][1]
        rows.append((ms, t, roman))
    return rows


def translation_role(text: str, orig_lang: str) -> str:
    """判断一段"译文"到底是真译文，还是原文的罗马音。"""
    tl = L.detect_lang(text)
    if tl == "zh":
        return "trans"
    if orig_lang != "latin" and tl == "latin":
        # 原文非拉丁、候选是纯拉丁 → 基本就是原文的音译。注意罗马音与假名/汉字
        # **没有共同字符**，靠字符重合判断不了，只能靠脚本类型。
        return "roman"
    return "trans"


def pick_translation(cands: list[str], orig_text: str) -> str | None:
    """同一时间戳有多个译文候选时择一。

    排序依据（按优先级）：
      1. **先剔掉与原文完全相同的候选** —— 有些上传会把原文再抄一遍，那不是译文。
         不剔的话，英文歌会因为"英文更长"而选中原文自己。
      2. 优先真译文而不是罗马音（见 `translation_role`）。
      3. 与原文同语种的候选往后排。
    """
    if not cands:
        return None
    orig_n = norm(orig_text)
    pool = [c for c in cands if norm(c) != orig_n]
    if not pool:
        return None                 # 全部是原文的副本 → 这首其实没有译文
    if len(pool) == 1:
        return pool[0]
    orig_lang = L.detect_lang(orig_text)

    def key(t):
        role = translation_role(t, orig_lang)
        same_lang = 1 if L.detect_lang(t) == orig_lang else 0
        return (0 if role == "trans" else 1, same_lang, -len(t))

    return sorted(pool, key=key)[0]


def _ne_lyric(song_id: int, deadline: float | None = None) -> dict:
    """→ {"lrc", "tlyric", "romalrc", "nolyric"}；任何失败都返回空，由调用方降级。

    之前这里不收 `deadline`，单曲 25s 总时限在取词环节是失效的（最坏 3 候选 ×
    3 次重试 × 20s）。`nolyric` 是网易云对「这首歌没有歌词」的显式标记，
    比靠文件名猜纯音乐可靠。
    """
    empty = {"lrc": "", "tlyric": "", "romalrc": "", "nolyric": False}
    r = _req("GET", "https://music.163.com/api/song/lyric", kind="netease",
             deadline=deadline,
             params={"id": song_id, "lv": 1, "kv": 1, "tv": 1, "rv": 1},
             headers=UA, timeout=TIMEOUT)
    if r is None or r.status_code != 200:
        return empty
    lr = r.json()
    return {
        "lrc": (lr.get("lrc") or {}).get("lyric") or "",
        "tlyric": (lr.get("tlyric") or {}).get("lyric") or "",
        "romalrc": (lr.get("romalrc") or {}).get("lyric") or "",
        "nolyric": bool(lr.get("nolyric")),
    }


def netease_fetch(title: str, artist: str, duration: float | None,
                  expected: str | None = None, deadline: float | None = None,
                  debug=False) -> dict | None:
    """按打分取候选。

    语言一致性**只加权**（LANG_BONUS），不做硬过滤 —— 依据见 LANG_BONUS 的注释。
    时长差超过 DURATION_TOLERANCE 的候选在**收集阶段**就淘汰：这样后面那个
    「够好就停止后续查询」的提前 break 不会被一个注定被淘汰的候选抬高分数而误触发。

    返回的 dict 额外带 `candidates`（最多 5 个候选的池子），供 UI 手动换版本。
    """
    queries = [f"{title} {artist}".strip(), title.strip(), f"{artist} {title}".strip()]
    base = lib.base_title(title)
    if base and norm(base) != norm(title):
        queries.append(f"{base} {artist}".strip() if artist else base)

    cands, seen, dropped = [], set(), 0
    for q in queries:
        if not q:
            continue
        try:
            songs = _ne_search(q, deadline=deadline)
        except Exception as e:
            if debug:
                print("   ne search err", mask_secret(e))
            continue
        for s in songs:
            sid = s.get("id")
            if sid in seen:
                continue
            seen.add(sid)
            artists = "/".join(a.get("name", "") for a in s.get("artists", []) or [])
            dur_ms = s.get("duration") or s.get("dt") or 0
            if not duration_ok(dur_ms, duration):
                dropped += 1
                if debug:
                    print(f"   ne 淘汰(时长不符) {s.get('name')} / {artists} "
                          f"{dur_ms / 1000:.0f}s vs {duration:.0f}s")
                continue
            sc = _score(s.get("name", ""), artists, dur_ms, title, artist, duration)
            cands.append((sc, s, artists))
        if cands and max(c[0] for c in cands) >= 6.5:
            break
    cands.sort(key=lambda x: -x[0])
    if debug and dropped:
        print(f"   ne 因时长淘汰 {dropped} 个候选")

    best, pool = None, []
    for sc, s, artists in cands[:5]:
        if sc < 4.0:
            break
        try:
            lyr = _ne_lyric(s["id"], deadline=deadline)
        except Exception as e:
            if debug:
                print("   ne lyric err", mask_secret(e))
            continue
        if lyr["nolyric"]:
            if debug:
                print(f"   ne 候选 {s.get('name')} 标记为无歌词 (nolyric)")
            continue
        orig_all = L.parse_lrc(lyr["lrc"])
        if not orig_all:
            continue
        # 先把混排的罗马音摘出去、并挂回对应的原文行，再判语言 ——
        # 否则罗马音版上传会被判成 latin，语言一致性加分永远拿不到
        # （这是 JP/CN 素材里最常见的一种误判）。
        rows = split_orig_with_roman(orig_all)
        det = L.detect_lang(" ".join(o for _, o, _ in rows))
        eff = sc + (LANG_BONUS if lang_ok(det, expected) else 0.0)
        info = {"source": "netease", "song_id": s["id"],
                "matched": f"{s.get('name')} / {artists}", "score": round(sc, 2),
                "lyric_lang": det, "lang_mismatch": not lang_ok(det, expected)}
        pool.append({"song_id": s["id"], "matched": info["matched"], "score": round(sc, 2),
                     "lyric_lang": det, "lines": len(rows), "effective": round(eff, 2)})
        if best is None or eff > best[0]:
            best = (eff, sc, s, artists, lyr, rows, info)
        if lang_ok(det, expected) and sc >= 6.5:
            break
        if debug:
            print(f"   ne 候选 {s.get('name')} lang={det} expect={expected} score={sc}")
    if not best:
        return None
    _, sc, s, artists, lyr, rows, info = best
    res = _ne_pack(sc, s, artists, lyr, rows, info)
    res["candidates"] = sorted(pool, key=lambda c: -c["effective"])
    return res


def _ne_pack(sc, s, artists, lyr, rows, info):
    """→ info，其中 `pairs` 是 [(ms, 原词, 译文, 罗马音)]。

    行角色在这里定型：`rows` 已经带上了从 `lrc` 里拆出来的罗马音，
    `romalrc` 是源站的罗马音字段，`tlyric` 里若混着拉丁文本则按 roman 处理 ——
    这样输出永远只有「原文 + 译文」两行。
    """
    trans_by_ms = defaultdict(list)
    for ms, t in (L.parse_lrc(lyr["tlyric"], drop_credits=False) if lyr["tlyric"] else []):
        trans_by_ms[ms].append(t)
    roman_by_ms: dict[int, str] = {}
    for ms, t in (L.parse_lrc(lyr["romalrc"], drop_credits=False) if lyr["romalrc"] else []):
        roman_by_ms.setdefault(ms, t)

    pairs, alts = [], {}
    for ms, txt, roman_from_lrc in rows:
        cands = trans_by_ms.get(ms)
        if cands is None:
            near = [k for k in trans_by_ms if abs(k - ms) <= 300]
            if near:
                cands = trans_by_ms[min(near, key=lambda k: abs(k - ms))]
        cands = cands or []
        orig_lang = L.detect_lang(txt)
        trans = pick_translation(cands, txt)
        roman = roman_from_lrc or roman_by_ms.get(ms)
        if trans and translation_role(trans, orig_lang) == "roman":
            # 选中的"译文"其实是原文的音译 → 降级成 roman，不占用译文行
            roman = roman or trans
            trans = None
        if trans:
            others = [c for c in cands if c != trans]
            if others:
                alts[ms] = others
        pairs.append((ms, txt, trans, roman))
    info.update({"pairs": pairs, "alternatives": alts,
                 "has_translation": any(p[2] for p in pairs)})
    return info


# ---------------------------------------------------------------- LRCLIB
def lrclib_fetch(title: str, artist: str, album: str, duration: float | None,
                 expected: str | None = None, deadline: float | None = None, debug=False):
    """LRCLIB：优先语言匹配的候选（避免俄语歌匹配到中文翻唱版歌词）。

    时长不符的候选直接淘汰；每一轮查询前重查一次 deadline —— `_throttle` 的
    1.2s 等待在 `_req` 的时限检查之外，4 次请求叠加起来能超出单曲预算。
    """
    cands = []
    base = lib.base_title(title)
    try:
        r = _req("GET", "https://lrclib.net/api/get", retries=1, kind="lrclib", deadline=deadline,
                 params={"artist_name": artist or "", "track_name": title or "",
                         "album_name": album or "", "duration": int(duration or 0)},
                 headers=LRCLIB_UA, timeout=LRCLIB_TIMEOUT)
        if r is not None and r.status_code == 200:
            d = r.json()
            if d.get("syncedLyrics"):
                cands.append((9.9, d))
    except Exception as e:
        if debug:
            print("   lrclib get err", mask_secret(e))

    forms = [{"track_name": title, "artist_name": artist}, {"q": f"{title} {artist}"}, {"q": title}]
    if base and norm(base) != norm(title):
        forms.append({"q": base})
    for params in forms:
        if deadline is not None and time.time() > deadline:
            if debug:
                print("   lrclib 超出单曲时限，停止查询")
            break
        try:
            r = _req("GET", "https://lrclib.net/api/search", retries=1, kind="lrclib", deadline=deadline,
                     params=params, headers=LRCLIB_UA, timeout=LRCLIB_TIMEOUT)
            if r is None:
                break          # 被限流/网络失败：别再对同源连环重试
            if r.status_code != 200:
                continue
            for c in r.json() or []:
                if not c.get("syncedLyrics"):
                    continue
                dur_ms = (c.get("duration") or 0) * 1000
                if not duration_ok(dur_ms, duration):
                    if debug:
                        print(f"   lrclib 淘汰(时长不符) {c.get('trackName')} "
                              f"{(dur_ms / 1000) if dur_ms else 0:.0f}s vs {(duration or 0):.0f}s")
                    continue
                sc = _score(c.get("trackName", ""), c.get("artistName", ""),
                            dur_ms, title, artist, duration)
                if sc >= 4.0:
                    cands.append((sc, c))
        except Exception:
            continue
        if cands:
            break
    if not cands:
        if debug:
            print("   lrclib 无候选")
        return None
    cands.sort(key=lambda x: -x[0])
    best, pool = None, []
    for sc, c in cands[:6]:
        entries = L.parse_lrc(c["syncedLyrics"])
        if not entries:
            continue
        det = L.detect_lang(" ".join(t for _, t in entries))
        eff = sc + (LANG_BONUS if lang_ok(det, expected) else 0.0)
        res = {"source": "lrclib", "entries": entries,
               "instrumental": bool(c.get("instrumental")),
               "matched": f"{c.get('artistName')} - {c.get('trackName')}",
               "score": round(sc, 2), "lyric_lang": det,
               "lang_mismatch": not lang_ok(det, expected)}
        pool.append({"matched": res["matched"], "score": round(sc, 2),
                     "lyric_lang": det, "lines": len(entries), "effective": round(eff, 2)})
        if debug:
            print(f"   lrclib 候选 {res['matched']} lang={det} score={sc} eff={eff:.2f}")
        if best is None or eff > best[0]:
            best = (eff, res)
    if not best:
        return None
    best[1]["candidates"] = sorted(pool, key=lambda c: -c["effective"])
    return best[1]


# ---------------------------------------------------------------- 翻译公共部分
def _remaining(deadline: float | None, default: float, floor: float = 5.0) -> float:
    """把「还剩多少秒」折进单次请求超时，避免一次调用吃掉整个预算。"""
    if deadline is None:
        return default
    return max(floor, min(default, deadline - time.time()))


BATCH_PROMPT = """你是专业的歌词翻译，把下面每一行歌词翻译成简体中文。
{title}{artist}{glossary}
规则：
1. 先把整段当成连贯的歌词语境来理解，再逐行输出。输入有多少行，输出就必须有多少行，编号一一对应，不要合并或拆分。
2. 连续几行可能是同一句话（比如上一行以 but / and / that 结尾，下一行接着说）：请按整句理解，再把译文拆回对应的每一行，保证每行都有内容、句意连贯。
3. 只输出 "编号|译文"，不要输出原文、解释或任何多余的内容。
4. 不要增译：译文里不能出现原文没有的词句。宁可简短，也不许自己加戏。
5. 拟声词、语气词、无实义的重复（Uh、uh huh、na na、yeah、oh 等）保留原文或音译，不要硬翻成中文。
6. 译文要符合歌词语气：口语、自然、简短，尽量能唱。人名/专有名词用常见译法。
{hook_rule}{context}输入：
{body}"""


def build_prompt(body: str, *, title: str = "", artist: str = "",
                 glossary: str | dict[str, str] | None = None,
                 context: str = "") -> str:
    """把 `BATCH_PROMPT` 里的占位符填上再发给模型。

    `{title}` / `{artist}` / `{glossary}` / `{context}` 都**缺省留空** —— 不给
    任何元数据时，就是一段纯规则提示词，与旧版行为一致，老调用方不受影响。
    """
    if isinstance(glossary, dict):
        glossary = "；".join(f"{k} → {v}" for k, v in glossary.items())
    title_line = f"歌曲：{title}\n" if title else ""
    artist_line = f"艺人：{artist}\n" if artist else ""
    glossary_line = f"术语表：{glossary}\n" if glossary else ""
    # 歌名 hook 规则只在知道歌名时才给 —— 副歌反复喊的那个词往往就是歌名的
    # 音译/谐音（APT. 的韩文写法 아파트 就是典型），直译会变成"公寓"这种怪结果。
    hook_rule = (
        f"7. 如果某一行是歌名的音译、谐音、罗马音或意译（副歌反复喊出的那个词往往"
        f"就是歌名本身），请直接采用歌名「{title}」的写法，不要按字面意译。\n"
    ) if title else ""
    context_block = (
        "整首歌的完整歌词如下，仅供你理解语境；要翻译的只是最后的输入：\n"
        + context + "\n\n") if context else ""
    return BATCH_PROMPT.format(title=title_line, artist=artist_line,
                               glossary=glossary_line, hook_rule=hook_rule,
                               context=context_block, body=body)


def _parse_numbered(text: str) -> dict[int, str]:
    out = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^\s*[*\-\s]*(\d{1,4})\s*[|｜:：.)、]\s*(.*?)\s*$", line)
        if m:
            txt = m.group(2).strip().strip("|｜")
            if txt:
                out[int(m.group(1))] = txt
    return out


# ---------------------------------------------------------------- 远端 OpenAI 兼容后端
# 状态码 → 人话。设置页的「测试连接」直接显示这些，不能只报一句"失败"。
REMOTE_ERR = {
    400: "请求被拒绝（模型名或参数不对）",
    401: "API key 无效或已过期",
    402: "账户余额不足",
    403: "无权访问该模型",
    404: "Base URL 或模型名不存在",
    429: "触发限流，请稍后重试",
}
# 这几类是凭据/配置错误，重试也没用，继续翻剩下的块只会白烧时间
FATAL_CODES = (400, 401, 402, 403, 404)
# 端点拒绝「未知字段」时常见的两个状态码
_EXTRA_REJECT = (400, 422)

# DeepSeek 的**思考模式默认是开的**（官方文档：`thinking.type` 默认 `enabled`，
# 默认强度 `high`）。对翻译这种任务它是纯负担：
#   · 慢、贵 —— 思考 token 照计费
#   · `max_tokens` 会被思考吃掉。默认不设 max_tokens 时思考模式给 64K，
#     我们设了 4096，一旦思考占满，正文就空 → `_parse_numbered` 拿不到 →
#     过不了 90% 门槛 → **整首降级为单语**
#   · 顺带一提，官方文档写明 `temperature` 在思考模式下**不生效**，
#     所以我们发的 0.1 也是空设
# 所以显式关掉它。
#
# 但这是 DeepSeek 专有字段：换成 one-api 代理 / vLLM / LM Studio 时，
# 严格校验的端点会用 400 拒掉未知字段。所以**只在识别到 DeepSeek 时才发**，
# 而且万一还是 400 了，会去掉它重试一次（见下面的 `_EXTRA_REJECT` 分支）。
_NO_THINK = {"thinking": {"type": "disabled"}}


def is_deepseek(base_url: str) -> bool:
    """base_url 是不是 DeepSeek 官方端点。用来决定要不要发它的专有字段。"""
    try:
        return "deepseek" in (urlsplit(base_url or "").hostname or "").lower()
    except ValueError:
        return False


def _remote_error(r) -> tuple[str, bool]:
    """把失败的响应翻成 (人话, 是否致命)。"""
    msg = REMOTE_ERR.get(r.status_code)
    if msg is None:
        msg = f"服务端返回 HTTP {r.status_code}"
        if r.status_code >= 500:
            msg += "（服务端错误，可稍后重试）"
    detail = ""
    try:
        b = r.json()
        detail = ((b.get("error") or {}).get("message")) or b.get("message") or ""
    except Exception:
        detail = r.text or ""
    detail = mask_secret(detail, 120)
    return (f"{msg}｜{detail}" if detail else msg, r.status_code in FATAL_CODES)


def remote_translate(lines: list[str], base_url: str, api_key: str, model: str,
                     chunk: int = 30, timeout: int = 60, deadline: float | None = None,
                     max_tokens: int = 4096, debug=False,
                     *, title: str = "", artist: str = "",
                     glossary: str | dict[str, str] | None = None,
                     context: str = "") -> tuple[dict[int, str], str | None]:
    """用任意 OpenAI 兼容端点翻译（DeepSeek 等）。

    → ({行号(1-based): 译文}, 错误说明或 None)。只要有任何一块成功，错误就是 None。

    与 `_req` 对齐补了指数退避 —— 原先一次 429 就丢掉整块。同时显式设 `max_tokens`：
    不设会走服务端默认值，长歌的译文被截断后 `_parse_numbered` 只拿到一部分，
    90% 门槛不过 → 整首降级为单语。
    """
    if not (base_url and model):
        return {}, "还没填 Base URL 或模型名"
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    out: dict[int, str] = {}
    first_err: str | None = None
    # 给 DeepSeek 关掉思考模式（见 `_NO_THINK` 的说明）；其它端点不发这个字段
    extra: dict = dict(_NO_THINK) if is_deepseek(base_url) else {}
    for start in range(0, len(lines), chunk):
        if deadline is not None and time.time() >= deadline:
            first_err = first_err or "翻译超出单曲时限（未翻完）"
            break
        part = lines[start:start + chunk]
        body = "\n".join(f"{i+1}|{t}" for i, t in enumerate(part))
        prompt = build_prompt(body, title=title, artist=artist,
                              glossary=glossary, context=context)
        payload = {"model": model,
                   "messages": [{"role": "user", "content": prompt}],
                   "temperature": 0.1, "stream": False, "max_tokens": max_tokens}
        block_err: tuple[str, bool] | None = None
        for attempt in range(3):
            if deadline is not None and time.time() >= deadline:
                break
            try:
                r = requests.post(url, json={**payload, **extra}, headers=headers,
                                  timeout=_remaining(deadline, timeout))
            except Exception as e:
                block_err = (mask_secret(
                    f"连不上 {redact_url(url)}：{type(e).__name__}: {e}"), False)
                time.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code == 200:
                try:
                    content = r.json()["choices"][0]["message"]["content"]
                except Exception as e:
                    block_err = (mask_secret(f"响应格式不对：{e}"), False)
                    break
                for i, t in _parse_numbered(content or "").items():
                    if 1 <= i <= len(part):
                        out[start + i] = t
                block_err = None
                break
            block_err = _remote_error(r)
            if extra and r.status_code in _EXTRA_REJECT:
                # 这个端点不认我们发的扩展字段。去掉重来一次 ——
                # 功能不受影响，只是那台服务器上关不掉思考模式。
                extra = {}
                continue
            if r.status_code not in FATAL_CODES:
                time.sleep(2.0 * (attempt + 1) ** 2)
                continue
            break
        if block_err:
            if first_err is None:
                first_err = block_err[0]
            if debug:
                print("   remote translate:", block_err[0])
            if block_err[1]:
                break
    return out, (None if out else first_err)


def remote_test(base_url: str, api_key: str, model: str, timeout: int = 20) -> tuple[bool, str]:
    """「测试连接」：发一条最短的请求验证 Base URL / key / 模型名。→ (是否可用, 人话)。

    `max_tokens` 给 512 而不是 64：万一对面是个关不掉思考模式的端点（非 DeepSeek），
    64 个 token 会被思考链吃光，正文返回空 —— 那会被误报成"连接有问题"。
    """
    got, err = remote_translate(["テスト"], base_url, api_key, model,
                                chunk=1, timeout=timeout, max_tokens=512)
    if got:
        return True, f"连接正常，{model} 返回了译文"
    if err:
        return False, err
    return False, ("模型返回了空内容。多半是模型名不对，或该模型的思考链吃光了输出预算"
                   "（DeepSeek 当前可用的是 deepseek-flash / deepseek-v4-pro）")


# ---------------------------------------------------------------- 源编排（M2）
def fetch_lyrics(song, cfg, *, deadline: float | None = None, log=None, errors=None):
    """统一降级链：按 priority 依次问每个可用源，第一个给出有用结果的即采用。

    与旧 `netease_fetch` / `lrclib_fetch` 的区别：它遍历**注册表**而不是写死两个源，
    所以新增一个源只要注册进来就自动进入降级链，`pipeline` 一行不用改。

    返回 `base.LyricResult` 或 None。**一个源取词抛异常会被吞掉并继续下一个源**
    （一个坏源/坏插件不能拖垮整首歌）。抛异常的源会以 `(源名, 脱敏后的原因)`
    追加进 `errors`（如果调用方传了列表）—— 这样「所有源都炸了」仍能被上层
    报成 error，而不是被误报成「没有歌词」。
    """
    from . import registry
    from .builtin import register_builtins

    register_builtins()          # 幂等：保证内置源始终在册（调用方忘了加载也不出错）

    enabled = {n for n in (getattr(cfg, "providers_enabled", None) or []) if n}
    disabled = {n for n in (getattr(cfg, "providers_disabled", None) or []) if n}

    for p in registry.all_providers():
        name = getattr(p, "name", "?")
        if name in disabled:
            continue
        if enabled and name not in enabled:
            continue
        try:
            ok, why = p.available()
        except Exception as e:
            msg = mask_secret(e)
            if errors is not None:
                errors.append((name, msg))
            if log:
                log(f"   源 {name} 自检异常：{msg}")
            continue
        if not ok:
            if log:
                log(f"   跳过源 {name}：{why}")
            continue
        try:
            res = p.fetch(song, cfg, deadline=deadline)
        except Exception as e:
            msg = mask_secret(e)
            if errors is not None:
                errors.append((name, msg))
            if log:
                log(f"   源 {name} 取词异常（继续下一个）：{msg}")
            continue
        if res and (res.pairs or getattr(res, "instrumental", False)):
            return res
    return None
