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
from . import i18n
from . import library as lib
from . import pipeline as pl
from . import providers as P
from . import store
from .i18n import t


def _log(msg: str) -> None:
    print(msg, flush=True)


def _config_from_args(a) -> pl.Config:
    cfg = store.load_config()
    return pl.Config(
        write=bool(getattr(a, "write", False)),
        dump_dir=getattr(a, "dump", None),
        force=bool(getattr(a, "force", False)),
        translate=getattr(a, "translate", None) or cfg.get("translate", "source"),
        translate_order=cfg.get("translate_order") or None,
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
        print(t("cli.scan.cannot_open", root=a.root), file=sys.stderr)
        return 1
    songs = lib.scan(a.root, limit=a.limit)
    s = lib.summarize(songs)
    print(t("cli.scan.root", root=a.root))
    print(t("cli.scan.total", n=s["total"]))
    print(t("cli.scan.bilingual", n=s["bilingual"]))
    print(t("cli.scan.mono", n=s["mono_or_single"]))
    print(t("cli.scan.missing", n=s["missing"]))
    print(t("cli.scan.broken", n=s["broken"]))
    print(t("cli.scan.instrumental", n=s["instrumental"]))
    if s["by_top"]:
        print()
        print(t("cli.scan.table_header"))
        for top, d in sorted(s["by_top"].items()):
            print(f"{top:<12}{d['audio']:>6}{d['lrc']:>8}{d['bilingual']:>7}"
                  f"{d['mono']:>7}{d['broken']:>7}{d['none']:>8}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"root": a.root, "summary": s}, fh, ensure_ascii=False, indent=1)
        print(t("cli.scan.wrote_json", path=a.json))
    return 0


# ---------------------------------------------------------------- run
def cmd_run(a) -> int:
    if not os.path.isdir(a.root):
        print(t("cli.scan.cannot_open", root=a.root), file=sys.stderr)
        return 1
    cfg = _config_from_args(a)
    # 先按 --only 筛，再截 --limit —— 顺序反了会空选（见 library.select 的说明）
    songs = lib.select(a.root, only=a.only, limit=a.limit)
    if not songs:
        print(t("cli.run.no_songs"), file=sys.stderr)
        return 1

    if cfg.write:
        mode = t("cli.run.mode_write")
    else:
        mode = t("cli.run.mode_preview", dump=cfg.resolved_dump())
    print(t("cli.run.header", n=len(songs), mode=mode, translate=cfg.translate))
    if not cfg.write:
        print(t("cli.run.preview_hint"))
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
    print(t("cli.run.summary"))
    print(json.dumps(stats, ensure_ascii=False))
    print(t("cli.run.elapsed", seconds=round(time.time() - t0, 1)))

    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump({"root": a.root, "stats": stats, "results": out["results"],
                       "elapsed_s": out["elapsed_s"]}, fh, ensure_ascii=False, indent=1)
        print(t("cli.run.report", path=a.report))
    if cfg.write:
        print(t("cli.run.fnos_warning"))
        print(t("cli.run.fnos_warning2"))
        print(t("cli.run.fnos_warning_new"))
        print(t("cli.run.fnos_warning_old"))
    return 2 if stats.get("failed") else 0


# ---------------------------------------------------------------- watch（M1）
def cmd_watch(a) -> int:
    """监听一个目录，新歌落盘即自动补双语。前台常驻，Ctrl-C 优雅退出。"""
    from . import watcher

    cfg = store.load_config()
    wcfg = store.watch_config(cfg)

    root = a.root or wcfg["watch_root"]
    if not root:
        print(t("cli.watch.no_root"), file=sys.stderr)
        return 1
    if not os.path.isdir(root):
        print(t("cli.watch.cannot_open", root=root), file=sys.stderr)
        return 1

    after = a.after or wcfg["watch_after"]
    move_to = a.move_to or wcfg["watch_move_to"]
    if after in ("copy", "move") and not move_to:
        print(t("cli.watch.need_move_to"), file=sys.stderr)
        return 1
    if move_to and move_to == root:
        print(t("cli.watch.same_dir"), file=sys.stderr)
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
    mode = t("cli.watch.mode_write") if do_write else t("cli.watch.mode_preview")
    print(t("cli.watch.header", root=root))
    print(f"  {mode}" + ("（dry-run：连预览都不落盘）" if dry else ""))
    print(t("cli.watch.after",
            after=after + (f" → {move_to}" if after in ('copy', 'move') else "")))
    print(t("cli.watch.timing", poll=f"{poll:g}", quiet=f"{quiet:g}",
            translate=base.translate))
    if after in ("copy", "move") and dry:
        print(t("cli.watch.dry_run"))
    print(t("cli.watch.hint"), flush=True)

    # 日志同样落盘（默认保留 48 小时），与服务端共用一份 watch-log.jsonl
    wlog = watcher.WatchLog(hours=watcher.log_retention_hours(
        store.load_config() if not dry else {"watch_log_hours": 0}))

    def on_event(ev):
        if not dry:
            wlog.append(ev)
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
        print(t("cli.watch.stopped"), file=sys.stderr)
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
        print(t("cli.report.csv", path=a.csv, n=len(rows)))
        return 0
    for r in rows:
        print(f"{r.get('status', ''):<16} {r.get('source', ''):<10} {r.get('file', '')}")
        if r.get("matched"):
            print(t("cli.report.matched", matched=r['matched'], score=r.get('score')))
        if r.get("note"):
            print(f"      {r['note']}")
    print(t("cli.report.rows", n=len(rows)))
    return 0


# ---------------------------------------------------------------- config / token
def cmd_config(a) -> int:
    if a.set:
        patch = {}
        for kv in a.set:
            if "=" not in kv:
                print(t("cli.config.bad_kv", kv=kv), file=sys.stderr)
                return 1
            k, _, v = kv.partition("=")
            patch[k.strip()] = v.strip()
        store.save_config(patch)
    cfg = store.load_config()
    shown = dict(cfg)
    if shown.get("remote_api_key"):
        shown["remote_api_key"] = P.mask_key(shown["remote_api_key"])   # 只显示掩码
    print(json.dumps(shown, ensure_ascii=False, indent=1))
    print(t("cli.config.path", path=store.config_path()))
    print(t("cli.config.data_dir", path=store.data_dir()))
    if store.api_key_locked():
        print(t("cli.config.env_key"))
    return 0


def cmd_token(a) -> int:
    # 注意：不能用 t 做变量名 —— 它会遮住从 i18n 导入的 t()
    tok = store.new_token() if a.rotate else store.get_token()
    if not tok:
        print(t("cli.token.missing"), file=sys.stderr)
        return 1
    print(tok)
    print(t("cli.token.warning"), file=sys.stderr)
    return 0


def cmd_serve(a) -> int:
    # 顶层 `--server` 用 a.open，子命令 `serve` 也用 --open（同一个 dest）
    return serve(a.host, a.port, bool(getattr(a, "open", False)))

# ---------------------------------------------------------------- 人工翻译（M3）
def cmd_translate_export(a) -> int:
    """导出缺译文行，供人工填写。默认 CSV（Excel 友好），--format json 给工具用。"""
    from . import manual

    if not os.path.isdir(a.root):
        print(t("cli.translate.cannot_open_root", root=a.root), file=sys.stderr)
        return 1
    songs = lib.select(a.root, only=a.only, limit=a.limit)
    if not songs:
        print(t("cli.run.no_songs"), file=sys.stderr)
        return 1

    def say(msg):
        print(msg, flush=True)

    meta = manual.export_files(songs, a.out, fmt=a.format, with_context=a.context,
                               only_missing=not a.all, root=a.root, log=say)
    if meta["lines"] == 0:
        print(t("cli.translate.nothing"), file=sys.stderr)
        return 0
    print(t("cli.translate.exported", files=meta['files'],
            lines=meta['lines'], path=meta['out']))
    print(t("cli.translate.export_hint", path=meta["out"])
          + (" --write" if a.write else "（预览）"))
    return 0


def cmd_translate_import(a) -> int:
    """读回人工填好的译文，按原时间戳合成为双语。默认不写库。"""
    from . import lrc as L
    from . import manual

    if not os.path.isfile(a.frm):
        print(t("cli.translate.cannot_open_file", path=a.frm), file=sys.stderr)
        return 1
    if not os.path.isdir(a.root):
        print(t("cli.translate.cannot_open_root", root=a.root), file=sys.stderr)
        return 1

    with open(a.frm, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    fmt = "json" if a.frm.lower().endswith(".json") else "csv"
    try:
        parsed = manual.parse(text, fmt=fmt)
    except manual.ImportError_ as e:
        for m in e.problems:
            print(f"❌ {m}", file=sys.stderr)
        return 1

    songs = {s.rel: s for s in lib.scan(a.root)}
    out_dir = a.dump or os.path.join(os.getcwd(), "fnlrc_out")
    ok = bad = wrote = 0
    results = []
    for rel, rows in parsed.items():
        song = songs.get(rel) or _song_by_rel(a.root, rel)
        if song is None:
            print(t("cli.translate.no_song", rel=rel), file=sys.stderr)
            bad += 1
            continue
        body, problems, n = manual.merge(song, rows)
        if problems:
            bad += 1
            for m in problems:
                print(f"❌ {rel}：{m}", file=sys.stderr)
            results.append({"file": rel, "ok": False, "problems": problems})
            continue
        # 落盘：--write 直写旁挂（先备份），否则进 dump 目录预览
        target = song.lrc_path if a.write else os.path.join(
            out_dir, os.path.splitext(rel)[0] + ".lrc")
        try:
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            if a.write and os.path.isfile(target) and not a.no_backup:
                bak = target + ".bak"
                if not os.path.isfile(bak):
                    os.replace(target, bak)
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        except Exception as e:
            bad += 1
            print(t("cli.translate.write_failed", rel=rel, target=target, err=e),
                  file=sys.stderr)
            results.append({"file": rel, "ok": False, "problems": [str(e)]})
            continue
        ok += 1
        wrote += n
        v = L.validate(body)
        results.append({"file": rel, "ok": True, "lines": n,
                        "validation": {"ok": v["ok"], "paired_groups": v["paired_groups"],
                                       "pair_ratio": v["pair_ratio"]},
                        "target": target})
        mark = "✓" if v["ok"] else "⚠"
        print(t("cli.translate.done", mark=mark, rel=rel, n=n, target=target)
              + ("" if v["ok"] else f"（并行组 {v['paired_groups']}，比例 {v['pair_ratio']}）"))

    # 记忆库（默认关）
    if a.memory and ok:
        pairs = []
        for rel, rows in parsed.items():
            for r in rows:
                if r["translation"]:
                    pairs.append((r["original"], r["translation"]))
        added = manual.remember(pairs)
        print(t("cli.translate.memory", n=added))

    print(t("cli.translate.summary", ok=ok, lines=wrote, bad=bad))
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump({"from": a.frm, "results": results}, fh, ensure_ascii=False, indent=1)
        print(t("cli.run.report", path=a.report))
    if not a.write and ok:
        print(t("cli.translate.preview_note"))
    return 1 if bad else 0


def _song_by_rel(root: str, rel: str):
    """按相对路径直接定位一首歌（scan 用的相对路径分隔符可能不同）。"""
    p = os.path.join(root, rel)
    if os.path.isfile(p):
        return lib.load_song(p, root)
    return None

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

    print(t("cli.provider.header", name="NAME", label="LABEL", priority="PRIORITY",
            needs_key="NEEDS_KEY"))
    for name, label, prio, needs_key, ok, why in rows:
        flag = "✅" if ok else f"❌ {why}"
        print(f"{name:<12}{label:<14}{prio:>8}  {'yes' if needs_key else 'no':<9}  {flag}")
    if not rows:
        print(t("cli.provider.none"))
    return 0


def cmd_provider_test(a) -> int:
    from .providers import base, registry

    _load_all_providers()
    p = registry.get(a.name)
    if p is None:
        names = ", ".join(registry.names()) or t("common.none")
        print(t("cli.provider.unknown", name=a.name, names=names), file=sys.stderr)
        return 1

    if not os.path.isfile(a.file):
        print(t("cli.provider.cannot_open", path=a.file), file=sys.stderr)
        return 1

    ok, why = p.available()
    if not ok:
        print(t("cli.provider.unavailable", name=a.name, why=why), file=sys.stderr)
        return 1

    root = os.path.dirname(a.file)
    song = lib.load_song(a.file, root)
    cfg = _config_from_args(a)
    print(t("cli.provider.testing", name=a.name, label=getattr(p, 'label', a.name)))
    print(t("cli.provider.song", title=song.title, artist=song.artist,
            lang=song.expected_lang))

    dl = time.time() + cfg.deadline_s
    try:
        res = p.fetch(song, cfg, deadline=dl)
    except Exception as e:
        print(t("cli.provider.fetch_error", err=P.mask_secret(e)), file=sys.stderr)
        return 1
    if res is None:
        print(t("cli.provider.no_result"))
        return 0

    problems = _check_contract(res)
    translated = sum(1 for r in res.pairs if r[2])
    print(t("cli.provider.matched", matched=res.matched, score=res.score,
            lang=res.lyric_lang))
    print(t("cli.provider.normalized", pairs=len(res.pairs), translated=translated))
    if problems:
        print(t("cli.provider.contract_failed"))
        for msg in problems:
            print(f"     · {msg}")
        return 2
    print(t("cli.provider.contract_ok"))
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
            print(t("cli.fnos.need_music_root"), file=sys.stderr)
            return 1
        out = fnos.apply_plan(app_data, appmeta, a.music_root,
                              allow_insert=bool(getattr(a, "allow_insert", False)))
        if "error" in out:
            print(f"❌ {out['error']}", file=sys.stderr)
            return 1
        if not out["changed"] and not out.get("inserted") and out.get("message"):
            print(t("cli.fnos.noop", message=out["message"]))
            return 0
        print(t("cli.fnos.done", changed=out['changed'])
              + f"，{out.get('inserted', 0)} 首新建歌词行（跳过 {out.get('skipped', 0)} 首）")
        print(t("cli.fnos.backup", path=out['backup_dir']))
        for x in out.get("insert_report", [])[:10]:
            if x.get("ok"):
                print(t("cli.fnos.inserted", file=Path(x['file']).name, shard=x['shard'])
                      + f" (stored_guid={x['stored_guid'][:12]}…)")
            else:
                print(t("cli.fnos.insert_failed", file=x.get('file', ''), why=x['why']))
        if out["failed"]:
            print(t("cli.fnos.failed_header", n=len(out['failed'])))
            for x in out["failed"][:10]:
                print(t("cli.fnos.failed_line", file=Path(x['file']).name, why=x['why']))
        print(t("cli.fnos.restart_hint"))
        return 1 if out["failed"] else 0

    if a.music_root:
        plan_ = fnos.plan(app_data, appmeta, a.music_root)
        print(fnos.format_plan(plan_))
        out = plan_
    else:
        rep = fnos.probe(app_data, appmeta)
        print(fnos.format_report(rep))
        out = rep

    if getattr(a, "json", None):
        slim = {k: v for k, v in out.items() if k != "todo"}
        if "todo" in out:
            slim["todo"] = [{k: v for k, v in item.items()
                             if k not in ("new_content", "old_content")}
                            for item in out["todo"]]
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(slim, fh, ensure_ascii=False, indent=1)
        print(t("cli.fnos.wrote_json", path=a.json))
    return 0 if out.get("verdict", "ok") == "ok" else 1


# ---------------------------------------------------------------- serve
def serve(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = False) -> int:
    """起 HTTP 服务。默认只绑回环 —— 要对外必须显式传 --host 0.0.0.0。"""
    import uvicorn
    from .server import app

    token = store.get_token()
    if host not in ("127.0.0.1", "localhost"):
        print(t("cli.serve.public_warning"), file=sys.stderr)
        # 这一行的格式是**故意**的：文档让用户跑 `docker logs fnlrc | grep -i token`，
        # 而中文提示里没有 "token" 三个字母，如果令牌那行也不带，grep 就什么都抓不到。
        print(t("cli.serve.token", token=token), file=sys.stderr)
        print(t("cli.serve.token_hint"), file=sys.stderr)
        print(t("cli.serve.key_hint"), file=sys.stderr)
    print(t("cli.serve.banner", version=__version__, host=host, port=port),
          file=sys.stderr)
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


# ---------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fnlrc", description=t("app.title"))
    p.add_argument("--version", action="version", version=f"fnlrc {__version__}")
    # 显式覆盖 > 环境变量 FNLRC_LANG > config.json 的 lang > 默认 zh
    p.add_argument("--lang", choices=i18n.available(),
                   help=f"{t('cli.arg.lang')} ({', '.join(i18n.available())})")
    p.add_argument("--server", action="store_true", help=t("cli.arg.server"))
    p.add_argument("--desktop", action="store_true", help=t("cli.arg.desktop"))
    p.add_argument("--host", default="127.0.0.1", help=t("cli.arg.host"))
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--open", action="store_true", help=t("cli.arg.open"))
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("scan", help=t("cli.cmd.scan"))
    s.add_argument("--root", required=True)
    s.add_argument("--limit", type=int)
    s.add_argument("--json")
    s.set_defaults(fn=cmd_scan)

    r = sub.add_parser("run", help=t("cli.cmd.run"))
    r.add_argument("--root", required=True)
    r.add_argument("--write", action="store_true", help=t("cli.arg.write"))
    r.add_argument("--dump", help=t("cli.arg.dump"))
    r.add_argument("--force", action="store_true", help=t("cli.arg.force"))
    r.add_argument("--rebuild-local", action="store_true",
                   help=t("cli.arg.rebuild_local"))
    r.add_argument("--translate", choices=["source", "remote", "none"])
    r.add_argument("--remote-base", help=t("cli.arg.remote_base"))
    r.add_argument("--remote-key", help=t("cli.arg.remote_key"))
    r.add_argument("--remote-model", help=t("cli.arg.remote_model"))
    r.add_argument("--limit", type=int)
    r.add_argument("--only", help=t("cli.arg.only"))
    r.add_argument("--report", help=t("cli.arg.report"))
    r.set_defaults(fn=cmd_run)

    rep = sub.add_parser("report", help=t("cli.cmd.report"))
    rep.add_argument("--from", dest="frm", required=True)
    rep.add_argument("--status")
    rep.add_argument("--csv")
    rep.set_defaults(fn=cmd_report)

    w = sub.add_parser("watch", help=t("cli.cmd.watch"))
    w.add_argument("--root", help=t("cli.arg.watch_root"))
    w.add_argument("--after", choices=["keep", "copy", "move"],
                   help=t("cli.arg.after"))
    w.add_argument("--move-to", help=t("cli.arg.move_to"))
    w.add_argument("--no-write", action="store_true",
                   help=t("cli.arg.no_write"))
    w.add_argument("--dump", help=t("cli.arg.dump"))
    w.add_argument("--quiet", type=float, help=t("cli.arg.quiet"))
    w.add_argument("--poll", type=float, help=t("cli.arg.poll"))
    w.add_argument("--dry-run", action="store_true",
                   help=t("cli.arg.dry_run"))
    w.add_argument("--force", action="store_true", help=t("cli.arg.force"))
    w.add_argument("--translate", choices=["source", "remote", "none"])
    w.add_argument("--remote-base")
    w.add_argument("--remote-key")
    w.add_argument("--remote-model")
    w.add_argument("--rebuild-local", action="store_true",
                   help=t("cli.arg.rebuild_local"))
    w.set_defaults(fn=cmd_watch)

    c = sub.add_parser("config", help=t("cli.cmd.config"))
    c.add_argument("--set", action="append", metavar="K=V")
    c.set_defaults(fn=cmd_config)

    tok = sub.add_parser("token", help=t("cli.cmd.token"))
    tok.add_argument("--rotate", action="store_true", help=t("cli.arg.rotate"))
    tok.set_defaults(fn=cmd_token)

    f = sub.add_parser("fnos", help=t("cli.cmd.fnos"))
    f.add_argument("--app-data", help=t("cli.arg.app_data"))
    f.add_argument("--appmeta", help=t("cli.arg.appmeta"))
    f.add_argument("--data-dir", help=t("cli.arg.data_dir"))
    f.add_argument("--music-root", help=t("cli.arg.music_root"))
    f.add_argument("--allow-insert", action="store_true",
                   help=t("cli.arg.allow_insert"))
    f.add_argument("--apply", action="store_true", help=t("cli.arg.apply"))
    f.add_argument("--json", help=t("cli.arg.json"))
    f.set_defaults(fn=cmd_fnos)

    v = sub.add_parser("serve", help=t("cli.cmd.serve"))
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8080)
    v.add_argument("--open", action="store_true")
    v.set_defaults(fn=cmd_serve)

    pr = sub.add_parser("provider", help=t("cli.cmd.provider"))
    prs = pr.add_subparsers(dest="pcmd")
    pl_ = prs.add_parser("list", help=t("cli.cmd.provider_list"))
    pl_.set_defaults(fn=cmd_provider_list)
    pt = prs.add_parser("test", help=t("cli.cmd.provider_test"))
    pt.add_argument("name", help=t("cli.arg.provider_name"))
    pt.add_argument("--file", required=True, help=t("cli.arg.file"))
    pt.set_defaults(fn=cmd_provider_test)

    te = sub.add_parser("translate-export", help=t("cli.cmd.te"))
    te.add_argument("--root", required=True)
    te.add_argument("--out", required=True, help=t("cli.arg.te_out"))
    te.add_argument("--format", choices=["csv", "json"], help=t("cli.arg.te_format"))
    te.add_argument("--context", action="store_true",
                    help=t("cli.arg.context"))
    te.add_argument("--all", action="store_true",
                    help=t("cli.arg.all"))
    te.add_argument("--only", help=t("cli.arg.only"))
    te.add_argument("--limit", type=int)
    te.add_argument("--write", action="store_true", help=t("cli.arg.te_write"))
    te.set_defaults(fn=cmd_translate_export)

    ti = sub.add_parser("translate-import", help=t("cli.cmd.ti"))
    ti.add_argument("--from", dest="frm", required=True, help=t("cli.arg.ti_from"))
    ti.add_argument("--root", required=True, help=t("cli.arg.ti_root"))
    ti.add_argument("--write", action="store_true", help=t("cli.arg.write"))
    ti.add_argument("--dump", help=t("cli.arg.dump"))
    ti.add_argument("--no-backup", action="store_true", help=t("cli.arg.no_backup"))
    ti.add_argument("--memory", action="store_true",
                    help=t("cli.arg.memory"))
    ti.add_argument("--report", help=t("cli.arg.report"))
    ti.set_defaults(fn=cmd_translate_import)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 输出里带 ✅ / ⚠ 这类符号。中文版 Windows 的控制台是 cp936，
    # 这些字符编码不了会直接抛 UnicodeEncodeError 把命令整个打断 ——
    # 宁可显示成 ? 也别崩。桌面/网页端不走这里，所以只影响 CLI。
    i18n._configure_console()

    # 兼容 README 里的 `python -m fnlrc --cli scan ...` 写法
    if argv and argv[0] == "--cli":
        argv = argv[1:]

    # 语言必须在 build_parser() 之前定下来 —— argparse 的 help=/子命令说明在
    # 构建时就取了 t()，晚一步就永远是默认语言。所以先扫一遍 argv 里有没有 --lang。
    i18n.set_lang(_prescan_lang(argv) or i18n.detect())

    a = build_parser().parse_args(argv)
    return _dispatch(a)


def _prescan_lang(argv: list[str]) -> str:
    """在 parse_args 之前把 --lang 抠出来。支持 `--lang en` 和 `--lang=en`。"""
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return ""


def _dispatch(a) -> int:
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
