"""命令行：scan / run / report / config / token / serve。

退出码：0 = 全部正常；1 = 用法或致命错误；2 = 跑完了但有失败项（便于 cron 判断）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from . import library as lib
from . import pipeline as pl
from . import providers as P
from . import store


def _log(msg: str) -> None:
    print(msg, flush=True)


def _config_from_args(a) -> pl.Config:
    cfg = store.load_config()
    return pl.Config(
        write=bool(getattr(a, "write", False)),
        dump_dir=getattr(a, "dump", None),
        force=bool(getattr(a, "force", False)),
        translate=getattr(a, "translate", None) or cfg.get("translate", "source"),
        remote_base_url=getattr(a, "remote_base", None) or cfg.get("remote_base_url", ""),
        remote_api_key=getattr(a, "remote_key", None) or store.effective_api_key(cfg),
        remote_model=getattr(a, "remote_model", None) or cfg.get("remote_model", ""),
        remote_timeout=int(cfg.get("remote_timeout", 60) or 60),
        backup=bool(cfg.get("backup", True)),
        # 默认开。--rebuild-local 显式关掉，用它把"保留本地时间轴"那条路排除掉。
        keep_local=not bool(getattr(a, "rebuild_local", False))
        and bool(cfg.get("keep_local", True)),
        deadline_s=int(cfg.get("deadline_s", 25) or 25),
        translate_deadline_s=int(cfg.get("translate_deadline_s", 180) or 180),
        limit=getattr(a, "limit", None),
        providers_enabled=cfg.get("providers_enabled") or None,
        providers_disabled=cfg.get("providers_disabled") or None,
    )


# ---------------------------------------------------------------- scan
def cmd_scan(a) -> int:
    if not os.path.isdir(a.root):
        print(f"打不开这个路径：{a.root}", file=sys.stderr)
        return 1
    songs = lib.scan(a.root, limit=a.limit)
    s = lib.summarize(songs)
    print(f"库根目录: {a.root}")
    print(f"音频文件: {s['total']} 首")
    print(f"  已有双语   : {s['bilingual']}")
    print(f"  单语/待双语: {s['mono_or_single']}")
    print(f"  没有歌词   : {s['missing']}")
    print(f"  乱码/损坏   : {s['broken']}")
    print(f"  纯音乐(zxx) : {s['instrumental']}")
    if s["by_top"]:
        print()
        print(f"{'目录':<12}{'歌曲':>6}{'有.lrc':>8}{'双语':>7}{'单语':>7}{'乱码':>7}{'缺歌词':>8}")
        for top, d in sorted(s["by_top"].items()):
            print(f"{top:<12}{d['audio']:>6}{d['lrc']:>8}{d['bilingual']:>7}"
                  f"{d['mono']:>7}{d['broken']:>7}{d['none']:>8}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"root": a.root, "summary": s}, fh, ensure_ascii=False, indent=1)
        print(f"\n已写出 JSON: {a.json}")
    return 0


# ---------------------------------------------------------------- run
def cmd_run(a) -> int:
    if not os.path.isdir(a.root):
        print(f"打不开这个路径：{a.root}", file=sys.stderr)
        return 1
    cfg = _config_from_args(a)
    # 先按 --only 筛，再截 --limit —— 顺序反了会空选（见 library.select 的说明）
    songs = lib.select(a.root, only=a.only, limit=a.limit)
    if not songs:
        print("没有要处理的歌", file=sys.stderr)
        return 1

    mode = "写入音乐库" if cfg.write else f"预览（输出到 {cfg.resolved_dump()}）"
    print(f"{len(songs)} 首 · {mode} · 翻译后端 {cfg.translate}")
    if not cfg.write:
        print("（加 --write 才会真的写文件；现在只生成到 dump 目录）")
    print()

    t0 = time.time()

    def on_event(ev):
        if ev.get("type") == "song":
            r = ev["result"]
            val = (f" 并行组={r['pairs']} 比例={r['validation']['pair_ratio']}"
                   if r.get("validation") else "")
            note = f" {r['note']}" if r.get("note") else ""
            print(f"[{ev['index']}/{ev['total']}] {r['status']:<16} "
                  f"{r.get('source', ''):<10} {r['file']}{val}{note} ({r['elapsed_s']}s)", flush=True)

    out = pl.run_batch(songs, cfg, on_event=on_event)
    stats = out["stats"]

    print()
    print("=== 汇总 ===")
    print(json.dumps(stats, ensure_ascii=False))
    print(f"总用时 {round(time.time() - t0, 1)}s")

    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump({"root": a.root, "stats": stats, "results": out["results"],
                       "elapsed_s": out["elapsed_s"]}, fh, ensure_ascii=False, indent=1)
        print(f"报告: {a.report}")
    if cfg.write:
        print("\n⚠ 飞牛不会重新读已入库歌曲的 .lrc（实测：扫描 / 改 mtime / 重写文件 / 移出再移回，全部无效）。")
        print("  它只在**歌曲首次入库时**读一次旁挂歌词。所以：")
        print("    · 新歌 —— 先补完再移进音乐库，天然生效")
        print("    · 已入库的歌 —— 没有零成本办法，见 docs/FAQ.md 的「飞牛里为什么没变化」")
    return 2 if stats.get("failed") else 0


# ---------------------------------------------------------------- watch（M1）
def cmd_watch(a) -> int:
    """监听一个目录，新歌落盘即自动补双语。前台常驻，Ctrl-C 优雅退出。"""
    from . import watcher

    cfg = store.load_config()
    wcfg = store.watch_config(cfg)

    root = a.root or wcfg["watch_root"]
    if not root:
        print("❌ 没有指定监听目录：加 --root，或先设置 watch_root", file=sys.stderr)
        return 1
    if not os.path.isdir(root):
        print(f"❌ 打不开这个路径：{root}", file=sys.stderr)
        return 1

    after = a.after or wcfg["watch_after"]
    move_to = a.move_to or wcfg["watch_move_to"]
    if after in ("copy", "move") and not move_to:
        print("❌ --after copy/move 必须同时给 --move-to", file=sys.stderr)
        return 1
    if move_to and move_to == root:
        print("❌ 监听目录和目标目录不能是同一个", file=sys.stderr)
        return 1

    # 监听模式默认直写旁挂（目标就是暂存目录）；--no-write 只 dump 预览
    do_write = not a.no_write
    quiet = a.quiet if a.quiet is not None else wcfg["watch_quiet_s"]
    poll = a.poll if a.poll is not None else wcfg["watch_poll_s"]

    base = _config_from_args(a)
    base.write = do_write
    if not do_write:
        base.dump_dir = a.dump or base.dump_dir

    dry = bool(a.dry_run)
    print(f"监听目录: {root}")
    print(f"  模式   : {'直写旁挂' if do_write else '只预览（不写库）'}"
          f"{'（dry-run：连预览都不落盘）' if dry else ''}")
    print(f"  搬运   : {after}" + (f" → {move_to}" if after in ('copy', 'move') else ""))
    print(f"  轮询 {poll:g}s · 静默 {quiet:g}s · 翻译后端 {base.translate}")
    if after in ("copy", "move") and dry:
        print("  （dry-run：只打印'会送到哪'，不动文件）")
    print("  Ctrl-C 退出。\n", flush=True)

    def on_event(ev):
        if ev.get("event") == "done":
            r = ev.get("result") or {}
            moved = r.get("moved_to") or r.get("copied_to")
            tail = f" → {moved}" if moved else ""
            note = f"  {r.get('note')}" if r.get("note") else ""
            print(f"  {r.get('status', ''):<16} {r.get('file', '')}{tail}{note}", flush=True)

    w = watcher.FolderWatcher(roots=[root], cfg=base, after=after, move_to=move_to,
                              quiet_s=quiet, poll_s=poll, on_event=on_event,
                              dry_run=dry, log=_log)
    try:
        w.start()
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)
        w.stop()
    return 0


# ---------------------------------------------------------------- report
def cmd_report(a) -> int:
    with open(a.frm, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data.get("results") or []
    if a.status:
        want = {s for s in a.status.split(",") if s}
        rows = [r for r in rows if r.get("status") in want]
    if a.csv:
        cols = ["file", "title", "artist", "status", "source", "backend", "pairs",
                "matched", "score", "expected_lang", "lyric_lang", "note", "elapsed_s"]
        with open(a.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"已写出 CSV: {a.csv}  ({len(rows)} 行)")
        return 0
    for r in rows:
        print(f"{r.get('status', ''):<16} {r.get('source', ''):<10} {r.get('file', '')}")
        if r.get("matched"):
            print(f"      匹配到: {r['matched']} (score={r.get('score')})")
        if r.get("note"):
            print(f"      {r['note']}")
    print(f"\n共 {len(rows)} 行")
    return 0


# ---------------------------------------------------------------- config / token
def cmd_config(a) -> int:
    if a.set:
        patch = {}
        for kv in a.set:
            if "=" not in kv:
                print(f"格式应为 key=value：{kv}", file=sys.stderr)
                return 1
            k, _, v = kv.partition("=")
            patch[k.strip()] = v.strip()
        store.save_config(patch)
    cfg = store.load_config()
    shown = dict(cfg)
    if shown.get("remote_api_key"):
        shown["remote_api_key"] = P.mask_key(shown["remote_api_key"])   # 只显示掩码
    print(json.dumps(shown, ensure_ascii=False, indent=1))
    print(f"\n配置文件: {store.config_path()}")
    print(f"数据目录: {store.data_dir()}")
    if store.api_key_locked():
        print("API key 由环境变量 FNLRC_REMOTE_API_KEY 提供（优先于配置文件）")
    return 0


def cmd_token(a) -> int:
    t = store.new_token() if a.rotate else store.get_token()
    if not t:
        print("还没有令牌，先生成：fnlrc token --rotate", file=sys.stderr)
        return 1
    print(t)
    print("\n这是网页版的写操作令牌，和 API key 一样敏感 —— 别贴进聊天、别提交到仓库。",
          file=sys.stderr)
    return 0


def cmd_serve(a) -> int:
    # 顶层 `--server` 用 a.open，子命令 `serve` 也用 --open（同一个 dest）
    return serve(a.host, a.port, bool(getattr(a, "open", False)))


# ---------------------------------------------------------------- provider（M2）
def _load_all_providers() -> None:
    """内置源 + 用户插件，全部注册进注册表。幂等。"""
    from .providers.builtin import register_builtins
    from .providers import loader

    register_builtins()
    loader.load_plugins(log=lambda m: print(m, file=sys.stderr))


def cmd_provider_list(a) -> int:
    from .providers import registry

    _load_all_providers()
    rows = []
    for p in registry.all_providers():
        try:
            ok, why = p.available()
        except Exception as e:
            ok, why = False, P.mask_secret(e)
        rows.append((p.name, getattr(p, "label", p.name), getattr(p, "priority", 999),
                     getattr(p, "needs_key", False), ok, why))

    print(f"{'NAME':<12}{'LABEL':<14}{'PRIORITY':>8}  {'NEEDS_KEY':<9}  AVAILABLE")
    for name, label, prio, needs_key, ok, why in rows:
        flag = "✅" if ok else f"❌ {why}"
        print(f"{name:<12}{label:<14}{prio:>8}  {'yes' if needs_key else 'no':<9}  {flag}")
    if not rows:
        print("（没有任何已注册的歌词源）")
    return 0


def cmd_provider_test(a) -> int:
    from .providers import base, registry

    _load_all_providers()
    p = registry.get(a.name)
    if p is None:
        print(f"没有这个源：{a.name}（可用：{', '.join(registry.names()) or '无'}）",
              file=sys.stderr)
        return 1

    if not os.path.isfile(a.file):
        print(f"打不开这个文件：{a.file}", file=sys.stderr)
        return 1

    ok, why = p.available()
    if not ok:
        print(f"源 {a.name} 当前不可用：{why}", file=sys.stderr)
        return 1

    root = os.path.dirname(a.file)
    song = lib.load_song(a.file, root)
    cfg = _config_from_args(a)
    print(f"测试源 {a.name}（{getattr(p, 'label', a.name)}）")
    print(f"  曲目：{song.title} / {song.artist}（预期语言 {song.expected_lang}）")

    dl = time.time() + cfg.deadline_s
    try:
        res = p.fetch(song, cfg, deadline=dl)
    except Exception as e:
        print(f"  ❌ 取词抛异常：{P.mask_secret(e)}", file=sys.stderr)
        return 1
    if res is None:
        print("  结果：这个源没有返回可用歌词（不等于失败，看源站是否有这首歌）")
        return 0

    problems = _check_contract(res)
    print(f"  匹配：{res.matched}（score={res.score}，语言 {res.lyric_lang}）")
    print(f"  归一化：{len(res.pairs)} 对，其中 {sum(1 for r in res.pairs if r[2])} 行有译文")
    if problems:
        print("  ❌ 契约未通过：")
        for msg in problems:
            print(f"     · {msg}")
        return 2
    print("  ✅ 契约通过")
    return 0


def _check_contract(res) -> list[str]:
    """对一次取词结果跑通用契约检查（`tests/test_provider_contract.py` 同源逻辑）。"""
    bad: list[str] = []
    if not res.matched:
        bad.append("matched 为空（用户无法核对配到了哪首）")
    if not isinstance(res.score, (int, float)):
        bad.append(f"score 不是数字：{res.score!r}")
    last = -1
    for row in res.pairs:
        if not (isinstance(row, tuple) and len(row) == 4):
            bad.append(f"pair 不是四元组：{row!r}")
            break
        ms, orig, trans, roman = row
        if not isinstance(ms, int):
            bad.append(f"毫秒不是整数：{ms!r}")
            break
        if ms < last:
            bad.append(f"毫秒未递增：{last} → {ms}")
            break
        last = ms
        if not orig:
            bad.append("存在空原文行")
            break
        if trans is not None and not isinstance(trans, str):
            bad.append(f"译文既不是 None 也不是字符串：{trans!r}")
            break
        if trans == "":
            bad.append("译文是空串（约定：没有译文用 None，不用空串）")
            break
    return bad


# ---------------------------------------------------------------- fnos
def cmd_fnos(a) -> int:
    """飞牛歌词库：探测 / 差异计划 / 原地回填。默认只读。"""
    from . import fnos
    app_data = a.app_data or a.data_dir
    appmeta = a.appmeta or a.data_dir

    if a.apply:
        if not a.music_root:
            print("❌ --apply 必须同时给 --music-root", file=sys.stderr)
            return 1
        out = fnos.apply_plan(app_data, appmeta, a.music_root,
                              allow_insert=bool(getattr(a, "allow_insert", False)))
        if "error" in out:
            print(f"❌ {out['error']}", file=sys.stderr)
            return 1
        if not out["changed"] and not out.get("inserted") and out.get("message"):
            print(f"ℹ {out['message']} —— 没有写任何东西。")
            return 0
        print(f"✅ 回填完成：{out['changed']} 首原地更新"
              f"，{out.get('inserted', 0)} 首新建歌词行（跳过 {out.get('skipped', 0)} 首）")
        print(f"   备份在：{out['backup_dir']}")
        for x in out.get("insert_report", [])[:10]:
            if x.get("ok"):
                print(f"     · 新建 {Path(x['file']).name} → {x['shard']} "
                      f"(stored_guid={x['stored_guid'][:12]}…)")
            else:
                print(f"     · ⚠ {x.get('file', '')}：{x['why']}")
        if out["failed"]:
            print(f"   ⚠ 有 {len(out['failed'])} 首失败：")
            for x in out["failed"][:10]:
                print(f"     · {Path(x['file']).name}：{x['why']}")
        print("\n如果飞牛里没立刻变化，重启一下飞牛音乐应用。")
        return 1 if out["failed"] else 0

    if a.music_root:
        pl = fnos.plan(app_data, appmeta, a.music_root)
        print(fnos.format_plan(pl))
        out = pl
    else:
        rep = fnos.probe(app_data, appmeta)
        print(fnos.format_report(rep))
        out = rep

    if getattr(a, "json", None):
        slim = {k: v for k, v in out.items() if k != "todo"}
        if "todo" in out:
            slim["todo"] = [{k: v for k, v in t.items()
                             if k not in ("new_content", "old_content")} for t in out["todo"]]
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(slim, fh, ensure_ascii=False, indent=1)
        print(f"\n已写出 JSON: {a.json}")
    return 0 if out.get("verdict", "ok") == "ok" else 1


# ---------------------------------------------------------------- serve
def serve(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = False) -> int:
    """起 HTTP 服务。默认只绑回环 —— 要对外必须显式传 --host 0.0.0.0。"""
    import uvicorn
    from .server import app

    token = store.get_token()
    if host not in ("127.0.0.1", "localhost"):
        print("⚠ 正在对外监听。任何能访问这个端口的人都能触发扫描和写入，所以要靠访问令牌保护。",
              file=sys.stderr)
        # 这一行的格式是**故意**的：文档让用户跑 `docker logs fnlrc | grep -i token`，
        # 而中文提示里没有 "token" 三个字母，如果令牌那行也不带，grep 就什么都抓不到。
        print(f"FNLRC_TOKEN={token}", file=sys.stderr)
        print("  浏览器首次打开会让你填它（从本机打开则会自动注入）。", file=sys.stderr)
        print("  API key 只存在服务端，浏览器拿不到。", file=sys.stderr)
    print(f"fnlrc {__version__}  http://{host}:{port}", file=sys.stderr)
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


# ---------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fnlrc", description="飞牛音乐歌词补完器")
    p.add_argument("--version", action="version", version=f"fnlrc {__version__}")
    p.add_argument("--server", action="store_true", help="起网页版服务")
    p.add_argument("--desktop", action="store_true", help="起桌面窗口（需要 pywebview）")
    p.add_argument("--host", default="127.0.0.1", help="--server 的监听地址（默认只绑本机）")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--open", action="store_true", help="--server 时自动打开浏览器")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("scan", help="库概览（不联网、不写文件）")
    s.add_argument("--root", required=True)
    s.add_argument("--limit", type=int)
    s.add_argument("--json")
    s.set_defaults(fn=cmd_scan)

    r = sub.add_parser("run", help="补完（默认预览，不写库）")
    r.add_argument("--root", required=True)
    r.add_argument("--write", action="store_true", help="真的写入音乐库（替换前备份 .bak）")
    r.add_argument("--dump", help="预览输出目录（默认 ./fnlrc_out）")
    r.add_argument("--force", action="store_true", help="已有合规双语也重做")
    r.add_argument("--rebuild-local", action="store_true",
                   help="外文单语歌词不要保留本地时间轴，按源站整份重抓")
    r.add_argument("--translate", choices=["source", "remote", "none"])
    r.add_argument("--remote-base", help="OpenAI 兼容端点，如 https://api.deepseek.com（DeepSeek 不要加 /v1）")
    r.add_argument("--remote-key", help="API key（也可用 FNLRC_REMOTE_API_KEY 环境变量）")
    r.add_argument("--remote-model", help="模型名，如 deepseek-flash")
    r.add_argument("--limit", type=int)
    r.add_argument("--only", help="只处理路径含该子串的歌")
    r.add_argument("--report", help="把结果写成 JSON 报告")
    r.set_defaults(fn=cmd_run)

    rep = sub.add_parser("report", help="读报告并筛选/导出")
    rep.add_argument("--from", dest="frm", required=True)
    rep.add_argument("--status")
    rep.add_argument("--csv")
    rep.set_defaults(fn=cmd_report)

    w = sub.add_parser("watch", help="监听目录，新歌落盘即自动补双语（前台常驻）")
    w.add_argument("--root", help="要监听的暂存目录（不填则用配置里的 watch_root）")
    w.add_argument("--after", choices=["keep", "copy", "move"],
                   help="处理完成后：keep=不动（默认）/ copy=复制到目标 / move=移动到目标")
    w.add_argument("--move-to", help="目标目录（--after copy/move 时必填）")
    w.add_argument("--no-write", action="store_true",
                   help="只生成到 dump 目录，不写音乐库（预览）")
    w.add_argument("--dump", help="--no-write 时的输出目录（默认 ./fnlrc_out）")
    w.add_argument("--quiet", type=float, help="落盘静默期秒数（默认 8）")
    w.add_argument("--poll", type=float, help="轮询间隔秒数（默认 5）")
    w.add_argument("--dry-run", action="store_true",
                   help="只打印会做什么，不写文件也不搬运（首次启用建议先跑）")
    w.add_argument("--force", action="store_true", help="已有合规双语也重做")
    w.add_argument("--translate", choices=["source", "remote", "none"])
    w.add_argument("--remote-base")
    w.add_argument("--remote-key")
    w.add_argument("--remote-model")
    w.add_argument("--rebuild-local", action="store_true",
                   help="外文单语歌词不保留本地时间轴，按源站整份重抓")
    w.set_defaults(fn=cmd_watch)

    c = sub.add_parser("config", help="查看/修改配置（不含 API key 明文）")
    c.add_argument("--set", action="append", metavar="K=V")
    c.set_defaults(fn=cmd_config)

    t = sub.add_parser("token", help="查看或重置网页版访问令牌")
    t.add_argument("--rotate", action="store_true", help="重新生成（旧的立即失效）")
    t.set_defaults(fn=cmd_token)

    f = sub.add_parser("fnos", help="飞牛歌词库：探测 / 差异计划 / 原地回填（默认只读）")
    f.add_argument("--app-data", help="主库 music.db 所在目录"
                                      "（默认 /usr/local/apps/@appdata/trim.music）")
    f.add_argument("--appmeta", help="歌词分片 lyric-sqlite/ 所在目录"
                                     "（默认 /vol2/@appmeta/trim.music）")
    f.add_argument("--data-dir", help="（旧参数）把它同时当作上面两个目录找")
    f.add_argument("--music-root", help="音乐库目录；给了才会对比磁盘上的 .lrc")
    f.add_argument("--allow-insert", action="store_true",
                   help="给**一行歌词都没有**的歌新建 lyric 行。默认不动它们 —— "
                        "已有歌词的歌永远不会被新建（偏好表指向旧 id，会静默无效）")
    f.add_argument("--apply", action="store_true", help="**真的写入**。不加则全程只读")
    f.add_argument("--json", help="把结果写成 JSON")
    f.set_defaults(fn=cmd_fnos)

    v = sub.add_parser("serve", help="起网页版服务")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8080)
    v.add_argument("--open", action="store_true")
    v.set_defaults(fn=cmd_serve)

    pr = sub.add_parser("provider", help="歌词源插件：列出 / 自检")
    prs = pr.add_subparsers(dest="pcmd")
    pl_ = prs.add_parser("list", help="列出所有歌词源（内置 + 插件）")
    pl_.set_defaults(fn=cmd_provider_list)
    pt = prs.add_parser("test", help="对某个源跑一次契约自检")
    pt.add_argument("name", help="源标识，如 netease / lrclib")
    pt.add_argument("--file", required=True, help="用来测试的音频文件路径")
    pt.set_defaults(fn=cmd_provider_test)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 输出的都是中文，还带 ✅ / ⚠ 这类符号。中文版 Windows 的控制台是 cp936，
    # 这些字符编码不了会直接抛 UnicodeEncodeError 把命令整个打断 ——
    # 宁可显示成 ? 也别崩。桌面/网页端不走这里，所以只影响 CLI。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass

    # 兼容 README 里的 `python -m fnlrc --cli scan ...` 写法
    if argv and argv[0] == "--cli":
        argv = argv[1:]
    a = build_parser().parse_args(argv)

    if getattr(a, "desktop", False):
        from . import desktop
        return desktop.run()
    if getattr(a, "server", False):
        return cmd_serve(a)
    fn = getattr(a, "fn", None)
    if fn is None:
        build_parser().print_help()
        return 1
    return fn(a)
