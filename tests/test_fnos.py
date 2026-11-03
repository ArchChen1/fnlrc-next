"""飞牛歌词库的探测 / 差异计划 / 回填。

**夹具一律照 2026-09 真机核实过的 schema 造**（见 `docs/TECH-NOTES.md` 2.5 与交付的
结构指纹 JSON）。这一点是硬要求：早先这个文件的夹具用的是一张**真机上根本不存在的
表结构**（把正文放在主库的 `lyric` 表、还带一个不存在的 `isLRC` 列），于是测试全绿
而脚本在真机上必然报「结构与预期不符」。

所以这里的 schema **刻意不含 `isLRC`**：真机全库搜过，没有这个列。谁要是再往写入
SQL 里加它，这里会直接报错。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fnlrc import fnos


# ---------------------------------------------------------------- 真机 schema
# 主库：audio_file / track / 歌词**索引**表 / 两张偏好表。**正文不在这里。**
MAIN_SCHEMA = """
CREATE TABLE audio_file (id INTEGER PRIMARY KEY, path TEXT, size INTEGER,
                         is_physical_file_deleted INTEGER DEFAULT 0);
CREATE TABLE track (id INTEGER PRIMARY KEY, guid TEXT, audio_file_id INTEGER,
                    title TEXT);
CREATE TABLE lyric (id INTEGER PRIMARY KEY, guid TEXT, track_id INTEGER,
                    source INTEGER, stored_guid TEXT,
                    created_at INTEGER, updated_at INTEGER);
CREATE TABLE user_track_lyric_preference (id INTEGER PRIMARY KEY, user_id INTEGER,
                    track_id INTEGER, lyric_id INTEGER,
                    created_at INTEGER, updated_at INTEGER);
CREATE TABLE user_track_lyric_offset (id INTEGER PRIMARY KEY, user_id INTEGER,
                    track_id INTEGER, lyric_id INTEGER, offset_ms INTEGER,
                    created_at INTEGER, updated_at INTEGER);
