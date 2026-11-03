#!/usr/bin/env python
"""语言标签体检：统计 language 标签分布，列出缺失的（P1「语言标签补全」的输入）。

为什么重要：
  · `language` 决定「这首歌要不要翻译」（中文歌不加译文）
  · 它同时是**匹配校验**的依据（外文曲目却抓到中文歌词 ⇒ 可能是中文翻唱版，不自动写）
  · 实测某 520 首库里 93 首没有这个标签，其中 54 首是日语歌——补全它们能显著提升判定质量

用法：
  python scripts/scan_languages.py --root "\\\\NAS\\音乐"
  python scripts/scan_languages.py --root /vol2/1000/音乐 --list-missing 30 --json langs.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fnlrc import library as lib  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="language 标签分布体检")
    ap.add_argument("--root", required=True)
    ap.add_argument("--list-missing", type=int, default=0, help="列出前 N 首缺标签的（含推断结果）")
    ap.add_argument("--json", help="结果写入 JSON")
    args = ap.parse_args()

    songs = lib.scan(args.root)
    by_dir: dict[str, Counter] = defaultdict(Counter)
    missing = []
    for s in songs:
        by_dir[s.top][s.language or "(缺)"] += 1
        if not s.language:
            missing.append(s)

    print(f"共 {len(songs)} 首，其中缺 language 标签的 {len(missing)} 首 "
          f"（{len(missing) * 100 // max(len(songs), 1)}%）\n")
    print(f"{'目录':<12}{'标签分布'}")
    for top, c in sorted(by_dir.items()):
        dist = " · ".join(f"{k} {v}" for k, v in c.most_common(8))
        print(f"{top:<12}{dist}")

    # 标签 → 脚本语言 的映射覆盖情况
    known = {k: v for k, v in lib.LANG_MAP.items()}
    unknown = sorted({s.language for s in songs if s.language and s.language not in known})
    if unknown:
        print(f"\n⚠ 未认识的 language 取值（语言交叉校验会退化为「不判断」）：{unknown}")
    else:
        print("\n✅ 所有出现过的 language 取值都在映射表内")

    if args.list_missing:
        print(f"\n--- 缺标签的前 {args.list_missing} 首（含按目录名推断的结果）---")
        for s in missing[:args.list_missing]:
            print(f"  {s.rel}")
            print(f"      推断预期语言={s.expected_lang}（目录={s.top}）"
                  f"{'  ← 只能靠目录名猜，建议补标签' if s.expected_lang else '  ← 无法推断'}")

    if args.json:
        data = {
            "total": len(songs),
            "missing_tag": len(missing),
            "by_dir": {k: dict(v) for k, v in by_dir.items()},
            "unknown_tag_values": unknown,
            "missing": [{"rel": s.rel, "title": s.title, "artist": s.artist,
                         "inferred": s.expected_lang} for s in missing],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print(f"\n已写出: {args.json}")


if __name__ == "__main__":
    main()
