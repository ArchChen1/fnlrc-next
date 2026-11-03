"""单曲决策 + 批量执行。

这是三端（CLI / Web / 桌面）共用的唯一入口，不含任何 UI 逻辑。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from . import library as lib
from . import lrc as L
from . import providers as P


@dataclass
class Config:
    """一次补完任务的配置。"""
    write: bool = False                 # False = 只生成到 dump_dir 供预览，不碰音乐库
    dump_dir: str | None = None
    force: bool = False                 # 已有合规双语文件也重做
    # source=只用源站译词（不调任何后端）| remote=用户自己的 OpenAI 兼容端点 | none=同 source
    translate: str = "source"
    remote_base_url: str = ""
    # repr=False：dataclass 的默认 repr 会把 key 原样打出来，任何 print(cfg) 或异常
    # 路径里的记录都会泄露它。
    remote_api_key: str = field(default="", repr=False)
    remote_model: str = ""
    remote_timeout: int = 60
    deadline_s: int = 25                # 单曲**抓取**总时限
    # 翻译阶段的独立预算。刻意不用 deadline_s：远端 API 一首长歌可能要翻好几个分块，
    # 用 25s 会把稍长的歌直接判成超时并降级为单语。
    translate_deadline_s: int = 180
    backup: bool = True                 # 替换已有 .lrc 前备份为 .bak
    # 外文单语歌词：保留**本地已有的时间轴与断句**，只把译文按同一时间戳插进去，
    # 而不是联网重抓、整份覆盖。用户手工精修过时间轴时这一步差别很大。
    # 对齐结果过不了飞牛双行规则时会自动退回重抓，所以最坏情况等于关掉它。
    keep_local: bool = True
    limit: int | None = None
    # 歌词源筛选（M2）：None/空 = 不限制。黑名单优先级高于白名单。
    # 放在 Config 里而不是让 fetch_lyrics 读全局配置，是为了让 pipeline 保持纯粹、
    # 单测里不依赖本机 config.json。
    providers_enabled: list[str] | None = None
    providers_disabled: list[str] | None = None

    def resolved_dump(self) -> str:
        return self.dump_dir or os.path.join(os.getcwd(), "fnlrc_out")


# ------------------------------------------------------------------ 预览（不联网）
def decide(song: lib.Song, cfg: Config) -> dict:
    """静态判断这首歌会被怎么处理，用于「扫描后先看一遍」。"""
    from . import lrc as L

    if song.expected_lang == "instrumental" or song.instrumental:
        return {"action": "skip", "reason": "纯音乐（language=zxx）"}
    if song.has_lrc and not cfg.force:
        try:
            text, _ = lib.read_text(song.lrc_path)
        except Exception:
            return {"action": "rebuild", "reason": "现有 .lrc 读不出来 → 重新获取"}
        broken = lib.looks_broken(text)
        if broken:
            # 把乱码的**形态**说出来。乱码多半是源头就坏了（实测过 GBK 被按 Big5 解），
            # 重刮通常还是同一来源、救不回来 —— 这时候对用户有用的不是"再试一次"，
            # 而是"这份得由你提供正确原文"。
            return {"action": "rebuild",
                    "reason": f"现有 .lrc 是乱码（{broken}）→ 重新获取。"
                              f"若重刮结果依旧，只能由你提供正确原文"}
        if not text.strip():
            return {"action": "rebuild", "reason": "现有 .lrc 是空文件 → 重新获取"}
        try:
            v = L.validate(text)
        except Exception:
            return {"action": "rebuild", "reason": "现有 .lrc 读不出来 → 重新获取"}
        if v["ok"]:
            return {"action": "skip", "reason": f"已是合规双语（并行组 {v['paired_groups']}）"}
        if v["total_ts"] >= 4 and L.detect_lang(L.TS_RE.sub("", text)) == "zh":
            return {"action": "skip", "reason": "中文歌词单语文件，无需双语"}
        if cfg.keep_local:
            return {"action": "bilingualize",
                    "reason": "外文单语歌词 → 保留本地时间轴，只补译文"}
        return {"action": "bilingualize", "reason": "外文单语歌词 → 重抓后补成双语"}
    if song.has_lrc:
        return {"action": "rebuild", "reason": "--force 强制重做"}
    return {"action": "create", "reason": "没有外挂歌词文件"}


# ------------------------------------------------------------------ 翻译后端
def _translate_missing(lines: list[str], cfg: Config, deadline: float | None = None,
                       log=None) -> tuple[list[str] | None, str]:
    """补齐源站没给出译文的那些行。→ (与输入等长的译文列表 或 None, 后端串)。

    | 配置 | 行为 |
    |---|---|
    | `source`（默认） | **不调任何后端**。源站没给译词就写单语 —— 零配置、零成本、零出网 |
    | `none` | 同 source，且明确表示不翻译 |
    | `remote` | 调用户自己配的 DeepSeek 端点 |

    只翻**缺译文的那几行**：源站译词优先，它来自人工/字幕组，质量比机器翻译好。

    远端返回部分结果时**逐行保留** —— 翻了一半的行不该被丢回单语。
    """
    if not lines:
        return [], "none"
    if cfg.translate != "remote":
        return None, "source"
    if not (cfg.remote_base_url and cfg.remote_model):
        if log:
            log("   还没配 Base URL 或模型名，跳过翻译")
        return None, "none"

    out, err = P.remote_translate(lines, cfg.remote_base_url, cfg.remote_api_key,
                                  cfg.remote_model, timeout=cfg.remote_timeout,
                                  deadline=deadline)
    if not out:
        if log:
            log(f"   远端翻译不可用：{err}")
        return None, "remote"
    return [out.get(i, "") for i in range(1, len(lines) + 1)], "remote"


# ------------------------------------------------------------------ 单曲处理
def _keep_local_body(
    song: lib.Song,
    pairs: list[tuple[int, str, str | None, str | None]],
) -> tuple[str | None, int, str | None]:
    """把译文对到**本地已有的时间轴**上，生成双语正文。

    → (正文 | None, 已有译文的行数, 放弃的原因 | None)

    只在本地文件确实是**外文单语**时才做。如果它已经是双行（同一时间戳出现两行），
    就该走「已是合规双语」的跳过分支；`--force` 重做时更不能拿它当基底 ——
    那会把原文行和译文行同时当成"本地原文"，越补越乱。所以重复时间戳直接放弃。

    放弃时返回原因而不是静默跳过：用户需要知道"我以为保住了时间轴，其实没有"。
    """
    try:
        text, _ = lib.read_text(song.lrc_path)
    except Exception:
        return None, 0, None
    local = L.parse_lrc(text)
    if len(local) < 4:
        return None, 0, None
    stamps = [ms for ms, _ in local]
    if len(set(stamps)) != len(stamps):
        return None, 0, None
    if L.detect_lang(L.TS_RE.sub("", text)) == "zh":
        return None, 0, None            # 中文单语，本来就无需双语

    aligned, hits = P.align_translation(local, pairs)
    if not hits:
        return None, 0, "本地时间轴：源站没有可用译文，改为重抓"
    body = L.build_bilingual_roles(song.title, song.artist, song.album,
                                   [(ms, o, tr, None) for ms, o, tr in aligned],
                                   by="fnlrc/keep-local", roles=("orig", "trans"))
    v = L.validate(body)
    if v["ok"]:
        return body, hits, None
    return None, hits, (f"本地时间轴版双语没过飞牛双行规则（并行组 {v['paired_groups']}、"
                        f"比例 {v['pair_ratio']}）→ 改为重抓")


def process_song(song: lib.Song, cfg: Config, log=None) -> dict:
    def say(msg):
        if log:
            log(msg)

    res = {"file": song.rel, "title": song.title, "artist": song.artist,
           "status": "", "source": "", "backend": "", "pairs": 0,
           "expected_lang": song.expected_lang,
           "expected_lang_source": song.expected_lang_source,
           "lyric_lang": None,
           "validation": None, "note": "", "elapsed_s": 0.0,
           # 这次的双语是"沿用本地时间轴"还是"整份重抓"，报告里要能区分开
           "keep_local": False,
           # 匹配回显：让用户能核对「到底配到了哪一首」，而不是只能看文件名
           "matched": None, "score": None, "candidates": [], "alternatives": {}}
    t0 = time.time()

    if song.expected_lang == "instrumental" or song.instrumental:
        res.update(status="skip_instrumental", note="纯音乐，无需歌词")
        res["elapsed_s"] = round(time.time() - t0, 1)
        return res

    dl = time.time() + cfg.deadline_s
    # 统一降级链：按 priority 依次问注册表里的每个源（网易云 → LRCLIB → …）。
    # 内置源也走同一协议，新增源只要注册进来就自动进入这条链。
    src_errors: list[tuple[str, str]] = []
    result = P.fetch_lyrics(song, cfg, deadline=dl, log=log, errors=src_errors)
    pairs: list[tuple[int, str, str | None, str | None]] = []
    src = ""
    if result is not None and getattr(result, "instrumental", False):
        res.update(status="skip_instrumental", source=result.source, note="源站标记为纯音乐")
        res["elapsed_s"] = round(time.time() - t0, 1)
        return res
    if result is not None and result.pairs:
        src = result.source
        pairs = list(result.pairs)
        res["lyric_lang"] = result.lyric_lang
        res["matched"] = result.matched
        res["score"] = result.score
        res["candidates"] = result.candidates or []
        res["alternatives"] = result.alternatives or {}

    if not pairs:
        # 所有可用的源都取词失败（抛异常）→ 这是"坏"，不是"没有歌词"。
        # 不这么分的话，一个源崩了会被误报成 no_lyrics，用户永远等不到它自愈。
        if src_errors:
            detail = "；".join(f"{n}: {m}" for n, m in src_errors)
            res.update(status="error", note=P.mask_secret(f"歌词源取词失败 —— {detail}"))
            res["elapsed_s"] = round(time.time() - t0, 1)
            return res
        # 只有文件名/目录名像纯音乐，没有权威标记（language=zxx 或源站 instrumental）。
        # 报成「跳过」会让它进不了任何重试队列 —— 匹配失败就永远查不出来。
        looks_inst = bool(lib.INSTRUMENTAL_HINT.search(song.title)) or "纯音乐" in song.rel
        if time.time() >= dl - 2:
            res.update(status="timeout", note="抓取超时（未查完），可重试")
        elif looks_inst:
            res.update(status="no_lyrics",
                       note="两个源都没有歌词（文件名像纯音乐，但没有 zxx 标记，保留待重试）")
        else:
            res.update(status="no_lyrics", note="两个源都没有歌词")
        res["elapsed_s"] = round(time.time() - t0, 1)
        return res

    joined = " ".join(o for _, o, _, _ in pairs)
    lyric_lang = L.detect_lang(joined)
    res["lyric_lang"] = lyric_lang

    if lyric_lang == "zh" and song.expected_lang not in (None, "zh", "instrumental"):
        res.update(status="lang_mismatch", source=src,
                   note=f"外文曲目却抓到中文版歌词（预期 {song.expected_lang}），需人工确认")
        res["elapsed_s"] = round(time.time() - t0, 1)
        return res

    wants_translation = lyric_lang != "zh"
    if wants_translation:
        missing = [i for i, (_, _, t, _) in enumerate(pairs) if not t]
        if missing:
            lines = [pairs[i][1] for i in missing]
            say(f"   翻译 {len(lines)} 行（后端 {cfg.translate}）")
            trans, backend = _translate_missing(
                lines, cfg, deadline=time.time() + cfg.translate_deadline_s, log=say)
            res["backend"] = backend
            if trans is not None:
                for k, i in enumerate(missing):
                    ms, o, _, r = pairs[i]
                    pairs[i] = (ms, o, trans[k], r)
                if src == "netease":
                    src = "netease+translate"
        else:
            res["backend"] = "source"

    res["source"] = src

    # 本地优先：用户自己精修过时间轴/断句的 .lrc 不该被整份换掉。
    # decide() 一直把这种情况标成 "bilingualize"（外文单语歌词 → 补成双语），
    # 但在那之前它只是个没人消费的标签，实际走的是"联网重抓、整份覆盖"。
    kl_why = ""
    if cfg.keep_local and wants_translation and song.has_lrc:
        body, hits, why = _keep_local_body(song, pairs)
        if body and why is None:
            res["status"] = "written_keep_local"
            res["keep_local"] = True
            res["validation"] = L.validate(body)
            res["pairs"] = res["validation"]["paired_groups"]
            res["backend"] = (res["backend"] or "source") + "+keep-local"
            res["note"] = f"保留本地时间轴与断句，按同一时间戳插入 {hits} 行译文"
            return _finish_write(res, song, cfg, body, t0)
        if why:
            # 保留失败的原因要**留着**，不能被下面重抓分支的 note 冲掉 ——
            # 用户以为保住了时间轴、其实被换掉了，这是最需要被告知的情况。
            kl_why = why
            say(f"   {why}")

    def note(text: str) -> str:
        return f"{kl_why}；{text}" if kl_why else text

    # 单语候选：**永远可接受，不能走 validate()**。
    # validate() 对单语恒为 False（paired_groups 数的是"有 ≥2 行的时间戳"，单语这里是 0），
    # 让它参与校验会把"无译文"的歌判成不通过，结果一个文件都不写 —— 比现状更糟。
    mono_body = L.build_mono(song.title, song.artist, song.album,
                             [(ms, o) for ms, o, _, _ in pairs], by=f"fnlrc/{src}")

    if wants_translation and any(r[2] for r in pairs):
        bi_body = L.build_bilingual_roles(song.title, song.artist, song.album, pairs,
                                          by=f"fnlrc/{src}", roles=("orig", "trans"))
        v = L.validate(bi_body)
        res["validation"] = v
        res["pairs"] = v["paired_groups"]
        if v["ok"]:
            res["note"] = kl_why
            return _finish_write(res, song, cfg, bi_body, t0)
        # 双语不过飞牛规则（译文只覆盖了一部分，成对比例不足）→ 退回单语，
        # 而不是什么都不写。译文进了正文也没用：不成对就只是"下一条歌词"。
        res["status"] = "written_mono"
        res["note"] = note(f"双语未通过飞牛双行规则（并行组 {v['paired_groups']}、"
                           f"比例 {v['pair_ratio']}）→ 已写单语，可换翻译后端重跑")
        return _finish_write(res, song, cfg, mono_body, t0)

    res["validation"] = L.validate(mono_body)
    res["status"] = "written_mono"
    res["note"] = note("无可用译文 → 已写单语（可稍后用翻译后端重跑）" if wants_translation
                       else "中文歌词，按单语写入")
    return _finish_write(res, song, cfg, mono_body, t0)


def _finish_write(res: dict, song: lib.Song, cfg: Config, body: str, t0: float) -> dict:
    if cfg.write:
        target = song.lrc_path
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.isfile(target) and cfg.backup:
                bak = target + ".bak"
                if not os.path.isfile(bak):
                    os.replace(target, bak)
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        except Exception as e:
            res.update(status="write_failed",
                       note=P.mask_secret(f"{type(e).__name__}: {e}"))
            res["elapsed_s"] = round(time.time() - t0, 1)
            return res
        if not res["status"]:
            res["status"] = "written"
    else:
        out = os.path.join(cfg.resolved_dump(), song.rel)
        out = os.path.splitext(out)[0] + ".lrc"
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        if not res["status"]:
            res["status"] = "dumped"
    res["elapsed_s"] = round(time.time() - t0, 1)
    return res


# ------------------------------------------------------------------ 批量
def should_skip_existing(song: lib.Song, cfg: Config) -> str | None:
    """已处理的文件直接跳过，返回原因。"""
    if cfg.force or not song.has_lrc:
        return None
    if not cfg.write:
        return None       # 预览模式不跳过，便于看判定
    if lib.is_broken_lrc(song.lrc_path):
        return None
    try:
        text, _ = lib.read_text(song.lrc_path)
        v = L.validate(text)
    except Exception:
        return None
    if v["ok"]:
        return f"已是合规双语（并行组 {v['paired_groups']}）"
    if v["total_ts"] >= 4 and L.detect_lang(L.TS_RE.sub("", text)) == "zh":
        return "中文单语歌词，无需双语"
    return None


def run_batch(songs: list[lib.Song], cfg: Config, on_event=None, should_stop=None) -> dict:
    """串行处理，逐首回调。on_event(event: dict)；should_stop() 返回 True 则优雅停止。"""
    stats = {"total": len(songs), "written": 0, "written_keep_local": 0, "written_mono": 0,
             "skipped": 0,
             "instrumental": 0, "no_lyrics": 0, "timeout": 0, "lang_mismatch": 0,
             "failed": 0, "done": 0}
    results = []
    t_start = time.time()

    def emit(ev):
        if on_event:
            on_event(ev)

    emit({"type": "start", "total": len(songs)})
    for i, song in enumerate(songs, 1):
        if should_stop and should_stop():
            emit({"type": "stopped", "index": i - 1})
            break
        why = should_skip_existing(song, cfg)
        if why:
            r = {"file": song.rel, "title": song.title, "artist": song.artist,
                 "status": "skipped_existing", "note": why, "pairs": 0,
                 "source": "", "backend": "", "validation": None,
                 "keep_local": False,
                 "expected_lang": song.expected_lang, "elapsed_s": 0.0}
        else:
            try:
                r = process_song(song, cfg)
            except Exception as e:
                r = {"file": song.rel, "title": song.title, "artist": song.artist,
                     "status": "error", "note": P.mask_secret(f"{type(e).__name__}: {e}"), "pairs": 0,
                     "source": "", "backend": "", "validation": None,
                     "keep_local": False,
                     "expected_lang": song.expected_lang, "elapsed_s": 0.0}
        results.append(r)
        stats["done"] = i
        st = r["status"]
        if st in ("written", "written_keep_local", "dumped"):
            stats["written"] += 1
            if st == "written_keep_local":
                stats["written_keep_local"] += 1
        elif st == "written_mono":
            stats["written_mono"] += 1
        elif st in ("skipped_existing", "skip"):
            stats["skipped"] += 1
        elif st == "skip_instrumental":
            stats["instrumental"] += 1
        elif st == "no_lyrics":
            stats["no_lyrics"] += 1
        elif st == "timeout":
            stats["timeout"] += 1
        elif st == "lang_mismatch":
            stats["lang_mismatch"] += 1
        else:
            stats["failed"] += 1
        emit({"type": "song", "index": i, "total": len(songs), "result": r})
    total_s = round(time.time() - t_start, 1)
    stats["elapsed_s"] = total_s
    emit({"type": "done", "stats": stats, "results": results})
    return {"stats": stats, "results": results, "elapsed_s": total_s}
