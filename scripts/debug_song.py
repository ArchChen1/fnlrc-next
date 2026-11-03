#!/usr/bin/env python
"""单曲调试：为什么这首歌没配上 / 配错了 / 没双行？

它会把整条决策链打印出来：标签 → 预期语言 → 各源候选与打分 → 选中哪个 → 歌词实际语言
→ 语言交叉校验 → 会写什么文件 → 飞牛规则校验结果。**出问题时用这个抓一份日志就够了。**

用法：
  python scripts/debug_song.py --file "\\\\NAS\\音乐\\日语\\某歌.flac"
  python scripts/debug_song.py --title "残酷な天使のテーゼ" --artist "高橋洋子" --duration 246
  python scripts/debug_song.py --file ... --preview 12
  python scripts/debug_song.py --title "..." --translate remote   # 用已配好的 DeepSeek
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fnlrc import library as lib  # noqa: E402
from fnlrc import lrc as L  # noqa: E402
from fnlrc import pipeline as pl  # noqa: E402
from fnlrc import providers as P  # noqa: E402
from fnlrc import store  # noqa: E402


def resolve_song(args) -> lib.Song:
    if args.file:
        root = os.path.dirname(os.path.abspath(args.file))
        return lib.load_song(args.file, root)
    if not args.title:
        sys.exit("需要 --file 或 --title")
    return lib.Song(path="(未指定文件)", lrc_path="", rel="(未指定文件)",
                    title=args.title, artist=args.artist or "", album=args.album or "",
                    duration=args.duration, expected_lang=args.expected)


def _print_pool(pool) -> None:
    """打印候选池 —— 「为什么选了这一首」的答案就在这里。"""
    if not pool:
        return
    print(f"  候选池（按有效分排序，共 {len(pool)} 个）:")
    for i, c in enumerate(pool, 1):
        flag = "  ← 选中" if i == 1 else ""
        print(f"    {i}. {c.get('matched')}  score={c.get('score')} "
              f"eff={c.get('effective')} lang={c.get('lyric_lang')} "
              f"行={c.get('lines')}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser(description="单曲歌词匹配调试")
    ap.add_argument("--file", help="音频文件路径（推荐：会读取真实标签）")
    ap.add_argument("--title"); ap.add_argument("--artist", default="")
    ap.add_argument("--album", default=""); ap.add_argument("--duration", type=float)
    ap.add_argument("--expected", help="手工指定预期语言（zh/ja/latin/ru），覆盖自动推断")
    ap.add_argument("--translate", default="source", choices=["source", "remote", "none"])
    ap.add_argument("--remote-base", help="OpenAI 兼容端点，如 https://api.deepseek.com（DeepSeek 不要加 /v1）")
    ap.add_argument("--remote-key", help="API key（默认用 config.json / 环境变量里已配的）")
    ap.add_argument("--remote-model", help="模型名，如 deepseek-flash")
    ap.add_argument("--preview", type=int, default=12, help="打印生成结果的前 N 行")
    args = ap.parse_args()

    song = resolve_song(args)
    print("=== 1) 曲目信息 ===")
    print(f"  文件      : {song.path}")
    print(f"  标题/艺人 : {song.title!r} / {song.artist!r}   专辑={song.album!r}")
    print(f"  时长      : {song.duration}s")
    print(f"  语言标签  : {song.language!r}  →  预期语言={song.expected_lang}")
    if song.embedded_lyrics:
        print(f"  ⚠ 内嵌歌词: {len(song.embedded_lyrics)} 字符 —— 飞牛优先级「内嵌(4) > 外挂(3)」，"
              f"外挂文件会被压住")
    if song.has_lrc:
        print(f"  现有 .lrc : {song.lrc_path}")
        if lib.is_broken_lrc(song.lrc_path):
            print("              ⚠ 判定为乱码/空文件 → 应重建")
        else:
            text, enc = lib.read_text(song.lrc_path)
            v = L.validate(text)
            print(f"              编码={enc} 校验={v}")
    else:
        print("  现有 .lrc : （无）")

    print("\n=== 2) 静态判定（不联网）===")
    # 远端参数：命令行显式给的优先，否则用 config.json / 环境变量里已配好的 ——
    # 之前这里从不填 remote_*，所以 --translate remote 是走不通的（静默返回"没译文"）
    cfg = pl.Config(
        translate=args.translate,
        remote_base_url=args.remote_base or store.load_config().get("remote_base_url", ""),
        remote_api_key=args.remote_key or store.effective_api_key(),
        remote_model=args.remote_model or store.load_config().get("remote_model", ""),
    )
    d = pl.decide(song, cfg)
    print(f"  动作: {d['action']}   原因: {d['reason']}")
    if d["action"] == "skip":
        print("  → 这首先跳过，不会发起任何网络请求")
        return

    print("\n=== 3) 网易云候选 ===")
    info = P.netease_fetch(song.title, song.artist, song.duration,
                           expected=song.expected_lang, debug=True)
    if info:
        print(f"  选中: {info['matched']}  打分={info['score']}  "
              f"歌词语言={info.get('lyric_lang')}  有译词={'是' if info.get('has_translation') else '否'}")
        _print_pool(info.get("candidates"))
    else:
        print("  没有可用候选")

    if not info or not info.get("pairs"):
        print("\n=== 4) LRCLIB 候选 ===")
        got = P.lrclib_fetch(song.title, song.artist, song.album, song.duration,
                             expected=song.expected_lang, debug=True)
        if got:
            print(f"  选中: {got['matched']}  打分={got['score']}  "
                  f"歌词语言={got.get('lyric_lang')}  行数={len(got['entries'])}")
            _print_pool(got.get("candidates"))
        else:
            print("  没有可用候选")

    print("\n=== 5) 完整跑一遍（不写文件）===")
    cfg.write = False
    cfg.dump_dir = os.path.join(os.getcwd(), "_debug_out")
    res = pl.process_song(song, cfg, log=lambda m: print(m))
    for k in ("status", "source", "backend", "pairs", "lyric_lang",
              "expected_lang", "expected_lang_source", "matched", "score", "note", "elapsed_s"):
        print(f"  {k:<20}= {res.get(k)}")
    if res.get("validation"):
        print(f"  validation  = {res['validation']}")
        print("  → 通过飞牛规则 = " + ("✅ 会双行显示" if res["validation"]["ok"] else "❌ 不会双行"))

    out = os.path.join(cfg.resolved_dump(), os.path.basename(song.lrc_path or "out.lrc"))
    if os.path.isfile(out):
        print(f"\n=== 6) 生成结果预览（{args.preview} 行）: {out} ===")
        with open(out, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= args.preview + 4:
                    print("  …")
                    break
                print("  " + line.rstrip())


if __name__ == "__main__":
    main()
