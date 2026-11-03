"""人工翻译：导出待译 → 人工填 → 导回（M3）。

机器翻译不可靠时，让用户（或字幕组）自己翻。本模块负责：

1. **导出**：把「缺译文的那几行」连同时间戳、序号、上下文写成 CSV / JSON；
2. **导回**：读回人工填的译文，走**同一套对齐逻辑**（`providers.align_translation`）
   合成为双语，**时间戳一个都不动**；
3. **记忆库**（默认关）：把「原文 → 译文」缓存起来，同语种→中文方向跨曲复用。

核心设计原则（对齐 `plan/03-manual-translation.md`）：

- 导出带 `index`（行序号）便于校验行数与防错位；
- 导回时**原文必须与当前抓取的一致**，不一致就报警要求重新导出（防止导出后
  源站数据变了导致译文错位）；
- CSV 用 `utf-8-sig` 写出（Excel 才认得中文）；JSON 保留完整结构给工具用；
- 「缺译文的行」的空译文一律用 `None` 而不是空串（与全站约定一致）。

本模块**零 UI 依赖**，只依赖 `library` / `lrc` / `providers`，CLI、Web、桌面共用。
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
from dataclasses import dataclass, field

from . import library as lib
from . import lrc as L
from . import providers as P

# ------------------------------------------------------------------ 数据结构
@dataclass
class Line:
    """一条待译（或已译）的行。"""
    index: int                 # 1 起，便于人类核对
    ms: int
    original: str
    translation: str | None = None   # None / 空 = 还没有译文

    def as_row(self, ctx_before: str = "", ctx_after: str = "") -> dict:
        return {
            "index": self.index,
            "time": L.fmt_ms(self.ms)[1:-1],     # 去掉方括号 → 00:12.34
            "original": self.original,
            "translation": self.translation or "",
            "context_before": ctx_before,
            "context_after": ctx_after,
        }


@dataclass
class SongLines:
    """一首歌的待译行。`rel` 是相对库根的路径（导回时按它匹配）。"""
    rel: str
    path: str = ""
    title: str = ""
    artist: str = ""
    expected_lang: str | None = None
    lines: list[Line] = field(default_factory=list)

    def as_dict(self, with_context: bool = False) -> dict:
        rows = []
        n = len(self.lines)
        for i, ln in enumerate(self.lines):
            before = self.lines[i - 1].original if (with_context and i > 0) else ""
            after = self.lines[i + 1].original if (with_context and i + 1 < n) else ""
            rows.append(ln.as_row(before, after))
        return {
            "file": _portable(self.rel),
            "title": self.title,
            "artist": self.artist,
            "expected_lang": self.expected_lang,
            "lines": rows,
        }


CSV_COLUMNS = ["file", "index", "time", "original", "translation",
               "context_before", "context_after"]


def _portable(rel: str) -> str:
    """把相对路径统一成 `/` 分隔 —— 导出文件要能在不同系统间搬运
    （Windows 导出的 CSV 拿到 Linux 上导回也要能对上）。"""
    return rel.replace("\\", "/")


# ------------------------------------------------------------------ 收集待译
def collect(songs: list[lib.Song], *, only_missing: bool = True,
            log=None) -> list[SongLines]:
    """为每首歌收集待译行。

    `only_missing=True`（默认）：只收**当前 .lrc 里已经是单语（外文原文）**的歌，
    且每行都待译 —— 因为这种歌正是"抓不到译文、只能人工补"的典型。
    `only_missing=False`：连**已有单语 .lrc** 的歌都收（换一种说法：它其实等价于
    过滤，因为已经有译文的双语文件本来就不该出现在人工待译清单里）。

    真正决定「收哪些行」的是 `manual_pairs`：从现有歌词里拆出原文行与已有译文，
    只把**没有译文**的行列为待译。
    """
    out: list[SongLines] = []
    for s in songs:
        if s.expected_lang == "instrumental" or s.instrumental:
            continue
        pairs, why = _existing_pairs(s)
        if pairs is None:
            continue
        missing = [(ms, o) for ms, o, tr, _ in pairs if not (tr or "").strip()]
        if not missing:
            continue
        sl = SongLines(rel=s.rel, path=s.path, title=s.title, artist=s.artist,
                       expected_lang=s.expected_lang)
        for i, (ms, o) in enumerate(missing, 1):
            sl.lines.append(Line(index=i, ms=ms, original=o))
        out.append(sl)
        if log:
            log(f"  {s.rel}：{len(missing)} 行待译（原 {len(pairs)} 行）")
    return out


def _existing_pairs(song: lib.Song):
    """从歌曲现有 .lrc 拆出 (ms, 原文, 译文, 罗马音) 列表。

    返回 `(pairs | None, 原因)`。没有 .lrc、或不是外文歌词时返回 None。

    单语文件：每行的译文为 None（全部待译）。
    双语文件：用行角色/交替规则把原文与译文分出来（`split_orig_roles` 只处理
    原文/罗马音；双语文件的判定用"同时间戳两行"更直接）。
    """
    if not song.has_lrc:
        return None, "没有 .lrc"
    try:
        text, _ = lib.read_text(song.lrc_path)
    except Exception as e:
        return None, f"读不了：{e}"
    entries = L.parse_lrc(text)
    if len(entries) < 2:
        return None, "行数太少"
    raw_lang = L.detect_lang(L.TS_RE.sub("", text))
    if raw_lang == "zh":
        return None, "中文歌词，无需人工翻译"

    # 同一时间戳出现两行 → 双语文件：第一行原文、第二行译文
    by_ms: dict[int, list[str]] = {}
    for ms, t in entries:
        by_ms.setdefault(ms, []).append(t)
    if any(len(v) >= 2 for v in by_ms.values()):
        pairs: list[tuple[int, str, str | None, str | None]] = []
        for ms in sorted(by_ms):
            vals = by_ms[ms]
            if len(vals) >= 2:
                pairs.append((ms, vals[0], vals[1], None))
            else:
                pairs.append((ms, vals[0], None, None))
        return pairs, None

    # 单语文件：用行角色规则把混进来的**罗马音**摘出去 —— 罗马音不是要翻的东西。
    orig_lines, _roman = P.split_orig_roles(entries)
    return [(ms, t, None, None) for ms, t in orig_lines], None


# ------------------------------------------------------------------ 导出
def export(songs: list[lib.Song], *, fmt: str = "csv", with_context: bool = False,
           only_missing: bool = True, root: str = "", log=None) -> str:
    """收集并序列化。返回**文件正文**（不落盘，由调用方决定写哪）。"""
    collected = collect(songs, only_missing=only_missing, log=log)
    if fmt == "json":
        payload = {
            "root": root,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "songs": [sl.as_dict(with_context=with_context) for sl in collected],
        }
        return json.dumps(payload, ensure_ascii=False, indent=1)

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for sl in collected:
        for i, ln in enumerate(sl.lines):
            n = len(sl.lines)
            before = sl.lines[i - 1].original if (with_context and i > 0) else ""
            after = sl.lines[i + 1].original if (with_context and i + 1 < n) else ""
            w.writerow({
                "file": _portable(sl.rel),
                "index": ln.index,
                "time": L.fmt_ms(ln.ms)[1:-1],
                "original": ln.original,
                "translation": "",
                "context_before": before,
                "context_after": after,
            })
    return buf.getvalue()


# ------------------------------------------------------------------ 解析导回
class ImportError_(ValueError):
    """导回时的校验错误。带 `problems` 列表（人话），调用方逐条展示。"""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("；".join(problems))


def parse(text: str, fmt: str = "csv") -> dict:
    """把导回文件正文解析成 {file: [ {index, original, translation}, ... ]}。

    只做结构解析，不做与源的比对（那是 `merge` 的事）。
    """
    if fmt == "json":
        try:
            d = json.loads(text)
        except Exception as e:
            raise ImportError_([f"JSON 解析失败：{e}"])
        out: dict[str, list[dict]] = {}
        for s in d.get("songs") or []:
            rel = s.get("file") or ""
            out[rel] = [{"index": ln.get("index"),
                         "original": ln.get("original", ""),
                         "translation": (ln.get("translation") or "").strip()}
                        for ln in s.get("lines") or []]
        return out

    out = {}
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ImportError_(["CSV 是空的"])
    missing = [c for c in ("file", "index", "original") if c not in reader.fieldnames]
    if missing:
        raise ImportError_([f"CSV 缺少必填列：{', '.join(missing)}"])
    for r in reader:
        rel = (r.get("file") or "").strip()
        if not rel:
            continue
        try:
            idx = int(r.get("index") or 0)
        except ValueError:
            raise ImportError_([f"index 不是数字：{r.get('index')!r}"])
        out.setdefault(rel, []).append({
            "index": idx,
            "original": r.get("original") or "",
            "translation": (r.get("translation") or "").strip(),
        })
    return out


# ------------------------------------------------------------------ 导回 + 合成
def merge(song: lib.Song, filled: list[dict]) -> tuple[str, list[str], int]:
    """把一个人工填好的行列表合到歌曲现有歌词上。

    → (合并后的正文, 问题列表, 写入的译文行数)。问题非空时为**拒绝对应行**，不是静默跳过。

    校验链（对齐 plan §四）：
    1. 行数与 index 连续、无重复；
    2. `original` 与现有歌词的原文一致（防错位）；
    3. 译文非空 → 参与合成；空行 → 保持原文单语。
    """
    problems: list[str] = []
    pairs, why = _existing_pairs(song)
    if pairs is None:
        return "", [why or "这首歌没有可对齐的歌词"], 0

    # 现有原文按文本索引（人工清单里没有 ms），同文本多行时按顺序各占一条
    orig_by_text: dict[str, list[int]] = {}
    for ms, o, _, _ in pairs:
        orig_by_text.setdefault(o, []).append(ms)

    filled_by_index: dict = {}
    for row in filled:
        idx = row.get("index")
        if idx in filled_by_index:
            problems.append(f"index={idx} 重复")
        filled_by_index[idx] = row
    idxs = sorted(k for k in filled_by_index if isinstance(k, int))
    if idxs and idxs != list(range(1, len(idxs) + 1)):
        problems.append(f"index 不连续：{idxs[:10]}{'…' if len(idxs) > 10 else ''}")
    if problems:
        return "", problems, 0

    trans_by_ms: dict[int, str] = {}
    for idx in idxs:
        row = filled_by_index[idx]
        orig = (row.get("original") or "").strip()
        trans = (row.get("translation") or "").strip()
        if not trans:
            continue                       # 空译文 → 保持单语
        mss = orig_by_text.get(orig)
        if not mss:
            problems.append(f"第 {idx} 行的原文与文件对不上（导出后可能源变了）：{orig[:20]}")
            continue
        trans_by_ms[mss.pop(0)] = trans
    if problems:
        return "", problems, 0

    out_pairs = [(ms, o, trans_by_ms.get(ms) or tr, r) for ms, o, tr, r in pairs]
    wrote = sum(1 for _, _, tr, _ in out_pairs if tr)
    if wrote == 0:
        return "", ["没有任何一行填了译文"], 0
    return _build(song, out_pairs), [], wrote


def _is_mono(body: str) -> bool:
    """正文是否只有单语行（没有同一时间戳成对）。"""
    return L.validate(body)["paired_groups"] == 0


def _build(song: lib.Song, pairs: list[tuple[int, str, str | None, str | None]]) -> str:
    """有译文 → 双语；全部没译文 → 单语。"""
    if any(tr for _, _, tr, _ in pairs):
        return L.build_bilingual_roles(song.title, song.artist, song.album, pairs,
                                       by="fnlrc/manual", roles=("orig", "trans"))
    return L.build_mono(song.title, song.artist, song.album,
                        [(ms, o) for ms, o, _, _ in pairs], by="fnlrc/manual")


# ------------------------------------------------------------------ 记忆库（默认关）
def _memory_path():
    from . import store
    return store.data_dir() / "translation-memory.json"


def norm_key(text: str) -> str:
    """记忆库键：归一化后的原文（复用 providers.norm 去掉标点/空白差异）。"""
    return P.norm(text)


def load_memory() -> dict[str, str]:
    p = _memory_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_memory(mem: dict[str, str]) -> None:
    p = _memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")


def remember(pairs: list[tuple[str, str]]) -> int:
    """把 (原文, 译文) 写进记忆库，返回新增条数。

    **只在「非中文原文 → 中文译文」方向记**：跨语种复用会到处串味。
    """
    mem = load_memory()
    added = 0
    for orig, trans in pairs:
        if not orig or not trans:
            continue
        if L.detect_lang(orig) == "zh":
            continue                       # 原文已经是中文，不需要记录
        if L.detect_lang(trans) != "zh":
            continue                       # 译文不是中文，不记（方向约束）
        k = norm_key(orig)
        if k and k not in mem:
            mem[k] = trans
            added += 1
    if added:
        save_memory(mem)
    return added


def recall(lines: list[str]) -> list[str | None]:
    """按顺序给出每行的记忆译文（没有则 None）。"""
    mem = load_memory()
    return [mem.get(norm_key(t)) for t in lines]


def export_files(songs: list[lib.Song], out_path: str, *, fmt: str | None = None,
                 with_context: bool = False, only_missing: bool = True,
                 root: str = "", log=None) -> dict:
    """便捷封装：导出到文件。CSV 用 utf-8-sig（Excel 友好）。"""
    if fmt is None:
        fmt = "json" if out_path.lower().endswith(".json") else "csv"
    text = export(songs, fmt=fmt, with_context=with_context,
                  only_missing=only_missing, root=root, log=log)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if fmt == "csv":
        with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(text)
    else:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    # 统计一下导出了多少行，便于 CLU 打印
    data = parse(text, fmt=fmt)
    return {"format": fmt, "files": len([k for k, v in data.items() if v]),
            "lines": sum(len(v) for v in data.values()), "out": out_path}
