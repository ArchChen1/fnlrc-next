"""监听文件夹（M1）。

重点钉住那些"错了会真丢文件"的行为：
- 半截文件不处理（静默期），且静默期内文件持续变化时**重新计时**
- 缺词件**原地不动**（搬走就永久缺词）
- 撞名时**新的上位**、旧文件退成 `.bak`，且不覆盖已有 `.bak`
- 跨盘/移动失败时**源文件不动**
- state 落盘：重启后已处理的文件不重跑（A3）
- 监听模式默认直写旁挂；`--no-write` / `dry_run` 只预览

`process_song` 走网，测试里 stub 掉 provider（与 test_pipeline 同法）。
"""
from __future__ import annotations

import json
import os
import time

import pytest

from fnlrc import library as lib
from fnlrc import pipeline as pl
from fnlrc import watcher as W


# ---------------------------------------------------------------- 夹具
def audio(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 64)
    return path


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", lambda *a, **k: None)
    monkeypatch.setattr(pl.P, "lrclib_fetch", lambda *a, **k: None)


def ne_pairs(n=8):
    return [((i + 1) * 10000, f"ああ サンプル{i}", f"中文译文{i}", None) for i in range(n)]


def stub_netease(pairs):
    def f(title, artist, duration, expected=None, deadline=None, debug=False):
        return {"source": "netease", "song_id": 1, "matched": "stub", "score": 8.0,
                "lyric_lang": "ja", "lang_mismatch": False, "pairs": pairs,
                "candidates": [], "alternatives": {}, "has_translation": True, "raw_lrc": ""}
    return f


def wcfg(**kw):
    kw.setdefault("write", True)
    return pl.Config(**kw)


def mk_watcher(tmp_path, root, **kw):
    kw.setdefault("cfg", wcfg())
    kw.setdefault("quiet_s", 0)          # 测试里默认不等静默期
    kw.setdefault("poll_s", 0.2)
    kw.setdefault("state_path_override", str(tmp_path / "watch-state.json"))
    return W.FolderWatcher(roots=[root], **kw)


# ---------------------------------------------------------------- 基本扫描/落盘
def test_新文件处理后写旁挂lrc(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root))
    # 第一轮：建立观察基线（还不处理）
    assert w.run_once() == []
    # 第二轮：稳定且未处理 → 处理
    results = w.run_once()
    assert len(results) == 1
    assert results[0]["status"] == "written"
    assert results[0]["trigger"] == "watch"
    assert (root / "song.lrc").is_file()


def test_临时后缀被忽略(tmp_path, offline):
    root = tmp_path / "inbox"
    audio(root / "song.flac.part")
    audio(root / "song.flac.!qb")
    w = mk_watcher(tmp_path, str(root))
    w.run_once()
    assert w.run_once() == []      # 两个都不该被当候选
    assert not (root / "song.lrc").exists()


def test_非音频扩展名被忽略(tmp_path, offline):
    root = tmp_path / "inbox"
    (root).mkdir(parents=True)
    (root / "notes.txt").write_text("hi")
    w = mk_watcher(tmp_path, str(root))
    w.run_once()
    assert w.run_once() == []


# ---------------------------------------------------------------- 静默期
def test_静默期内文件在变则重新计时(tmp_path, offline, monkeypatch):
    """半截文件的经典形态：先建 0 字节、再写入。不能在第一轮就处理。"""
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    f = root / "song.flac"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"")
    w = mk_watcher(tmp_path, str(root), quiet_s=10)   # 故意留长静默期

    # 三次观察：每次都改大小 → 永远重新计时，绝不处理
    for n in (1, 2, 3):
        f.write_bytes(b"x" * (n * 1000))
        assert w.run_once() == []
    assert not (root / "song.lrc").exists()


def test_稳定后才处理(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    f = audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root), quiet_s=0.3)
    w.run_once()                       # 观察基线（此时 mtime 已定）
    assert w.run_once() == []          # 还没过静默期
    time.sleep(0.35)
    got = w.run_once()
    assert len(got) == 1 and got[0]["status"] == "written"


# ---------------------------------------------------------------- 去重 / state
def test_同一文件不重复处理(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root))
    w.run_once()
    assert len(w.run_once()) == 1
    # 再来两轮都不该再处理（终态去重）
    assert w.run_once() == []
    assert w.run_once() == []


def test_state落盘后新实例不重跑(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    audio(root / "song.flac")
    w1 = mk_watcher(tmp_path, str(root))
    w1.run_once()
    assert len(w1.run_once()) == 1
    assert (tmp_path / "watch-state.json").is_file()

    # 模拟"重启"：同一 state 文件、全新实例
    w2 = mk_watcher(tmp_path, str(root))
    w2.run_once()                      # 建立观察基线
    assert w2.run_once() == []         # 已处理过，不再跑


def test_文件变了会重新处理(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    f = audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root))
    w.run_once()
    assert len(w.run_once()) == 1
    # 改动文件（mtime/size 变）→ 视为新内容，重跑
    time.sleep(0.01)
    f.write_bytes(b"\x00" * 128)
    w.run_once()                       # 重新建立基线
    got = w.run_once()
    assert len(got) == 1


