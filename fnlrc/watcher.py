"""监听目录：新歌落盘即自动补双语（M1）。

**纯轮询**，不引入 watchdog（见 ADR-019）：

- 项目硬依赖只有 `requests` + `mutagen`；watchdog 是 C 扩展，NAS / ARM / 容器上可能装不上；
- 监听的通常只有一个暂存目录、文件量小，轮询开销可忽略；
- 轮询对 **SMB / 网络盘**比 inotify 一类更稳。

**零 UI 依赖**：只依赖标准库 + 本包的 `pipeline` / `library` / `store`；
CLI / Web / 桌面三端都通过这个类复用同一套逻辑。

复用而非重写：单曲处理**必须**调用 `pipeline.process_song(song, cfg)`，
绝不复制打分 / 校验 / 写入逻辑。

用法：

    w = FolderWatcher(roots=["/music/inbox"], cfg=pl.Config(write=True),
                      after="move", move_to="/music/library", on_event=cb)
    w.start()      # 阻塞常驻；Ctrl-C / w.stop() 退出

测试友好：`w.run_once()` 手动驱动一轮轮询。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import replace
from pathlib import Path

from . import library as lib
from . import pipeline as pl
from . import providers as P
from . import store

# 下载器写到一半的临时后缀。lib.AUDIO_EXT 已经挡掉绝大多数（`.part` 不在音频扩展名里），
# 这里再挡一层"最终名 + 临时后缀"的形态（如 `song.flac.part`）。
TEMP_SUFFIX = (".part", ".!ut", ".!qb", ".crdownload", ".tmp", ".filepart",
               ".download", ".partial", ".bts", ".aria2", ".opdownload")

# 处理完**可以搬运**的状态：已写出可用歌词 / 本来就是好的 / 纯音乐。
# 缺词件（no_lyrics / timeout）留在原地 —— 搬走了重试就找不到源文件（设计 2.6）。
MOVABLE = {"written", "written_keep_local", "written_mono", "dumped",
           "skipped_existing", "skip", "skip_instrumental"}
# 需要重试的状态：属于"暂时做不到"，退避后再试。
RETRYABLE = {"no_lyrics", "timeout", "write_failed", "error"}

# 退避：1min → 5min → 30min，最多 3 次。之后不再自动重试（文件改动会重新触发）。
RETRY_DELAYS = (60, 300, 1800)
MAX_RETRY = len(RETRY_DELAYS)


def state_path() -> Path:
    """重试 / 去重状态的落盘位置（A3：重启不重跑）。"""
    return store.data_dir() / "watch-state.json"


def has_temp_suffix(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(s) for s in TEMP_SUFFIX)


def _retire(path: str) -> None:
    """把已存在的目标文件退成备份（"新的上位"，A1 / 设计 2.6 规则 3）。

    `歌名.flac` → `歌名.flac.bak`；若该 `.bak` 已存在则 `歌名 (1).flac.bak`。
    先查再改名，避免覆盖上一条备份。
    """
    for cand in _retire_candidates(path):
        if not os.path.exists(cand):
            os.rename(path, cand)
            return
    # 理论上走不到（候选无限递增），保险起见抛错让调用方记录
    raise OSError(f"无法为 {path} 找到可用的备份名")


def _retire_candidates(path: str):
    yield path + ".bak"
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        yield f"{root} ({i}){ext}.bak"
        i += 1


def place(src: str, dst_dir: str, move: bool, dry_run: bool = False) -> str:
    """把 `src` 放到 `dst_dir` 下（保留文件名）。

    - 撞名：现有目标退成 `.bak`，**新文件用回原名**（A1）。
    - 原子性：**先复制成功、再删源**；中途失败则源文件不动（A5 / 设计规则 4）。
    - `dry_run` 只算出目标路径，不碰任何文件。

    返回目标路径。
    """
    name = os.path.basename(src)
    dst = os.path.join(dst_dir, name)
    if dry_run:
        return dst
    os.makedirs(dst_dir, exist_ok=True)
    if os.path.exists(dst):
        _retire(dst)
    shutil.copy2(src, dst)          # 先复制
    if move:
        os.remove(src)              # 复制成功后才删源
    return dst


class FolderWatcher:
    """监听一个或多个目录，把落盘的新音频交给 pipeline 处理。

    :param roots: 要监听的目录（界面先只暴露一个，能力支持多个）
    :param cfg: `pipeline.Config`（`write=True` 时直写旁挂 `.lrc`）
    :param after: `keep`（默认，不动） / `copy` / `move`
    :param move_to: `copy` / `move` 的目标目录
    :param quiet_s: 落盘静默期（连续这么久 size/mtime 不变才算写完）
    :param poll_s: 轮询间隔
    :param on_event: 事件回调，收 `{"type": "watch", "event": ..., "file": ..., "result": ...}`
    :param should_stop: 返回 True 则优雅停止
    :param dry_run: 只报告"会送到哪 / 会写什么"，不落盘、不搬运
    """

    def __init__(self, roots, cfg: pl.Config, after: str = "keep", move_to: str = "",
                 quiet_s: float = 8, poll_s: float = 5, on_event=None, should_stop=None,
                 state_path_override: str | None = None, dry_run: bool = False, log=None):
        self.roots = [r for r in roots if r]
        self.cfg = cfg
        self.after = after if after in ("keep", "copy", "move") else "keep"
        self.move_to = move_to or ""
        self.quiet_s = max(0.0, float(quiet_s))
        self.poll_s = max(0.2, float(poll_s))
        self.on_event = on_event
        self.should_stop = should_stop
        self.dry_run = dry_run
        self._log = log or (lambda *_: None)
        self._state_file = Path(state_path_override) if state_path_override else state_path()

        # path → ((size, mtime), 首次观察到该签名的时间)
        self._obs: dict[str, tuple[tuple[int, float], float]] = {}
        # path → {size, mtime, status, ts, attempts, retry_at}
        self._done: dict[str, dict] = {}
        self._stopped = False
        self._running = False
        # 本次运行内的统计（供 /api/watch/status 回显）
        self.counts: dict[str, int] = {}
        self._load_state()

    # ------------------------------------------------------------ 状态持久化
    def _load_state(self) -> None:
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(raw, dict):
            self._done = {k: v for k, v in raw.items() if isinstance(v, dict)}

    def _save_state(self) -> None:
        if self.dry_run:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(self._state_file) + ".tmp"
            Path(tmp).write_text(json.dumps(self._done, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
            os.replace(tmp, self._state_file)   # 原子替换，别留半个 JSON
        except Exception as e:
            self._log(f"watch 状态写入失败：{P.mask_secret(str(e))}")

    # ------------------------------------------------------------ 扫描
    def _scan(self) -> dict[str, tuple[int, float]]:
        """扫一遍所有监听目录 → {绝对路径: (size, mtime)}。"""
        out: dict[str, tuple[int, float]] = {}
        for root in self.roots:
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in lib.SKIP_DIRS and not d.startswith(".")]
                for fn in filenames:
                    if has_temp_suffix(fn):
                        continue
                    if os.path.splitext(fn)[1].lower() not in lib.AUDIO_EXT:
                        continue
                    full = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    out[full] = (st.st_size, st.st_mtime)
        return out

    def _sig(self, path: str) -> tuple[int, float] | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_size, st.st_mtime)

    def _root_of(self, path: str) -> str:
        for r in self.roots:
            if os.path.abspath(path).startswith(os.path.abspath(r)):
                return r
        return os.path.dirname(path)

    # ------------------------------------------------------------ 去重 / 重试判定
    def _is_handled(self, path: str, sig, now: float) -> bool:
        entry = self._done.get(path)
        if not entry:
            return False
        # 文件变了（size/mtime 变）→ 当作全新文件重来
        if entry.get("size") != sig[0] or entry.get("mtime") != sig[1]:
            return False
        status = entry.get("status", "")
        if status in RETRYABLE:
            if int(entry.get("attempts", 0)) >= MAX_RETRY:
                return True                       # 试够了，别再烦
            retry_at = entry.get("retry_at")
            if retry_at and now < float(retry_at):
                return True                       # 还在退避等待
            return False                          # 到点重试
        return True                               # 终态：不再处理

    # ------------------------------------------------------------ 单文件处理
    def on_file_ready(self, path: str) -> dict:
        """一个文件稳定落盘 → 处理它（并可选搬运）。返回结果字典。"""
        sig = self._sig(path)
        if sig is None:
            return {"file": path, "status": "error", "note": "文件已消失", "trigger": "watch"}
        entry = self._done.get(path) or {}
        attempts = int(entry.get("attempts", 0))

        self._emit("processing", path)
        result = self._process(path)
        status = result.get("status", "")

        # 搬运（只搬"成功 / 本来就好 / 纯音乐"，缺词件留原地重试）
        if (self.after in ("copy", "move") and self.move_to
                and status in MOVABLE and not self.dry_run):
            if os.path.abspath(self.move_to) != os.path.abspath(os.path.dirname(path)):
                try:
                    self._transfer(result, path)
                except Exception as e:
                    extra = f"搬运失败（源文件保留）：{P.mask_secret(str(e))}"
                    result["note"] = f"{result['note']}；{extra}" if result.get("note") else extra

        # 记录 / 更新去重状态
        rec = {"size": sig[0], "mtime": sig[1], "status": status, "ts": time.time()}
        if status in RETRYABLE:
            attempts += 1
            rec["attempts"] = attempts
            if attempts < MAX_RETRY:
                rec["retry_at"] = time.time() + RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
        self._done[path] = rec
        self.counts[status] = self.counts.get(status, 0) + 1

        self._emit("done", path, result)
        return result

    def _process(self, path: str) -> dict:
        song = lib.load_song(path, self._root_of(path))
        cfg = replace(self.cfg, write=False) if self.dry_run else self.cfg
        # 幂等（设计 2.4）：已经是合规双语 / 中文单语的，直接跳过，
        # 不要浪费一次联网。复用 pipeline 的判定，不另写一套。
        why = pl.should_skip_existing(song, cfg)
        if why:
            return {"file": song.rel, "title": song.title, "artist": song.artist,
                    "status": "skipped_existing", "note": why, "pairs": 0,
                    "source": "", "backend": "", "validation": None,
                    "keep_local": False, "expected_lang": song.expected_lang,
                    "elapsed_s": 0.0, "trigger": "watch", "path": path}
        try:
            res = pl.process_song(song, cfg, log=self._log)
        except Exception as e:
            res = {"file": song.rel, "title": song.title, "artist": song.artist,
                   "status": "error", "note": P.mask_secret(f"{type(e).__name__}: {e}"),
                   "pairs": 0, "source": "", "backend": "", "validation": None,
                   "keep_local": False, "expected_lang": song.expected_lang, "elapsed_s": 0.0}
        res["trigger"] = "watch"          # 报告里区分"手动跑"还是"监听触发"（设计 三）
        res["path"] = path
        return res

    def _transfer(self, result: dict, path: str) -> None:
        """把音频 + 同名 `.lrc` 一起送到目标目录。"""
        move = (self.after == "move")
        audio_dst = place(path, self.move_to, move)
        lrc = os.path.splitext(path)[0] + ".lrc"
        if os.path.isfile(lrc):
            place(lrc, self.move_to, move)
        result["moved_to" if move else "copied_to"] = audio_dst

    def _emit(self, event: str, path: str, result: dict | None = None) -> None:
        if not self.on_event:
            return
        rel = result.get("file") if result else os.path.basename(path)
        self.on_event({"type": "watch", "event": event,
                       "file": result.get("path") if result else path,
                       "rel": rel, "result": result})

    # ------------------------------------------------------------ 轮询主循环
    def run_once(self) -> list[dict]:
        """跑一轮轮询：找出已稳定的文件并处理之。测试可手动调用。"""
        now = time.time()
        files = self._scan()

        ready: list[str] = []
        for path, sig in files.items():
            prev = self._obs.get(path)
            if prev is None or prev[0] != sig:
                self._obs[path] = (sig, now)      # 命中（新文件或又有变化）→ 重新计时
                continue
            if now - prev[1] < self.quiet_s:
                continue                          # 还在静默期内
            if self._is_handled(path, sig, now):
                continue
            ready.append(path)

        # 清理已消失文件的观察记录，避免长跑时内存无界增长
        for gone in [p for p in list(self._obs) if p not in files]:
            self._obs.pop(gone, None)

        results: list[dict] = []
        for path in ready:
            if self.should_stop and self.should_stop():
                break
            try:
                results.append(self.on_file_ready(path))
            except Exception as e:
                self._log(f"watch 处理 {path} 出错：{P.mask_secret(str(e))}")
        if results:
            self._save_state()
        return results

    def start(self) -> None:
        """阻塞常驻（CLI）。Ctrl-C 时抛 KeyboardInterrupt，由调用方 stop()。"""
        self._running = True
        self._stopped = False
        self._log(f"开始监听 {', '.join(self.roots) or '(无目录)'}"
                  f" · 间隔 {self.poll_s:g}s · 静默 {self.quiet_s:g}s · 搬运 {self.after}")
        try:
            while not self._stopped:
                if self.should_stop and self.should_stop():
                    break
                try:
                    self.run_once()
                except Exception as e:
                    self._log(f"watch 轮询出错：{P.mask_secret(str(e))}")
                slept = 0.0
                while slept < self.poll_s and not self._stopped:
                    time.sleep(min(0.5, self.poll_s - slept))
                    slept += 0.5
        finally:
            self._running = False
            self._save_state()

    def stop(self) -> None:
        self._stopped = True

    # ------------------------------------------------------------ 状态回显
    @property
    def running(self) -> bool:
        return self._running

    def snapshot(self) -> dict:
        return {"running": self._running, "roots": list(self.roots),
                "after": self.after, "move_to": self.move_to,
                "poll_s": self.poll_s, "quiet_s": self.quiet_s, "dry_run": self.dry_run,
                "counts": dict(self.counts), "seen": len(self._obs),
                "handled": len(self._done)}
