#!/usr/bin/env python
"""库可写性 + 标签体检。

两件事：
  1) 目标目录到底能不能写（Web UI 里的「测试写入」按钮会调这个逻辑）
  2) 抽若干首歌，打印它们的标签，看匹配歌词需要的元数据全不全

为什么要单独测写入：SMB 共享、WebDAV、容器挂载、飞牛的目录属主都可能让你「能读不能写」，
而且失败原因各不相同（只读挂载 / 权限 / 凭据不同 / 被占用）。先测出来，别等跑了半小时才失败。

⚠️ 副作用：探针文件是「建了再删」。在**开了回收站**的系统上（飞牛就是），删除会进回收站，
所以每测一个目录就会在回收站留一条 `fnlrc_write_test.tmp`。库内是干净的，回收站里有记录。
用 `--subdirs N` 控制测几个子目录（默认 8，`--subdirs 0` 完全不测子目录）。

用法：
  python scripts/probe_write.py --root "\\\\NAS\\音乐"
  python scripts/probe_write.py --root /vol2/1000/音乐 --sample 8
  python scripts/probe_write.py --root /vol2/1000/音乐 --subdirs 0     # 只测根目录
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fnlrc import library as lib  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="测试写入权限并抽查标签")
    ap.add_argument("--root", required=True)
    ap.add_argument("--sample", type=int, default=5, help="抽查多少首歌的标签")
    ap.add_argument("--subdirs", type=int, default=8,
                    help="最多抽查多少个子目录（0 = 不查子目录）。默认 8 而不是全部 —— "
                         "每查一个目录就会产生一次删除，在飞牛上这会进回收站")
    args = ap.parse_args()

    root = args.root
    print(f"目标目录: {root}")
    if not os.path.isdir(root):
        sys.exit("  ✗ 目录不存在或打不开")

    # ---- 1) 写入测试 ----
    # 注意：不要用「点开头」的文件名做探针 —— 部分 SMB/Samba 配置（veto files / hide dot files）
    # 会拒绝创建点文件，导致误判为「不可写」。
    probe = os.path.join(root, "fnlrc_write_test.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("fnlrc write test\n")
        size = os.path.getsize(probe)
        os.remove(probe)
        print(f"  ✅ 可写（创建并删除测试文件成功，{size} 字节）")
    except Exception as e:
        print(f"  ❌ 不可写：{type(e).__name__}: {e}")
        print("     排查：① 目录是否只读挂载 ② 容器/进程用户对目录的权限")
        print("           ③ SMB 与 WebDAV 可能是不同凭据 ④ 文件是否被占用")
        sys.exit(1)

    # 附加检查：点文件是否被 SMB 策略拦截（不影响主流程，只是告知）
    dotprobe = os.path.join(root, ".__fnlrc_write_test.tmp")
    try:
        with open(dotprobe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(dotprobe)
    except Exception as e:
        print(f"  ℹ 点开头的文件无法创建（{type(e).__name__}）——"
              f"该共享可能启用了 veto/hide dot files；本工具不使用点文件，无影响")

    # ---- 2) 子目录写入测试（有些库只在子目录不可写）----
    # ⚠️ 探针是「建了再删」。在飞牛上（以及开了回收站的 SMB 共享上）**删除会进回收站**，
    # 所以每个被测目录都会在回收站留一条记录 —— 这是本脚本唯一的副作用，故默认限量。
    dirs = sorted({os.path.dirname(p) for p in lib.iter_audio(root)})
    limit = max(0, args.subdirs)
    bad = []
    for d in dirs[:limit]:
        p = os.path.join(d, "fnlrc_write_test.tmp")
        try:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("x")
            os.remove(p)
        except Exception as e:
            bad.append((os.path.relpath(d, root), f"{type(e).__name__}: {e}"))
    if limit == 0:
        print("  ℹ 跳过了子目录写入检查（--subdirs 0）")
    elif bad:
        print(f"  ⚠ 有 {len(bad)} 个子目录不可写：")
        for rel, err in bad[:10]:
            print(f"     - {rel}: {err}")
    else:
        print(f"  ✅ 抽查的 {min(len(dirs), limit)} 个子目录都可以写")
    if dirs and limit:
        print(f"  ℹ 探针文件已经删掉，但在开了回收站的系统上（飞牛、部分 SMB）这些删除"
              f"会进回收站。想清干净：")
        print(f"     find \"{root}\" -name 'fnlrc_write_test.tmp' -delete   # 库内如有残留")
        print(f"     # 回收站里的那几条，在飞牛的文件管理器里清空回收站即可")

    # ---- 3) 标签体检 ----
    if args.sample:
        print(f"\n--- 标签抽查（前 {args.sample} 首）---")
        n = 0
        missing_meta = 0
        for path in lib.iter_audio(root):
            s = lib.load_song(path, root)
            tags = lib.read_tags(path)
            flags = []
            if not tags.get("title"):
                flags.append("无标题(用文件名兜底)")
            if not tags.get("artist"):
                flags.append("无艺人")
            if not tags.get("album"):
                flags.append("无专辑")
            if not tags.get("language"):
                flags.append("无 language 标签")
            if s.embedded_lyrics:
                flags.append(f"⚠ 内嵌歌词 {len(s.embedded_lyrics)} 字符(会压住外挂文件)")
            if flags:
                missing_meta += 1
            print(f"  {s.rel}")
            print(f"      title={s.title!r} artist={s.artist!r} album={s.album!r} "
                  f"language={s.language!r} 时长={s.duration and round(s.duration, 1)}s "
                  f"预期语言={s.expected_lang}")
            if flags:
                print(f"      ⚠ {' / '.join(flags)}")
            n += 1
            if n >= args.sample:
                break
        if n == 0:
            print("  （没找到音频文件）")
        else:
            print(f"\n  元数据有缺口的: {missing_meta}/{n}")
            print("  说明：缺 title/artist 会明显降低歌词匹配率；缺 language 会让「语言交叉校验」退化")


if __name__ == "__main__":
    main()
