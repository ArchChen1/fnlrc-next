#!/usr/bin/env python
"""库概览：扫描音乐库并输出统计（不联网、不写任何文件）。

这是「没有 UI 时的眼睛」——先看清楚库里有什么，再决定怎么补。

用法：
  python scripts/scan_library.py --root "\\\\NAS\\音乐"
  python scripts/scan_library.py --root /vol2/1000/音乐 --json report.json
  python scripts/scan_library.py --root 音乐 --show-broken 20      # 列出乱码/空歌词文件
  python scripts/scan_library.py --root 音乐 --spread 10          # 跨目录轮流抽 10 首（配合 --pilot 试跑）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fnlrc import library as lib  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="音乐库歌词现状体检")
    ap.add_argument("--root", required=True, help="音乐库根目录（本地路径 / UNC / 挂载点）")
    ap.add_argument("--json", help="把结果写到这个 JSON 文件")
    ap.add_argument("--limit", type=int, help="最多扫描多少首")
    ap.add_argument("--show-broken", type=int, default=0, help="列出前 N 个乱码/损坏的 .lrc")
    ap.add_argument("--spread", type=int, default=0, help="按目录轮流抽样 N 首并打印（试跑顺序预览）")
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        sys.exit(f"打不开这个路径：{args.root}")

    songs = lib.scan(args.root, limit=args.limit)
    summary = lib.summarize(songs)

    print(f"库根目录: {args.root}")
    print(f"音频文件: {summary['total']} 首")
    print(f"  已有双语   : {summary['bilingual']}")
    print(f"  单语/待双语: {summary['mono_or_single']}")
    print(f"  没有歌词   : {summary['missing']}")
    print(f"  乱码/损坏   : {summary['broken']}")
    print(f"  纯音乐(zxx) : {summary['instrumental']}")
    print()
    print(f"{'目录':<12}{'歌曲':>6}{'有.lrc':>8}{'双语':>7}{'单语':>7}{'乱码':>7}{'缺歌词':>8}")
    for top, d in sorted(summary["by_top"].items()):
        print(f"{top:<12}{d['audio']:>6}{d['lrc']:>8}{d['bilingual']:>7}"
              f"{d['mono']:>7}{d['broken']:>7}{d['none']:>8}")

    if args.show_broken:
        print(f"\n--- 乱码/损坏的歌词文件（前 {args.show_broken} 个）---")
        n = 0
        for s in songs:
            if s.has_lrc and lib.is_broken_lrc(s.lrc_path):
                text, enc = lib.read_text(s.lrc_path)
                reason = lib.looks_broken(text) or "空文件"
                print(f"  [{reason}] {s.rel}  (编码尝试: {enc})")
                n += 1
                if n >= args.show_broken:
                    break
        if n == 0:
            print("  （没有）")

    if args.spread:
        from collections import defaultdict, deque
        buckets: dict[str, deque] = defaultdict(deque)
        for s in songs:
            buckets[s.top].append(s)
        order, keys = [], sorted(buckets)
        while any(buckets.values()):
            for k in keys:
                if buckets[k]:
                    order.append(buckets[k].popleft())
        print(f"\n--- 跨目录抽样顺序（前 {args.spread} 首，建议用它做试跑）---")
        for i, s in enumerate(order[:args.spread], 1):
            print(f"  {i:>3}. {s.rel}")

    if args.json:
        data = {
            "root": args.root,
            "summary": summary,
            "songs": [
                {"rel": s.rel, "title": s.title, "artist": s.artist, "language": s.language,
                 "expected_lang": s.expected_lang, "has_lrc": s.has_lrc,
                 "broken_lrc": bool(s.has_lrc and lib.is_broken_lrc(s.lrc_path)),
                 "embedded_lyrics": bool(s.embedded_lyrics)}
                for s in songs
            ],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print(f"\n已写出 JSON: {args.json}")


if __name__ == "__main__":
    main()