"""
# 分片：正文表叫 `lyric_content`，key 是 guid，正文是 **BLOB**。
SHARD_SCHEMA = """
CREATE TABLE lyric_content (guid TEXT PRIMARY KEY, content BLOB, created_at INTEGER);
"""

LRC_NEW = "[00:01.00]新しい歌詞\n[00:01.00]新译文\n" * 4
LRC_OLD = "old text\n"


def _mk(path: Path, schema: str, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    for sql, params in rows:
        conn.execute(sql, params)
    conn.commit()
    conn.close()


def guid_of(i: int) -> str:
    """分片编号 = stored_guid 的首个十六进制字符（两位零填充）。"""
    return ("0123456789abcdef"[i % 16] * 8) + f"{i:06d}"


def build(appdata: Path, appmeta: Path, music: Path, *, songs=6,
          lyrics_for=None, same_content=False, lrc_missing_for=(),
          dropped_shard_for=(), with_prefs=True) -> None:
    """造一个形状与真机一致的库，并在磁盘上放好 .lrc。

    `lyrics_for` 指定哪些下标的歌**有**歌词索引行（默认全部有）——
    没给的那些就是真机上"一行歌词都没有"的那一批。
    """
    for d in (appdata / "db", appmeta / "lyric-sqlite", music):
        d.mkdir(parents=True, exist_ok=True)
    has_lyric = set(range(songs)) if lyrics_for is None else set(lyrics_for)

    schema = MAIN_SCHEMA
    if not with_prefs:
        schema = schema.replace(
            "CREATE TABLE user_track_lyric_preference (id INTEGER PRIMARY KEY, user_id INTEGER,\n"
            "                    track_id INTEGER, lyric_id INTEGER,\n"
            "                    created_at INTEGER, updated_at INTEGER);", "")
        schema = schema.replace(
            "CREATE TABLE user_track_lyric_offset (id INTEGER PRIMARY KEY, user_id INTEGER,\n"
            "                    track_id INTEGER, lyric_id INTEGER, offset_ms INTEGER,\n"
            "                    created_at INTEGER, updated_at INTEGER);", "")
    main_rows: list[tuple] = []
    for i in range(songs):
        audio = music / f"song{i}.flac"
        audio.write_bytes(b"x")
        if i not in lrc_missing_for:
            (music / f"song{i}.lrc").write_text(
                LRC_OLD if same_content else LRC_NEW, encoding="utf-8")
        main_rows.append(("INSERT INTO audio_file (id, path) VALUES (?, ?)",
                          (i, audio.as_posix())))
    for i in range(songs):
        main_rows.append(
            ("INSERT INTO track (id, guid, audio_file_id, title) VALUES (?, ?, ?, ?)",
             (i, f"tguid{i:04d}", i, f"song{i}")))
    for i in sorted(has_lyric):
        main_rows.append(
            ("INSERT INTO lyric (id, guid, track_id, source, stored_guid, created_at, updated_at)"
             " VALUES (?, ?, ?, ?, ?, ?, ?)",
             (100 + i, f"lguid{i:04d}", i, 3, guid_of(i), 1, 1)))
    main_rows.append(("INSERT INTO user_track_lyric_preference (id, user_id, track_id, lyric_id)"
                      " VALUES (?, ?, ?, ?)", (1, 1, 0, 100)))
    _mk(appdata / "db" / "music.db", schema, main_rows)

    # 分片：建全部 16 个（真机就是 16 个），正文写进对应那个
    for n in range(16):
        shard = appmeta / "lyric-sqlite" / f"lyric-{n:02x}.db"
        rows = []
        for i in sorted(has_lyric):
            if guid_of(i)[0] == f"{n:x}" and n not in dropped_shard_for:
                rows.append(("INSERT INTO lyric_content (guid, content, created_at)"
                             " VALUES (?, ?, ?)", (guid_of(i), LRC_OLD.encode(), 1)))
        _mk(shard, SHARD_SCHEMA, rows)


@pytest.fixture
def lib(tmp_path):
    ad, am, music = tmp_path / "appdata", tmp_path / "appmeta", tmp_path / "音乐"
    build(ad, am, music)
    return ad, am, music


def _snapshot(*roots: Path) -> dict:
    out = {}
    for r in roots:
        out.update({p: p.read_bytes() for p in sorted(r.rglob("*.db"))})
    return out


# ---------------------------------------------------------------- 探测
def test_两处布局都要指对(lib):
    """真机的关键事实：主库和分片在两个目录、两个文件系统上。"""
    ad, am, _ = lib
    r = fnos.probe(str(ad), str(am))
    assert r["verdict"] == "ok"
    assert r["counts"]["track"] == 6
    assert r["counts"]["lyric"] == 6


def test_正文表是lyric_content而不是lyric(lib):
    """这是上一版最大的错：真机分片里的正文表叫 `lyric_content`。

    主库那张 `lyric` 是**索引**（没有正文列）。写错名字的后果不是"少拿点数据"，
    而是探测直接判「结构与预期不符」—— 真机上就是这么卡住的。
    """
    ad, am, _ = lib
    info = fnos.discover(ad, am)
    assert info.get("error") is None
    assert info["shard_table"] == "lyric_content"
    assert "content" in info["shard_cols"]


def test_不去主库找正文(lib):
    """主库 `lyric` 表只有索引列，不该被当成正文来源。"""
    ad, am, _ = lib
    info = fnos.discover(ad, am)
    assert "content" not in [c.lower() for c in info["main_cols"]["lyric"]]
    assert info["fields"]["lyric_track_fk"] == "track_id"
    assert info["fields"]["stored_guid"] == "stored_guid"


def test_全库没有isLRC列(lib):
    """真机全库搜过，**没有 `isLRC`**。夹具也因此不含它 ——

    所以只要写入路径里出现 `SET isLRC = 1`，这里就会以 sqlite3.OperationalError
    的形式暴露出来，而不是悄悄把一个虚构的列写进真机。
    """
    ad, am, _ = lib
    for db in [ad / "db" / "music.db", *sorted((am / "lyric-sqlite").glob("*.db"))]:
        conn = sqlite3.connect(db)
        cols = {r[1] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'") for r in
            conn.execute(f'PRAGMA table_info("{t[0]}")')}
        conn.close()
        assert not any("lrc" in c.lower() and c.lower() not in ("lyric_content",) for c in cols), \
            f"{db.name} 里出现了 isLRC 之类的列：{sorted(cols)}"


def test_分片名两位零填充():
    """`bc8…` → `lyric-0b.db`。漏掉零填充会错掉大部分（真机上错过 660 条）。"""
    assert fnos.shard_name_for("bc8abcdef") == "lyric-0b.db"
    assert fnos.shard_name_for("a12345678") == "lyric-0a.db"
    assert fnos.shard_name_for("0f9") == "lyric-00.db"
    assert fnos.shard_name_for("") is None
    assert fnos.shard_name_for("zzz") is None


def test_关联自检会把命中率打出来(lib):
    """`lyric.stored_guid` → 分片 → `lyric_content.guid` 这条链要自证。

    真机上有两种可能（用 stored_guid 还是 guid 去查正文），所以两个都测、都报，
    而不是我们悄悄猜一个。
    """
    ad, am, _ = lib
    r = fnos.probe(str(ad), str(am))
    lc = r["link_check"]
    assert lc["by_stored_guid"] == 1.0
    assert lc["sampled"] > 0
    assert any("关联自检" in n for n in lc["notes"])


def _missing(tmp_path, name="不存在"):
    """一个**明确指向空目录**的路径。

    刻意传显式路径而不是 `None`：`None` 会触发自动探测，那么在**真装了飞牛的机器上**
    它会找到真的 music.db，于是"断言找不到"的测试反而红了 —— 只在 CI 绿、
    在它要服务的机器上红，是最没用的那种测试。传显式路径才与环境无关。
    """
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return str(d)


def test_只给appmeta会明确告诉你要挂另一处(tmp_path):
    """这正是真机上踩到的坑：只挂 @appmeta，报错要能说清缺什么。"""
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music)
    r = fnos.probe(_missing(tmp_path), str(am))
    assert r["verdict"] == "not_found"
    text = fnos.format_report(r)
    assert "music.db" in text and "appdata" in text


def test_只给appdata也要提示缺分片(tmp_path):
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music)
    r = fnos.probe(str(ad), _missing(tmp_path))
    assert r["verdict"] == "not_found"
    assert "appmeta" in fnos.format_report(r)


def test_给了路径就以它为准不回退自动探测(tmp_path, monkeypatch):
    """显式指了一处路径，就不该跑去用另一处。

    用户说"主库在这里"，我们却因为这里没有 music.db 而改用别处的库去写 ——
    那比直接说"这里没有 music.db"危险得多。
    """
    real, am, music = tmp_path / "real", tmp_path / "m", tmp_path / "音乐"
    build(real, am, music)
    monkeypatch.setattr(fnos, "APP_DATA_CANDIDATES", (str(real),))
    monkeypatch.setattr(fnos.glob, "glob", lambda *a, **k: [])

    assert fnos.find_app_data(None) == real          # 没给 hint → 自动探测
    assert fnos.find_app_data(_missing(tmp_path)) is None   # 给了 hint → 只看它


def test_权限不足时给可读提示而不是崩溃(tmp_path, monkeypatch):
    """飞牛的 `@appdata` / `@appmeta` 是 770 root:root。

    非 root 用户 stat 一下就 PermissionError。而 `Path.is_file()` **只吞**
    ENOENT/ENOTDIR/ELOOP，EACCES 是重新抛出的 —— 不处理的话，用户在真机上
    看到的是 traceback，而不是文档承诺的那句"检查两处挂载"。
    """
    import errno

    def deny(self):
        raise PermissionError(errno.EACCES, "Permission denied", str(self))

    monkeypatch.setattr(fnos.Path, "is_file", deny)
    monkeypatch.setattr(fnos.Path, "is_dir", deny)
    monkeypatch.setattr(fnos.glob, "glob", lambda *a, **k: [])

    r = fnos.probe(None, None)                 # 不该抛
    assert r["verdict"] == "not_found"
    text = fnos.format_report(r)
    assert "music.db" in text and "lyric-sqlite" in text


def test_主库缺lyric索引表就判结构不符(tmp_path):
    """主库没有 lyric 表 → 停。别去分片里找替代。"""
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music)
    conn = sqlite3.connect(ad / "db" / "music.db")
    conn.execute("DROP TABLE lyric")
    conn.commit()
    conn.close()
    r = fnos.probe(str(ad), str(am))
    assert r["verdict"] == "unexpected_schema"
    assert "lyric" in r["error"]


def test_分片缺正文表就判结构不符(tmp_path):
    """分片里没有正文表 → 停。别拿主库的索引表顶替。"""
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music)
    for sh in (am / "lyric-sqlite").glob("*.db"):
        conn = sqlite3.connect(sh)
        conn.execute("DROP TABLE lyric_content")
        conn.commit()
        conn.close()
    r = fnos.probe(str(ad), str(am))
    assert r["verdict"] == "unexpected_schema"
    assert "正文表" in r["error"] or "lyric_content" in r["error"]


def test_探测报告回显真机的表名列名(lib):
    """判「结构不符」时要能照着输出定位到具体哪一行预期错了，而不是再猜一轮。"""
    ad, am, _ = lib
    text = fnos.format_report(fnos.probe(str(ad), str(am)))
    assert "分片正文表「lyric_content」的列" in text
    assert "stored_guid" in text and "content" in text
    assert "主库列：" in text


# ---------------------------------------------------------------- 差异计划
def test_计划里每条都注明匹配依据(lib):
    ad, am, music = lib
    pl = fnos.plan(str(ad), str(am), str(music))
    assert pl.get("error") is None
    assert len(pl["todo"]) == 6
    t = pl["todo"][0]
    assert t["track_id"] == 0 and t["lyric_id"] == 100
    assert t["stored_guid"] == guid_of(0)
    assert t["shard"] == "lyric-00.db"
    assert t["new_len"] == len(LRC_NEW)


def test_内容一致时跳过(tmp_path):
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music, same_content=True)   # 盘上与库里的正文一致
    pl = fnos.plan(str(ad), str(am), str(music))
    assert pl["todo"] == []
    assert pl["same"] == 6


def test_没有歌词行的单独列出且不动(tmp_path):
    """真机上 520 首里 142 首是这样 —— 这是**可新建**的情形，不是故障。"""
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music, lyrics_for=[0, 1, 2])
    pl = fnos.plan(str(ad), str(am), str(music))
    assert len(pl["todo"]) == 3
    assert len(pl["no_row"]) == 3
    assert not any(t.get("note") for t in pl["no_row"])
    assert "library" not in json.dumps(pl).lower()


def test_一个track对到多行歌词时不动它(tmp_path):
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music)
    conn = sqlite3.connect(ad / "db" / "music.db")
    conn.execute("INSERT INTO lyric (id, guid, track_id, source, stored_guid)"
                 " VALUES (999, 'x', 0, 4, ?)", (guid_of(0),))
    conn.commit()
    conn.close()
    pl = fnos.plan(str(ad), str(am), str(music))
    assert len(pl["multi"]) == 1
    assert all(t["track_id"] != 0 for t in pl["todo"])


# ---------------------------------------------------------------- 写入
def test_原地更新只换正文(lib):
    """保住歌单的关键：`lyric.id`、`stored_guid` 与 `source` 一律不动。"""
    ad, am, music = lib
    before = _snapshot(ad, am)
    out = fnos.apply_plan(str(ad), str(am), str(music))
    assert out["changed"] == 6 and not out["failed"]

    conn = sqlite3.connect(ad / "db" / "music.db")
    rows = conn.execute("SELECT id, guid, track_id, source, stored_guid FROM lyric"
                        " ORDER BY id").fetchall()
    conn.close()
    assert rows[0] == (100, "lguid0000", 0, 3, guid_of(0)), "索引行不该被动过"

    sh = am / "lyric-sqlite" / "lyric-00.db"
    conn = sqlite3.connect(sh)
    got = conn.execute("SELECT content FROM lyric_content WHERE guid = ?",
                       (guid_of(0),)).fetchone()[0]
    conn.close()
    assert fnos._text(got) == LRC_NEW
    assert before != _snapshot(ad, am)      # 确实写了东西


def test_正文以BLOB写入且能读回(lib):
    """真机上是 BLOB。写 str 进去应用不一定认，所以统一编码成 bytes 再写。"""
    ad, am, music = lib
    fnos.apply_plan(str(ad), str(am), str(music))
    conn = sqlite3.connect(am / "lyric-sqlite" / "lyric-00.db")
    row = conn.execute("SELECT content, typeof(content) FROM lyric_content WHERE guid = ?",
                       (guid_of(0),)).fetchone()
    conn.close()
    assert row[1] == "blob"
    assert bytes(row[0]).decode("utf-8") == LRC_NEW


def test_写入前会备份主库与分片(lib):
    ad, am, music = lib
    out = fnos.apply_plan(str(ad), str(am), str(music))
    b = Path(out["backup_dir"])
    assert (b / "music.db").is_file()
    assert (b / "lyric-00.db").is_file()
    assert (b / "old-content.tsv").is_file()
    assert "old text" in (b / "old-content.tsv").read_text(encoding="utf-8")


def test_回读校验能发现写了但没生效(tmp_path):
    """回读校验不是摆设。

    用触发器把每条 UPDATE 的内容**立刻改回旧值** —— UPDATE 本身成功、
    也不抛异常，只有回读对 sha256 才能发现"其实没生效"。
    没有这道校验，这种情况会静默记成成功。
    """
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music)
    for sh in (am / "lyric-sqlite").glob("*.db"):
        conn = sqlite3.connect(sh)
        conn.execute("CREATE TRIGGER revert AFTER UPDATE ON lyric_content "
                     "BEGIN UPDATE lyric_content SET content = OLD.content "
                     "WHERE guid = OLD.guid; END;")
        conn.commit()
        conn.close()

    out = fnos.apply_plan(str(ad), str(am), str(music))

    assert out["changed"] == 0
    assert len(out["failed"]) == 6
    assert all("回读不一致" in x["why"] for x in out["failed"])


# ---------------------------------------------------------------- 新建行
def test_给没有歌词的歌新建行(tmp_path):
    """真机实测通过的路：没有歌词行的歌可以新建，应用立刻接受。"""
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music, lyrics_for=[0, 1, 2])
    out = fnos.apply_plan(str(ad), str(am), str(music), allow_insert=True)

    assert out["changed"] == 3
    assert out["inserted"] == 3, out.get("insert_report")
    assert not out["failed"]

    conn = sqlite3.connect(ad / "db" / "music.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM lyric WHERE track_id >= 3").fetchall()
    conn.close()
    assert len(rows) == 3
    for r in rows:
        assert r["source"] == fnos.INSERT_SOURCE == 2      # 旁挂，不是 3（在线刮削）
        assert r["stored_guid"] and r["guid"]
        # 落点必须符合分片规则，否则应用找不到这条正文
        assert fnos.shard_name_for(r["stored_guid"]) is not None
        sh = am / "lyric-sqlite" / fnos.shard_name_for(r["stored_guid"])
        assert sh.is_file(), f"{sh.name} 不存在"
        c = sqlite3.connect(sh)
        got = c.execute("SELECT content FROM lyric_content WHERE guid = ?",
                        (r["stored_guid"],)).fetchone()
        c.close()
        assert got and "新しい歌詞" in fnos._text(got[0])


def test_默认不给没有歌词的歌新建行(tmp_path):
    """新建行要显式开启 —— 它会往另一个应用的库里插记录。"""
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music, lyrics_for=[0, 1, 2])
    out = fnos.apply_plan(str(ad), str(am), str(music))          # 没给 allow_insert
    assert out["changed"] == 3
    assert out.get("inserted", 0) == 0
    conn = sqlite3.connect(ad / "db" / "music.db")
    n = conn.execute("SELECT COUNT(*) FROM lyric").fetchone()[0]
    conn.close()
    assert n == 3, "不该多出索引行"


def test_有歌词的歌永远不会被新建行(tmp_path):
    """真机实测的坑：已有歌词时插新行，偏好表仍指向旧 id，**用户看不到新版本**。

    要修就得同时 upsert `user_track_lyric_preference`。我们不做这件事 ——
    对已有歌词的歌一律原地更新，从根上不碰这个坑。
    """
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music)
    out = fnos.apply_plan(str(ad), str(am), str(music), allow_insert=True)
    assert out.get("inserted", 0) == 0
    conn = sqlite3.connect(ad / "db" / "music.db")
    n = conn.execute("SELECT COUNT(*) FROM lyric").fetchone()[0]
    pref = conn.execute("SELECT lyric_id FROM user_track_lyric_preference WHERE track_id = 0"
                        ).fetchone()[0]
    conn.close()
    assert n == 6, "已有歌词的歌不该多出索引行"
    assert pref == 100, "偏好表不该被动过"


def test_学不出guid格式就拒绝新建(tmp_path):
    """宁可不动，也不要插一行格式不对的记录。"""
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music, lyrics_for=[])
    # 把已有的 guid 形态改乱 → 学不出稳定格式
    conn = sqlite3.connect(ad / "db" / "music.db")
    conn.execute("INSERT INTO lyric (id, guid, track_id, source, stored_guid)"
                 " VALUES (1, 'a', 0, 3, 'b')")
    conn.execute("INSERT INTO lyric (id, guid, track_id, source, stored_guid)"
                 " VALUES (2, ?, 0, 3, ?)", ("x" * 40, "y" * 41))
    conn.commit()
    conn.close()
    out = fnos.apply_plan(str(ad), str(am), str(music), allow_insert=True)
    assert out.get("inserted", 0) == 0
    assert any("学不出" in x.get("why", "") for x in out.get("insert_report", []))


# ---------------------------------------------------------------- 安全底线
def test_只读模式下不写一个字节(tmp_path):
    """plan() 必须真的只读 —— 这是整个工具的安全底线。"""
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music)
    before = _snapshot(ad, am)
    fnos.plan(str(ad), str(am), str(music))
    fnos.probe(str(ad), str(am))
    assert _snapshot(ad, am) == before


def test_不碰track和audio_file(lib):
    """动这两张表才会换 guid、才会丢歌单。"""
    ad, am, music = lib
    fnos.apply_plan(str(ad), str(am), str(music), allow_insert=True)
    conn = sqlite3.connect(ad / "db" / "music.db")
    t = conn.execute("SELECT id, guid, audio_file_id FROM track ORDER BY id").fetchall()
    a = conn.execute("SELECT id, path FROM audio_file ORDER BY id").fetchall()
    conn.close()
    assert t[0] == (0, "tguid0000", 0)
    assert a[0][0] == 0


def test_没有需要改的时候什么都不写(tmp_path):
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    build(ad, am, music, same_content=True)
    before = _snapshot(ad, am)
    out = fnos.apply_plan(str(ad), str(am), str(music))
    assert out["changed"] == 0
    assert "没有需要回填的" in out["message"]
    assert _snapshot(ad, am) == before
