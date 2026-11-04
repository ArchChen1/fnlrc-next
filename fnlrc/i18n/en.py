"""English strings —— 同时是 FALLBACK 目录，**键必须最全**。

键名与 `fnlrc/web/js/i18n/en.js` 保持一致。
`tests/test_i18n.py` 会检查其它语言没有多余的键、FALLBACK 没有缺失的键。
"""

STRINGS = {
    # ── 通用 ──────────────────────────────────────────────
    "app.title": "fnlrc · FNOS Lyrics Completer",
    "common.close": "Close ✕",
    "common.save": "Save",
    "common.cancel": "Cancel",
    "common.retry": "Retry",
    "common.done": "Done",
    "common.skip": "Skip",
    "common.yes": "yes",
    "common.no": "no",
    "common.none": "none",
    "common.unknown": "unknown",
    "common.empty": "Nothing here",

    # ── 状态（与后端 status 枚举一一对应）──────────────────
    "status.written": "✓ Bilingual",
    "status.written_keep_local": "✓ Bilingual · kept local timeline",
    "status.dumped": "✓ Preview",
    "status.written_mono": "⚠ Single language",
    "status.skipped_existing": "⊘ Skipped",
    "status.skip": "⊘ Skipped",
    "status.skip_instrumental": "⊘ Instrumental",
    "status.no_lyrics": "⚠ No lyrics found",
    "status.timeout": "⚠ Timed out",
    "status.lang_mismatch": "🔴 Needs review",
    "status.validate_failed": "🔴 Failed validation",
    "status.write_failed": "🔴 Write failed",
    "status.error": "🔴 Error",

    # ── 判定动作（preview 的 action）────────────────────────
    "action.create": "will create",
    "action.bilingualize": "will add translation",
    "action.rebuild": "will redo",
    "action.skip": "will skip",

    # ── 翻译后端 ──────────────────────────────────────────
    "backend.source": "Source lyrics",
    "backend.source.hint": (
        "Default. Uses only the Chinese translation shipped by the source site, "
        "calls no backend and sends nothing outbound; writes single-language "
        "lyrics when the source has no translation."
    ),
    "backend.remote": "AI translation",
    "backend.remote.hint": (
        "Uses your own OpenAI-compatible endpoint to translate the lines the source "
        "did not provide. Sends lyric text outbound."
    ),
    "backend.none": "No translation",
    "backend.none.hint": "Only fixes formatting and mojibake, generates no translation.",

    # ── 报告筛选 ──────────────────────────────────────────
    "filter.all": "All",
    "filter.written": "Written",
    "filter.written_mono": "Single language",
    "filter.no_lyrics": "No lyrics",
    "filter.timeout": "Timed out",
    "filter.lang_mismatch": "Needs review",
    "filter.failed": "Failed",

    # ── CLI: scan ─────────────────────────────────────────
    "cli.scan.cannot_open": "Cannot open this path: {root}",
    "cli.scan.root": "Library root: {root}",
    "cli.scan.total": "Audio files: {n}",
    "cli.scan.bilingual": "  Already bilingual : {n}",
    "cli.scan.mono": "  Single language   : {n}",
    "cli.scan.missing": "  No lyrics         : {n}",
    "cli.scan.broken": "  Broken / mojibake : {n}",
    "cli.scan.instrumental": "  Instrumental(zxx) : {n}",
    "cli.scan.table_header": "Dir         Songs   .lrc  Bilingual  Single  Broken  Missing",
    "cli.scan.wrote_json": "JSON written: {path}",

    # ── CLI: run ──────────────────────────────────────────
    "cli.run.no_songs": "No songs to process",
    "cli.run.header": "{n} songs · {mode} · backend {translate}",
    "cli.run.mode_write": "write into library",
    "cli.run.mode_preview": "preview (output to {dump})",
    "cli.run.preview_hint": "(add --write to actually write files; right now output goes to the dump dir)",
    "cli.run.summary": "=== Summary ===",
    "cli.run.elapsed": "Total {seconds}s",
    "cli.run.report": "Report: {path}",
    "cli.run.fnos_warning": (
        "\n⚠ FNOS does not re-read .lrc for songs already in its library "
        "(verified: rescanning / touching mtime / rewriting the file / moving it "
        "out and back all have no effect)."
    ),
    "cli.run.fnos_warning2": "  It only reads sidecar lyrics the first time a song is imported. So:",
    "cli.run.fnos_warning_new": "    · New songs — complete them before importing, works out of the box",
    "cli.run.fnos_warning_old": (
        "    · Already imported — no zero-cost fix, see docs/FAQ.md "
        "\"why nothing changed in FNOS\""
    ),

    # ── CLI: watch ────────────────────────────────────────
    "cli.watch.no_root": "❌ No watch directory: pass --root or set watch_root first",
    "cli.watch.cannot_open": "❌ Cannot open this path: {root}",
    "cli.watch.need_move_to": "❌ --after copy/move requires --move-to",
    "cli.watch.same_dir": "❌ Watch directory and target directory must differ",
    "cli.watch.header": "Watch directory: {root}",
    "cli.watch.mode_write": "direct write to sidecar",
    "cli.watch.mode_preview": "preview only (no writes)",
    "cli.watch.after": "  After done : {after}",
    "cli.watch.timing": "  Poll {poll}s · Quiet {quiet}s · backend {translate}",
    "cli.watch.dry_run": "  (dry-run: only prints where files would go, touches nothing)",
    "cli.watch.hint": "  Ctrl-C to quit.\n",
    "cli.watch.stopped": "\nStopped.",

    # ── CLI: report / config / token ───────────────────────
    "cli.report.csv": "CSV written: {path}  ({n} rows)",
    "cli.report.matched": "      Matched: {matched} (score={score})",
    "cli.report.rows": "\n{n} rows in total",
    "cli.config.bad_kv": "Expected key=value: {kv}",
    "cli.config.path": "\nConfig file: {path}",
    "cli.config.data_dir": "Data directory: {path}",
    "cli.config.env_key": "API key comes from FNLRC_REMOTE_API_KEY (takes priority over the config file)",
    "cli.token.missing": "No token yet, generate one: fnlrc token --rotate",
    "cli.token.warning": "\nThis is the write token for the web UI — as sensitive as an API key. Do not paste it into chats or commit it.",

    # ── CLI: translate（M3）─────────────────────────────────
    "cli.translate.nothing": "No lines need translating (all Chinese, or all already translated)",
    "cli.translate.exported": "Exported {files} songs, {lines} lines to translate → {path}",
    "cli.translate.export_hint": "After filling the translation column: fnlrc translate-import --from {path}",
    "cli.translate.cannot_open_file": "Cannot open this file: {path}",
    "cli.translate.cannot_open_root": "Cannot open this path: {root}",
    "cli.translate.no_song": "⚠ No matching song: {rel}",
    "cli.translate.write_failed": "❌ {rel}: cannot write {target}: {err}",
    "cli.translate.done": "{mark} {rel}: {n} translated lines → {target}",
    "cli.translate.memory": "(memory bank: {n} new entries)",
    "cli.translate.summary": "\n=== Summary === {ok} succeeded ({lines} lines), {bad} failed",
    "cli.translate.preview_note": "(preview mode: add --write to write into the library; right now output goes to the dump dir)",

    # ── CLI: provider（M2）──────────────────────────────────
    "cli.provider.header": "{name:<12}{label:<14}{priority:>8}  {needs_key:<9}  AVAILABLE",
    "cli.provider.none": "(no lyric sources registered)",
    "cli.provider.unknown": "No such source: {name} (available: {names})",
    "cli.provider.cannot_open": "Cannot open this file: {path}",
    "cli.provider.unavailable": "Source {name} is currently unavailable: {why}",
    "cli.provider.testing": "Testing source {name} ({label})",
    "cli.provider.song": "  Song: {title} / {artist} (expected language {lang})",
    "cli.provider.fetch_error": "  ❌ fetch raised: {err}",
    "cli.provider.no_result": "  Result: this source returned no usable lyrics (not necessarily a failure — check whether the site has the song)",
    "cli.provider.matched": "  Matched: {matched} (score={score}, language {lang})",
    "cli.provider.normalized": "  Normalized: {pairs} pairs, {translated} lines with translation",
    "cli.provider.contract_failed": "  ❌ Contract check failed:",
    "cli.provider.contract_ok": "  ✅ Contract check passed",

    # ── CLI: fnos ─────────────────────────────────────────
    "cli.fnos.need_music_root": "❌ --apply requires --music-root",
    "cli.fnos.noop": "ℹ {message} — nothing was written.",
    "cli.fnos.done": "✅ Backfill complete: {changed} songs updated in place",
    "cli.fnos.backup": "   Backed up to: {path}",
    "cli.fnos.inserted": "     · Created {file} → {shard}",
    "cli.fnos.insert_failed": "     · ⚠ {file}: {why}",
    "cli.fnos.failed_header": "   ⚠ {n} songs failed:",
    "cli.fnos.failed_line": "     · {file}: {why}",
    "cli.fnos.restart_hint": "\nIf FNOS does not change immediately, restart the FNOS music app.",
    "cli.fnos.wrote_json": "\nJSON written: {path}",

    # ── CLI: serve ────────────────────────────────────────
    "cli.serve.public_warning": "⚠ Listening on a public interface. Anyone who can reach this port can trigger scans and writes, so the access token is what protects you.",
    "cli.serve.token": "FNLRC_TOKEN={token}",
    "cli.serve.token_hint": "  The browser asks for it the first time (local access injects it automatically).",
    "cli.serve.key_hint": "  The API key lives only on the server; the browser never sees it.",
    "cli.serve.banner": "fnlrc {version}  http://{host}:{port}",

    # ── 人工翻译（M3）──────────────────────────────────────
    "manual.title": "Add translations",
    "manual.loading": "Loading…",
    "manual.no_lines": "This song has no editable lyric lines",
    "manual.time": "Time",
    "manual.original": "Original",
    "manual.translation": "Translation",
    "manual.placeholder": "Type the translation for this line…",
    "manual.note": (
        "Only translations are added — the timeline is never touched. "
        "Lines left blank stay single-language."
    ),
    "manual.draft_restored": "Restored {n} unsaved lines from your last draft.",
    "manual.save": "Save",
    "manual.discard": "Discard draft",
    "manual.draft_hint": "Unsaved translations are stored in your browser, so closing the page will not lose them.",
    "manual.memory": "Remember these translations (off by default)",
    "manual.saved": "Saved {n} translations",
    "manual.save_failed": "Save failed: {err}",
    "manual.rejected": "Validation failed, see the messages below",
    "manual.load_failed": "Load failed: {err}",
    "manual.no_lyrics": "No lyrics to align for this song",
    "manual.file_missing": "File not found",
    "manual.discarded": "Local draft discarded",

    # ── 设置面板 ──────────────────────────────────────────
    "settings.title": "Settings",
    "settings.language": "Language",
    "settings.language.hint": "Only affects the interface. Exported data is language-neutral.",
    "cli.arg.lang": "interface language",
    "cli.arg.server": "start the web UI server",
    "cli.arg.desktop": "start the desktop window (needs pywebview)",
    "cli.arg.host": "bind address for --server (loopback by default)",
    "cli.arg.open": "open the browser automatically with --server",
    "cli.cmd.scan": "library overview (no network, no writes)",
    "cli.cmd.run": "fill in lyrics (preview by default, no writes)",
    "cli.cmd.report": "read a report, filter and export it",
    "cli.cmd.watch": "watch a folder and fill in new files as they land (stays in foreground)",
    "cli.cmd.config": "show or change config (API keys stay masked)",
    "cli.cmd.token": "show or rotate the web UI access token",
    "cli.cmd.fnos": "fnOS lyric DB: probe / diff / fill in place (read-only by default)",
    "cli.cmd.serve": "start the web UI server",
    "cli.cmd.provider": "lyric source plugins: list / self-test",
    "cli.cmd.provider_list": "list every lyric source (built-in + plugins)",
    "cli.cmd.provider_test": "run a contract self-test against one source",
    "cli.cmd.te": "export rows missing a translation for manual fill-in (M3)",
    "cli.cmd.ti": "read back manual translations and build bilingual LRC (preview by default)",
    "cli.arg.write": "actually write into the library (backup .bak first)",
    "cli.arg.te_write": "suggest writing on import",
    "cli.arg.dump": "preview output folder (default ./fnlrc_out)",
    "cli.arg.force": "redo files that already have valid bilingual lyrics",
    "cli.arg.rebuild_local": "for foreign mono LRC, discard the local timeline and rebuild from the source",
    "cli.arg.remote_base": "OpenAI-compatible endpoint, e.g. https://api.deepseek.com (no /v1 for DeepSeek)",
    "cli.arg.remote_key": "API key (or set FNLRC_REMOTE_API_KEY)",
    "cli.arg.remote_model": "model name, e.g. deepseek-flash",
    "cli.arg.only": "only process songs whose path contains this substring",
    "cli.arg.report": "write the result as a JSON report",
    "cli.arg.watch_root": "folder to watch (defaults to watch_root in config)",
    "cli.arg.after": "after processing: keep (default) / copy / move",
    "cli.arg.move_to": "target folder (required with --after copy/move)",
    "cli.arg.no_write": "only write to the dump folder, never the library (preview)",
    "cli.arg.quiet": "quiet period in seconds after a file lands (default 8)",
    "cli.arg.poll": "poll interval in seconds (default 5)",
    "cli.arg.dry_run": "print what would happen without writing or moving anything (run this first)",
    "cli.arg.rotate": "regenerate (the old token stops working immediately)",
    "cli.arg.app_data": "folder holding music.db (default /usr/local/apps/@appdata/trim.music)",
    "cli.arg.appmeta": "folder holding lyric-sqlite/ (default /vol2/@appmeta/trim.music)",
    "cli.arg.data_dir": "(legacy) use this as both folders above",
    "cli.arg.music_root": "library folder; only then are on-disk .lrc files compared",
    "cli.arg.allow_insert": "create lyric rows for songs that have none. Off by default: songs that already have lyrics are never re-created (the preference table points at the old id, so it would silently do nothing)",
    "cli.arg.apply": "actually write. Without it everything stays read-only",
    "cli.arg.json": "write the result as JSON",
    "cli.arg.provider_name": "source id, e.g. netease / lrclib",
    "cli.arg.file": "path to an audio file to test with",
    "cli.arg.te_out": "output file (.csv by default, .json for tooling)",
    "cli.arg.te_format": "inferred from the extension by default",
    "cli.arg.context": "include one line of context before and after (off by default)",
    "cli.arg.all": "also include songs that are already mono (default: only rows missing a translation)",
    "cli.arg.ti_from": "file produced by translate-export",
    "cli.arg.ti_root": "music library root",
    "cli.arg.no_backup": "do not create .bak when writing",
    "cli.arg.memory": "store these translations in the memory bank (off by default; reuses same-language→Chinese across songs)",
}