# ---------------------------------------------------------------- 重试
def test_缺词件进重试并设retry_at(tmp_path, offline):
    """两个源都查不到 → no_lyrics，记录 attempts 与 retry_at，且**不搬运**。"""
    root = tmp_path / "inbox"
    dst = tmp_path / "lib"
    audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root), after="move", move_to=str(dst))
    w.run_once()
    got = w.run_once()
    assert got[0]["status"] == "no_lyrics"
    entry = w._done[str(root / "song.flac")]
    assert entry["attempts"] == 1
    assert entry["retry_at"] > time.time()
    assert not dst.exists()            # 缺词件留在原地


def test_重试次数用尽后不再处理(tmp_path, offline):
    root = tmp_path / "inbox"
    f = audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root))
    # 直接灌一个"已试满"的状态
    st = os.stat(f)
    w._done[str(f)] = {"size": st.st_size, "mtime": st.st_mtime,
                       "status": "no_lyrics", "ts": time.time(), "attempts": W.MAX_RETRY}
    w.run_once()
    assert w.run_once() == []


# ---------------------------------------------------------------- 搬运
def test_move搬运音频与lrc(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    dst = tmp_path / "lib"
    audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root), after="move", move_to=str(dst))
    w.run_once()
    got = w.run_once()
    assert got[0]["status"] == "written"
    assert (dst / "song.flac").is_file()
    assert (dst / "song.lrc").is_file()
    assert not (root / "song.flac").exists()          # move 搬走不留源
    assert got[0]["moved_to"].endswith("song.flac")


def test_copy搬运并保留源(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    dst = tmp_path / "lib"
    audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root), after="copy", move_to=str(dst))
    w.run_once()
    got = w.run_once()
    assert (dst / "song.flac").is_file()
    assert (root / "song.flac").is_file()             # copy 保留源
    assert got[0]["copied_to"].endswith("song.flac")


def test_keep模式不搬运(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    dst = tmp_path / "lib"
    audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root), after="keep", move_to=str(dst))
    w.run_once()
    w.run_once()
    assert (root / "song.lrc").is_file()
    assert not dst.exists()


def test_撞名时新的上位旧文件退bak(tmp_path):
    """直接测 place()：目标已有同名 → 旧的退成 .bak，新的用回原名。"""
    dst = tmp_path / "lib"
    dst.mkdir()
    old = dst / "song.flac"
    old.write_bytes(b"OLD")
    src = tmp_path / "song.flac"
    src.write_bytes(b"NEW")

    placed = W.place(str(src), str(dst), move=True)
    assert placed == str(dst / "song.flac")
    assert (dst / "song.flac").read_bytes() == b"NEW"
    assert (dst / "song.flac.bak").read_bytes() == b"OLD"


def test_撞名且bak已存在时加序号(tmp_path):
    dst = tmp_path / "lib"
    dst.mkdir()
    (dst / "song.flac").write_bytes(b"OLD1")
    (dst / "song.flac.bak").write_bytes(b"OLD0")     # 已有备份
    src = tmp_path / "song.flac"
    src.write_bytes(b"NEW")

    W.place(str(src), str(dst), move=True)
    assert (dst / "song.flac").read_bytes() == b"NEW"
    assert (dst / "song.flac.bak").read_bytes() == b"OLD0"     # 老备份没被动
    assert (dst / "song (1).flac.bak").read_bytes() == b"OLD1"  # 旧的退到 (1)


def test_跨盘移动失败时源文件不动(tmp_path, monkeypatch):
    """复制失败 → 源文件必须还在（先复制、后删源）。"""
    root = tmp_path / "inbox"
    src = audio(root / "song.flac")
    dst = tmp_path / "lib"

    import shutil
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(shutil, "copy2", boom)
    with pytest.raises(OSError):
        W.place(str(src), str(dst), move=True)
    assert src.is_file()          # 源文件在


# ---------------------------------------------------------------- dry-run / 预览
def test_dry_run不写文件不搬运(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    dst = tmp_path / "lib"
    audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root), after="move", move_to=str(dst), dry_run=True)
    w.run_once()
    got = w.run_once()
    assert got[0]["status"] == "dumped"        # 预览而非写入
    assert not (root / "song.lrc").exists()
    assert not dst.exists()
    assert not (tmp_path / "watch-state.json").exists()   # dry-run 不落 state


