#!/usr/bin/env python
"""数据源冒烟测试：连通性 + 真实延迟（出问题时的第一件事）。

排查口诀：
  · 网易云 0.2–0.3s 是正常；>2s 说明网络/被限流
  · LRCLIB <1s 正常；但**偶发 20–35s** 是已知行为 → 代码里已设 8s 快速失败
  · 如果 LRCLIB 持续超时：外网不通，或该站临时故障 → 靠源站译词也能跑

用法：
  python scripts/smoke_sources.py
  python scripts/smoke_sources.py --query "残酷な天使のテーゼ" --artist "高橋洋子"
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fnlrc import providers as P  # noqa: E402

DEFAULT_CASES = [
    ("日语", "残酷な天使のテーゼ", "高橋洋子"),
    ("英语", "Teeth", "5 Seconds Of Summer"),
    ("俄语", "Умри если меня не любишь", "Dakooka"),
]


def timed(label, fn, warn_over=2.0):
    t0 = time.time()
    try:
        out = fn()
        dt = time.time() - t0
        flag = "  ⚠ 偏慢" if dt > warn_over else ""
        print(f"  {label:<44} {dt:6.2f}s  {out}{flag}")
        return dt, out
    except Exception as e:
        dt = time.time() - t0
        print(f"  {label:<44} {dt:6.2f}s  ❌ {type(e).__name__}: {e}")
        return dt, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", help="自定义查询（歌名）")
    ap.add_argument("--artist", default="")
    args = ap.parse_args()

    cases = [(None, args.query, args.artist)] if args.query else DEFAULT_CASES

    print("=== 1) 网易云（原词 + 中文译词 + 罗马音）===")
    for tag, title, artist in cases:
        print(f"[{tag or '自定义'}] {title} / {artist}")
        hits = timed("搜索", lambda t=title, a=artist: len(
            P._ne_search(f"{t} {a}".strip(), limit=5)))
        info = timed("取歌词（含译词）", lambda t=title, a=artist: (
            lambda r: f"原词+译词={len(r['pairs'])} 行 lang={r.get('lyric_lang')} "
                      f"有译词={'是' if r.get('has_translation') else '否'}"
        )(P.netease_fetch(t, a, None, expected=None)))
        print()

    print("=== 2) LRCLIB（时间轴原词）===")
    for tag, title, artist in cases:
        timed(f"搜索+取词 {title[:28]}", lambda t=title, a=artist: (
            lambda r: "无结果" if not r else f"{len(r['entries'])} 行 lang={r.get('lyric_lang')}"
        )(P.lrclib_fetch(t, a, "", None, expected=None)))
    print("  提示：LRCLIB 偶发 20–35s 属已知行为；项目已设 8s 快速失败 + 单曲 25s 总时限")

    print("\n=== 3) 翻译后端 ===")
    from fnlrc import store
    cfg = store.load_config()
    key = store.effective_api_key(cfg)
    base = cfg.get("remote_base_url") or "（未配置）"
    model = cfg.get("remote_model") or "（未配置）"
    if key:
        src = "环境变量 FNLRC_REMOTE_API_KEY" if store.api_key_locked() else "config.json"
        print(f"  ✅ 已配置远端翻译：{base} / {model}")
        print(f"     key 来自 {src}，掩码 {P.mask_key(key)}")
        ok, msg = P.remote_test(base, key, model)
        print(f"     连通性：{'✅' if ok else '❌'} {msg}")
    else:
        print("  ⚪ 没配远端 API（不影响：默认用源站译词，零成本零出网）")
        print("     想用 DeepSeek 补翻：fnlrc config --set remote_base_url=https://api.deepseek.com \\")
        print("                                     --set remote_model=deepseek-flash \\")
        print("                                     --set remote_api_key=sk-xxxx")


if __name__ == "__main__":
    main()