def test_no_write只预览不落库(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", stub_netease(ne_pairs()))
    root = tmp_path / "inbox"
    dump = tmp_path / "out"
    audio(root / "song.flac")
    w = mk_watcher(tmp_path, str(root),
                   cfg=wcfg(write=False, dump_dir=str(dump)))
    w.run_once()
    got = w.run_once()
    assert got[0]["status"] == "dumped"
    assert (dump / "song.lrc").is_file()
    assert not (root / "song.lrc").exists()


# ---------------------------------------------------------------- 幂等（已是合规双语）
def test_已是合规双语则跳过并搬运(tmp_path, offline):
    """skipped_existing 属于"本来就好"，可搬运。"""
    root = tmp_path / "inbox"
    dst = tmp_path / "lib"
    audio(root / "song.flac")
    # 写一个合规双语 .lrc（交错，同时间戳两行）
    lines = []
    for i in range(6):
        t = f"[00:{10 + i:02d}.00]"
        lines += [f"{t}orig {i}", f"{t}trans {i}"]
    (root / "song.lrc").write_text("\n".join(lines) + "\n", encoding="utf-8")

    w = mk_watcher(tmp_path, str(root), after="move", move_to=str(dst))
    w.run_once()
    got = w.run_once()
    assert got[0]["status"] == "skipped_existing"
    assert (dst / "song.lrc").is_file()


# ---------------------------------------------------------------- 配置
def test_watch_config默认值(tmp_path, monkeypatch):
    from fnlrc import store
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    wc = store.watch_config()
    assert wc["watch_after"] == "keep"
    assert wc["watch_write"] is True
    assert wc["watch_quiet_s"] == 8
    assert wc["watch_poll_s"] == 5
    assert wc["watch_autostart"] is False


def test_watch_config读取用户值(tmp_path, monkeypatch):
    from fnlrc import store
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    store.save_config({"watch_root": "/music/inbox", "watch_after": "move",
                       "watch_move_to": "/music/lib", "watch_quiet_s": 20})
    wc = store.watch_config()
    assert wc["watch_root"] == "/music/inbox"
    assert wc["watch_after"] == "move"
    assert wc["watch_quiet_s"] == 20


def test_after非法值回退keep(tmp_path):
    w = W.FolderWatcher(roots=[str(tmp_path)], cfg=wcfg(), after="bogus",
                        state_path_override=str(tmp_path / "s.json"))
    assert w.after == "keep"


# ---------------------------------------------------------------- 监听日志落盘（M6.6）
def test_日志默认保留48小时(tmp_path, monkeypatch):
    from fnlrc import store
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    assert store.watch_config()["watch_log_hours"] == 48
    assert W.DEFAULT_LOG_HOURS == 48
    assert W.log_retention_hours() == 48


def test_日志追加后能读回并落盘(tmp_path):
    p = tmp_path / "watch-log.jsonl"
    lg = W.WatchLog(hours=48, path=p)
    lg.append({"event": "done", "rel": "a.flac",
               "result": {"status": "written", "file": "a.flac", "note": "ok"}})
    lg.append({"event": "processing", "rel": "b.flac", "result": {}})
    assert lg.count() == 2
    assert p.is_file()
    tails = lg.tail()
    assert tails[-1]["name"] == "b.flac"
    assert tails[-2]["status"] == "written"
    assert tails[-2]["note"] == "ok"


def test_日志重启后能从磁盘恢复(tmp_path):
    p = tmp_path / "watch-log.jsonl"
    lg = W.WatchLog(hours=48, path=p)
    lg.append({"event": "done", "rel": "x.flac", "result": {"status": "written"}})
    # 新实例（模拟进程重启）读同一份文件
    lg2 = W.WatchLog(hours=48, path=p)
    assert lg2.count() == 1
    assert lg2.tail()[0]["name"] == "x.flac"


def test_日志超期被裁掉(tmp_path):
    p = tmp_path / "watch-log.jsonl"
    lg = W.WatchLog(hours=48, path=p)
    # 手工塞一条 49 小时前的记录
    old = {"ts": time.time() - 49 * 3600, "event": "done", "name": "old.flac"}
    p.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    lg2 = W.WatchLog(hours=48, path=p)   # 加载时即按窗口裁剪
    assert lg2.count() == 0


def test_日志保留0小时不保存(tmp_path):
    p = tmp_path / "watch-log.jsonl"
    lg = W.WatchLog(hours=0, path=p)
    lg.append({"event": "done", "rel": "a.flac", "result": {}})
    assert lg.count() == 0          # 服务端不保留
    assert not p.exists()           # 也不落盘


def test_日志reconfigure裁掉超出窗口的行(tmp_path):
    p = tmp_path / "watch-log.jsonl"
    lg = W.WatchLog(hours=48, path=p)
    lg.append({"event": "done", "rel": "a.flac", "result": {}})
    assert lg.count() == 1
    # 保留窗口改成 0 → 现有记录都超期
    lg.reconfigure(0)
    assert lg.count() == 0


def test_日志清空(tmp_path):
    lg = W.WatchLog(hours=48, path=tmp_path / "watch-log.jsonl")
    lg.append({"event": "done", "rel": "a.flac", "result": {}})
    lg.clear()
    assert lg.count() == 0
    assert lg.tail() == []


def test_日志tail受cap限制(tmp_path):
    lg = W.WatchLog(hours=48, path=tmp_path / "watch-log.jsonl", cap=5)
    for i in range(12):
        lg.append({"event": "done", "rel": f"{i}.flac", "result": {}})
    assert lg.count() == 5
    assert [r["name"] for r in lg.tail()] == ["7.flac", "8.flac", "9.flac",
                                              "10.flac", "11.flac"]
