"""飞牛音乐歌词库：探测、差异计划、原地回填 / 补建歌词行。

**这个文件只依赖 Python 标准库**（`sqlite3` + `pathlib`），所以可以单独拷到 NAS 上运行，
不需要安装 fnlrc、不需要 Docker：

    python3 fnos.py                                    # 探测（自动找路径）
    python3 fnos.py --music-root /vol2/1000/音乐       # 看清楚要改什么
    python3 fnos.py --music-root /vol2/1000/音乐 --apply         # 真的写（只改已有行）
    python3 fnos.py --music-root /vol2/1000/音乐 --allow-insert --apply   # 也给没歌词的歌建行

装了 fnlrc 的话两种用法等价：`python -m fnlrc fnos ...`

---

## 飞牛的数据分布在**两个**目录、**两张表**里（2026-09 真机核实）

```
/usr/local/apps/@appdata/trim.music/db/music.db      主库
    audio_file(id, path, …)   track(id, guid, audio_file_id, …)
    lyric(id, guid, track_id, source, stored_guid, created_at, updated_at)   ← 索引，**不含正文**
/vol2/@appmeta/trim.music/lyric-sqlite/lyric-<XX>.db 歌词分片，16 个
    lyric_content(guid PK, content BLOB, created_at)   ← 正文在这里
```

**关联链**（三段，缺一不可）：

```
audio_file.path  ←  track.audio_file_id      ← 选歌
track.id         ←  lyric.track_id           ← 找索引行
lyric.stored_guid → 分片文件名 →  lyric_content.guid ← 取/写正文
```

### 三个最容易写错的地方

1. **主库的 `lyric` 表里没有正文。** 正文在分片的 `lyric_content` 表。
2. **分片文件名是 `lyric-%02x.db`，`%02x = int(stored_guid[0], 16)`** ——
   **两位零填充**（`bc8…` → `lyric-0b.db`，不是 `lyric-b.db`）。
   真机上按这条统计 660/660 命中；漏掉零填充会错掉大部分。
3. **没有 `isLRC` 这个列。** 全库搜过，不存在。"是不是 LRC" 由应用自己判断。
   写库时不要试图去设它。

另外 `/var/apps/trim.music/var` 是前者的软链别名（指向 `@appdata/trim.music`）。
**历史上这个脚本以为"正文在主库的 lyric 表里"，那是错的。**

---

⚠️ 这里动的是**另一个应用的私有数据**。五条铁律：

1. **探测一律 `mode=ro`** —— 物理上写不进去。
2. **默认只读**：不加 `--apply` 就绝不写一个字节。
3. **写前必须能证明匹配是对的**。`--music-root` 那一步会打印"这首歌 → 匹配到哪一行 lyric、
   依据是什么"。匹配不上就**拒绝执行**，不猜。
4. **结构变了就拒绝**。飞牛一次升级就可能改表结构，猜着写会毁掉用户的库。
5. **给已有歌词的歌新建行 = 静默无效**。`user_track_lyric_preference` 以 `lyric_id` 为键，
   新增一行但不动偏好，应用仍然显示旧那版（真机实测）。所以新建行**只对"一行都没有"的歌**开放，
   且由 `--allow-insert` 显式开启。

为什么需要它：飞牛只在**歌曲首次入库时**读一次旁挂 `.lrc`，已入库的歌再怎么改文件都不生效
（实测见 `docs/TECH-NOTES.md` 2.3）。唯一能同时"让它生效"又"保住歌单"的办法，
是改它的歌词数据并**保留 `lyric_id`**。

背景与操作路径见 `docs/FNOS-SIDECAR.md`。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path

# 主库所在的目录（里面通常还有一层 db/）。按 2026-09 真机实测顺序排。
APP_DATA_CANDIDATES = (
    "/usr/local/apps/@appdata/trim.music",
    "/var/apps/trim.music/var",
    "/vol2/@appdata/trim.music",
    "/vol1/@appdata/trim.music",
)
# 歌词分片所在的目录
APPMETA_CANDIDATES = (
    "/vol2/@appmeta/trim.music",
    "/vol1/@appmeta/trim.music",
    "/vol3/@appmeta/trim.music",
)

# 主库里必须有的表。`lyric` 只是索引 —— 正文不在这里，别再找错了。
EXPECT_MAIN_TABLES = ("audio_file", "track", "lyric")
# 偏好表：回填时靠"保留 lyric_id"来保住它们，所以先确认它们在不在
EXPECT_PREF_TABLES = ("user_track_lyric_preference", "user_track_lyric_offset")
# 分片里的正文表名（真机实测）。找不到时会退化为"按列名找带 content 的表"。
SHARD_CONTENT_TABLE = "lyric_content"

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".ape", ".wma", ".dsf", ".aac", ".opus"}

# 新建行时写进 `lyric.source` 的值。**不要写 3**：
# 1=内嵌 2=同目录旁挂 3=在线刮削 4=用户手动关联。
# 我们写进去的是"从旁挂 .lrc 来的内容"，所以语义上就是 2（旁挂）。
# 写成 3 会让"哪些是本地旁挂"的判断失真。
INSERT_SOURCE = 2


# ---------------------------------------------------------------- 打开
def _open_ro(path: Path) -> sqlite3.Connection:
    """**只读**打开。用 URI 的 mode=ro —— 这样即使代码写错了也写不进去。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _open_rw(path: Path) -> sqlite3.Connection:
    """可写打开。应用正在跑，所以要走 WAL 友好的参数并设 busy_timeout。"""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _text(v) -> str:
    """正文列是 **BLOB**，读出来可能是 bytes。统一成 str。"""
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return bytes(v).decode(enc)
            except UnicodeDecodeError:
                continue
        return bytes(v).decode("utf-8", "replace")
    return str(v)


# ---------------------------------------------------------------- 定位
def _is_file(p) -> bool:
    """`Path.is_file()` 在**权限不足时会抛 PermissionError**。

    pathlib 只吞掉 ENOENT / ENOTDIR / ELOOP 这几个 errno，EACCES / EPERM 是重新抛出的。
    而飞牛的 `@appdata` / `@appmeta` 正是 `770 root:root` —— 非 root 用户 stat 一下就炸，
    于是用户看到的是 traceback，而不是文档承诺的那句「没找到，检查两处挂载」。
    这里统一把"没权限"当成"没找到"：**看不见就等于没有**，然后由上层给可读提示。
    """
    try:
        return p.is_file()
    except OSError:
        return False


def _is_dir(p) -> bool:
    """同 `_is_file`，只是判目录。"""
    try:
        return p.is_dir()
    except OSError:
        return False


def _glob_dir(d, pattern: str) -> list:
    """`Path.glob` 同样会在权限不足时抛 —— 扫不到就当空。"""
    try:
        return sorted(d.glob(pattern))
    except OSError:
        return []


def find_app_data(hint: str | None = None) -> Path | None:
    """主库（music.db）所在的目录。

    **给了 hint 就以它为准，不再回退到自动探测。** 用户明确指了一处路径，
    我们却跑去用另一个目录写库，比直接说"这里没有 music.db"危险得多。
    """
    if hint:
        return Path(hint) if _music_db(Path(hint)) else None
    cands = list(APP_DATA_CANDIDATES) + sorted(glob.glob("/vol*/@appdata/trim.music"))
    for c in cands:
        if c and _music_db(Path(c)):
            return Path(c)
    return None


def find_appmeta(hint: str | None = None) -> Path | None:
    """歌词分片（lyric-sqlite/）所在的目录。hint 同样是权威的。"""
    if hint:
        p = Path(hint)
        return p if _is_dir(p / "lyric-sqlite") else None
    cands = list(APPMETA_CANDIDATES) + sorted(glob.glob("/vol*/@appmeta/trim.music"))
    for c in cands:
        if c and _is_dir(Path(c) / "lyric-sqlite"):
            return Path(c)
    return None


def _music_db(app_data: Path) -> Path | None:
    """主库可能有几层，都试一遍。"""
    for rel in ("db/music.db", "music.db", "var/db/music.db"):
        p = app_data / rel
        if _is_file(p):
            return p
    return None


def _list_shards(appmeta: Path) -> list[Path]:
    d = appmeta / "lyric-sqlite"
    return _glob_dir(d, "lyric-*.db") if _is_dir(d) else []


def shard_name_for(stored_guid: str) -> str | None:
    """`stored_guid` → 分片文件名。

    **两位零填充**：`int(stored_guid[0], 16)` 再 `%02x`。
    `bc8…` → `lyric-0b.db`。真机 660/660 命中，漏掉零填充会错掉大部分。
    """
    g = (stored_guid or "").strip().lower()
    if not g:
        return None
    try:
        return "lyric-%02x.db" % int(g[0], 16)
    except ValueError:
        return None


def _shard_for(shards: list[Path], stored_guid: str) -> Path | None:
    """按 `stored_guid` 定位分片文件。

    优先用 `shard_name_for` 的规范名；找不到时退回"最长匹配前缀" ——
    真机上见过一位的写法，不能假定只有一种。
    """
    want = shard_name_for(stored_guid)
    if want:
        for sh in shards:
            if sh.name.lower() == want:
                return sh
    g = (stored_guid or "").lower()
    best, best_len = None, 0
    for sh in shards:
        # lyric-a.db → "a"；lyric-0a.db → "0a"
        suffix = sh.stem.split("-", 1)[-1].lower()
        if suffix and g.startswith(suffix) and len(suffix) > best_len:
            best, best_len = sh, len(suffix)
    return best


# ---------------------------------------------------------------- 结构读取
def _tables(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """→ {表名: [列名]}。读 sqlite_master，不改任何东西。"""
    out: dict[str, list[str]] = {}
    for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        name = row["name"]
        try:
            out[name] = [r["name"] for r in conn.execute(f'PRAGMA table_info("{name}")')]
        except sqlite3.Error:
            out[name] = []
    return out


def _guess(cols: list[str], *needles: str) -> list[str]:
    """按名字模糊匹配列。飞牛不同版本列名可能不一样，所以不硬编码。"""
    return [c for c in cols if all(n in c.lower() for n in needles)]


def _pick_shard_content_table(tabs: dict[str, list[str]]) -> tuple[str, list[str]]:
    """在分片里找出**正文表**。

    真机上是 `lyric_content(guid, content, created_at)`。找不到同名表时退化为
    "名字含 lyric、且有一列含 content"的表 —— 但只认这一个候选，含混就报错，
    宁可停下来让操作者看一眼，也不要猜。
    """
    if SHARD_CONTENT_TABLE in tabs:
        return SHARD_CONTENT_TABLE, tabs[SHARD_CONTENT_TABLE]
    cands = [(t, c) for t, c in tabs.items()
             if "lyric" in t.lower() and any("content" in x.lower() for x in c)]
    if len(cands) == 1:
        return cands[0]
    return "", []


def discover(app_data: Path | None, appmeta: Path | None) -> dict:
    """发现表、列、分片的对应关系。**只读**。失败时返回 `error`。"""
    if app_data is None:
        return {"error": "没找到主库（music.db）。它在飞牛的 @appdata 目录下，"
                         "通常是 /usr/local/apps/@appdata/trim.music/db/music.db —— "
                         "容器要把这个目录也挂进来（只挂 @appmeta 是不够的）"}
    if appmeta is None:
        return {"error": "没找到歌词分片目录（lyric-sqlite/）。它在 @appmeta 下，"
                         "通常是 /vol2/@appmeta/trim.music"}

    music_db = _music_db(app_data)
    if music_db is None:
        return {"error": f"{app_data} 下没有 music.db"}

    shards = _list_shards(appmeta)
    if not shards:
        return {"error": f"{appmeta}/lyric-sqlite/ 下没有 lyric-*.db 分片"}

    conn = _open_ro(music_db)
    try:
        main_tables = _tables(conn)
        missing = [t for t in EXPECT_MAIN_TABLES if t not in main_tables]
        if missing:
            return {"error": f"主库里缺少预期的表 {missing}；实际有 {sorted(main_tables)}。"
                             f"（`lyric` 表是**索引**，正文不在里面，别去别处找正文）"}

        af_cols, tk_cols, ly_cols = (main_tables["audio_file"], main_tables["track"],
                                     main_tables["lyric"])
        fields = {
            "path": _guess(af_cols, "path") or _guess(af_cols, "file") or _guess(af_cols, "uri"),
            "af_pk": [c for c in af_cols if c.lower() == "id"],
            "track_pk": [c for c in tk_cols if c.lower() == "id"],
            "track_guid": _guess(tk_cols, "guid"),
            "af_fk": [c for c in tk_cols if "audio" in c.lower() and "id" in c.lower()],
            # 主库 lyric 表 = 索引。正文在分片。
            "lyric_pk": [c for c in ly_cols if c.lower() == "id"],
            "lyric_guid": [c for c in ly_cols if c.lower() == "guid"],
            # 分片是按 `stored_guid` 切的。表里同时有 `guid` 和 `stored_guid` 时，
            # 模糊匹配会取到列序靠前的那个 —— 先找更具体的名字。
            "stored_guid": _guess(ly_cols, "stored", "guid") or _guess(ly_cols, "guid"),
            "lyric_track_fk": [c for c in ly_cols if "track" in c.lower() and "id" in c.lower()],
            "source": _guess(ly_cols, "source"),
        }
    finally:
        conn.close()

    # 正文表从**第一个分片**里发现（真机上正文不在主库）
    shard_table, shard_cols = "", []
    for sh in shards:
        try:
            c = _open_ro(sh)
        except (sqlite3.Error, OSError):
            continue
        try:
            shard_table, shard_cols = _pick_shard_content_table(_tables(c))
            if shard_table:
                break
        finally:
            c.close()
    if not shard_table:
        return {"error": f"分片里找不到正文表（预期 `{SHARD_CONTENT_TABLE}`，"
                         f"含 guid + content 列）；首个分片里有 "
                         f"{sorted(_tables(_open_ro(shards[0])))}"}

    fields.update({
        "content": _guess(shard_cols, "content"),
        "content_key": _guess(shard_cols, "guid") or [c for c in shard_cols if c.lower() == "id"],
    })
    required = ("path", "track_guid", "lyric_track_fk", "stored_guid", "content", "content_key")
    bad = [k for k in required if not fields.get(k)]
    if bad:
        return {"error": f"关键列没找全：{bad}。主库 audio_file={af_cols} track={tk_cols} "
                         f"lyric={ly_cols}；分片 {shard_table}={shard_cols}"}

    af_fk = fields.get("af_fk") or ["audio_file_id"]
    return {
        "app_data": app_data, "appmeta": appmeta, "music_db": music_db,
        "shards": shards,
        "main_tables": main_tables,
        "fields": {k: (v[0] if isinstance(v, list) else v) for k, v in fields.items() if v},
        "af_fk": af_fk[0],
        "has_prefs": {t: bool(main_tables.get(t)) for t in EXPECT_PREF_TABLES},
        # 把真机上的表名和列原样带出来 —— 判「结构不符」时，这两行就是证据本身
        "shard_table": shard_table,
        "shard_cols": shard_cols,
        "main_cols": {"audio_file": af_cols, "track": tk_cols, "lyric": ly_cols},
    }


# ---------------------------------------------------------------- 探测报告
def probe(app_data: str | None = None, appmeta: str | None = None,
          sample: int = 20) -> dict:
    """只读探测，返回一份给人看的报告。**什么都不改。**"""
    report: dict = {"steps": [], "verdict": "not_found"}

    def step(msg: str) -> None:
        report["steps"].append(msg)

    ad = find_app_data(app_data)
    am = find_appmeta(appmeta)
    report["app_data"] = str(ad) if ad else None
    report["appmeta"] = str(am) if am else None

    if ad:
        step(f"✅ 主库目录：{ad}（{_music_db(ad)}）")
    else:
        step(f"❌ 没找到 music.db。试过这些位置：{list(APP_DATA_CANDIDATES)}")
        step("   真机上它在系统盘：/usr/local/apps/@appdata/trim.music/db/music.db")
        step("   ⚠️ 只挂 @appmeta 是不够的 —— 那个目录里没有 music.db")
    if am:
        step(f"✅ 歌词分片目录：{am}/lyric-sqlite/")
    else:
        step(f"❌ 没找到 lyric-sqlite/。试过这些位置：{list(APPMETA_CANDIDATES)}")

    if not (ad and am):
        report["verdict"] = "not_found"
        return report

    info = discover(ad, am)
    if "error" in info:
        report["error"] = info["error"]
        report["verdict"] = "unexpected_schema"
        step(f"❌ {info['error']}")
        return report

    f = info["fields"]
    step(f"主库共 {len(info['main_tables'])} 张表；歌词分片 {len(info['shards'])} 个")
    step("字段对应关系（按名字模糊匹配出来的）：")
    for k, v in info["fields"].items():
        step(f"  · {k} = {v}")
    for t, ok in info["has_prefs"].items():
        step(f"  · {t}：{'有' if ok else '⚠ 没有'}")
    # 原样打出真机上的表名列名。判「结构不符」时不必再猜，这几行就是依据。
    step(f"分片正文表「{info['shard_table']}」的列：{', '.join(info['shard_cols']) or '（读不到）'}")
    for t, cols in info["main_cols"].items():
        step(f"主库列：{t} = {', '.join(cols)}")

    conn = _open_ro(info["music_db"])
    try:
        report["counts"] = {}
        for t in ("audio_file", "track", "lyric"):
            try:
                n = conn.execute(f'SELECT COUNT(*) AS n FROM "{t}"').fetchone()["n"]
                report["counts"][t] = n
                step(f"  · {t}：{n} 行")
            except sqlite3.Error as e:
                step(f"  · {t}：读不出行数（{e}）")
        # 索引行 → 分片正文，两段关联各自的自检命中率
        report["link_check"] = _link_check(conn, info, sample)
    finally:
        conn.close()
    for line in report["link_check"]["notes"]:
        step(line)

    gcol = f["stored_guid"]
    report["shards"] = []
    total_rows = 0
    for sh in info["shards"]:
        item = {"file": sh.name, "rows": None, "guid_prefix_ok": None}
        try:
            c = _open_ro(sh)
            try:
                tabs = _tables(c)
                tbl, cols = _pick_shard_content_table(tabs)
                if not tbl:
                    item["note"] = f"没有正文表（有 {sorted(tabs)[:4]}…）"
                else:
                    item["rows"] = c.execute(
                        f'SELECT COUNT(*) AS n FROM "{tbl}"').fetchone()["n"]
                    total_rows += item["rows"] or 0
                    if item["rows"]:
                        kcol = (_guess(cols, "guid") or [c for c in cols if c.lower() == "id"])[0]
                        rows = c.execute(
                            f'SELECT "{kcol}" AS g FROM "{tbl}" '
                            f'WHERE "{kcol}" IS NOT NULL LIMIT ?', (sample,)).fetchall()
                        # 比的是**分片编号**，不是字符串前缀：guid 是 `b…`、
                        # 分片名是 `lyric-0b.db`（两位零填充）。直接 startswith("0b")
                        # 永远为假，会假报"前缀命中 0/N"。
                        ok = sum(1 for r in rows if shard_name_for(str(r["g"])) == sh.name)
                        item["guid_prefix_ok"] = f"{ok}/{len(rows)}"
            finally:
                c.close()
        except sqlite3.Error as e:
            item["note"] = f"打不开：{e}"
        report["shards"].append(item)
    report["lyric_rows"] = total_rows
    step(f"分片正文行数合计 {total_rows}（抽样验证编号规则）：")
    for item in report["shards"][:4]:
        step(f"  · {item['file']}：{item.get('rows')} 行，编号命中 {item.get('guid_prefix_ok')}"
             + (f"，{item['note']}" if item.get("note") else ""))
    if len(report["shards"]) > 4:
        step(f"  · …另外 {len(report['shards']) - 4} 个分片同理")

    report["verdict"] = "ok"
    step("✅ 结构探测通过")
    return report


def _link_check(conn: sqlite3.Connection, info: dict, sample: int) -> dict:
    """**自检**：主库 `lyric` 索引行能不能落到分片正文上。

    真机上的链路是 `lyric.stored_guid` → 分片文件 → `lyric_content.guid`。
    但"分片表的 key 列到底是 `stored_guid` 还是 `guid`"这件事，
    不同版本有可能不一样 —— 所以**两个都测一遍，把命中率打出来**，
    让操作者一眼看到哪个是对的，而不是我们悄悄猜一个。

    命中率明显偏低就说明假设错了：这时候**不要往下走**。
    """
    f, shards = info["fields"], info["shards"]
    notes: list[str] = []
    out = {"by_stored_guid": None, "by_guid": None, "sampled": 0, "notes": notes}

    cols = f'"{f["lyric_pk"]}" AS lid, "{f["stored_guid"]}" AS sg'
    cols += f', "{f["lyric_guid"]}" AS g' if f.get("lyric_guid") else ", NULL AS g"
    try:
        rows = conn.execute(f'SELECT {cols} FROM "lyric" LIMIT ?', (max(sample, 50),)).fetchall()
    except sqlite3.Error as e:
        notes.append(f"  ⚠ 关联自检读不出（{e}）")
        return out
    if not rows:
        notes.append("  ℹ 主库 lyric 表是空的 —— 这批歌一行歌词都没有")
        return out

    out["sampled"] = len(rows)
    hits = {"stored_guid": 0, "guid": 0}
    for r in rows:
        for which, key in (("stored_guid", r["sg"]), ("guid", r["g"])):
            if not key:
                continue
            sh = _shard_for(shards, str(key))
            if sh is None:
                continue
            try:
                c = _open_ro(sh)
            except sqlite3.Error:
                continue
            try:
                tbl, scols = _pick_shard_content_table(_tables(c))
                if not tbl:
                    continue
                kcol = (_guess(scols, "guid") or [x for x in scols if x.lower() == "id"])[0]
                if c.execute(f'SELECT 1 FROM "{tbl}" WHERE "{kcol}" = ? LIMIT 1',
                             (str(key),)).fetchone():
                    hits[which] += 1
            finally:
                c.close()
    for which in ("stored_guid", "guid"):
        rate = hits[which] / len(rows)
        out[f"by_{which}"] = round(rate, 3)
        mark = "✅" if rate >= 0.98 else ("⚠" if rate > 0.2 else "❌")
        notes.append(f"  {mark} 关联自检：用 lyric.{which} 去分片查正文，"
                     f"命中 {hits[which]}/{len(rows)}（{rate:.0%}）")
    if (out["by_stored_guid"] or 0) < 0.98:
        notes.append("     ⚠️ 命中率不足 98%。分片的 key 列假设可能不对 —— "
                     "把上面几行原样发出来，不要往下走。")
    return out


def format_report(report: dict) -> str:
    lines = ["", "  飞牛数据位置："]
    lines += [f"  {s}" for s in report.get("steps", [])]
    lines += ["", {
        "ok": "结论：结构正常，可以做回填。",
        "not_found": "结论：没找到库。检查两个目录是不是都挂进来了 —— "
                     "主库在 @appdata（系统盘），分片在 @appmeta（存储池），是两处。",
        "unexpected_schema": "结论：结构与预期不符。**不要写入** —— 把这份报告贴出来，"
                             "先搞清飞牛改了什么。",
    }.get(report.get("verdict"), f"结论：{report.get('verdict')}")]
    return "\n".join(lines)


# ---------------------------------------------------------------- 差异计划
def _read_lrc(path: Path) -> str | None:
    """读旁挂歌词，并把换行统一成 `\\n`。

    归一化是必须的：Windows 上写出来的 `.lrc` 是 CRLF，原样写进库会影响
    "与库里一致" 的判断（同一个文件会被反复判成有差异），也不符合应用的存储格式。
    编码顺序与 `library.py` 保持同一套。
    """
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "big5", "utf-16"):
        try:
            raw = path.read_bytes().decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if raw is None:
        return None
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def _load_index(info: dict) -> dict[int, dict]:
    """track_id → 它的歌词索引行（含 stored_guid）。**只读。**

    一个 track 理论上可以有多条 lyric（多版本）。这里把多条的单独标出来，
    后面**不动**它们 —— 猜哪一条是"当前显示的"太危险。
    """
    f = info["fields"]
    conn = _open_ro(info["music_db"])
    try:
        cols = (f'"{f["lyric_pk"]}" AS lid, "{f["lyric_track_fk"]}" AS tid, '
                f'"{f["stored_guid"]}" AS sg')
        cols += f', "{f["source"]}" AS src' if f.get("source") else ", NULL AS src"
        out: dict[int, dict] = {}
        for r in conn.execute(f'SELECT {cols} FROM "lyric"'):
            tid = r["tid"]
            item = {"id": r["lid"], "stored_guid": str(r["sg"] or ""), "source": r["src"]}
            if tid in out:
                out[tid].setdefault("extra", []).append(item)
            else:
                out[tid] = item
        return out
    finally:
        conn.close()


def _shard_content(shards: list[Path], table: str, key_col: str,
                   stored_guid: str) -> tuple[str, Path | None]:
    """→ (正文文本, 命中的分片文件 | None)。分片没找到时第二个是 None。"""
    sh = _shard_for(shards, stored_guid)
    if sh is None:
        return "", None
    try:
        c = _open_ro(sh)
    except (sqlite3.Error, OSError):
        return "", None
    try:
        # 正文列名从行本身取（content / lyric_content / …），不硬编码
        row = c.execute(f'SELECT * FROM "{table}" WHERE "{key_col}" = ?',
                        (stored_guid,)).fetchone()
        if row is None:
            return "", sh
        ccol = next((k for k in row.keys() if "content" in k.lower()), None)
        return (_text(row[ccol]) if ccol else ""), sh
    except sqlite3.Error:
        return "", sh
    finally:
        c.close()


def plan(app_data: str | None, appmeta: str | None, music_root: str | None) -> dict:
    """算出"磁盘上的 .lrc"与"库里的歌词"有哪些不一致。**全程只读。**

    这一步的核心是**证明匹配是对的**：每条候选都会附带它凭什么匹配到那一行。
    匹配不上的会被明确列出来，不会被静默跳过。
    """
    ad, am = find_app_data(app_data), find_appmeta(appmeta)
    info = discover(ad, am)
    if "error" in info:
        return {"error": info["error"]}
    if not music_root:
        return {"error": "还需要 --music-root 指向音乐库，才能对比磁盘上的 .lrc"}
    mr = Path(music_root)
    if not _is_dir(mr):
        return {"error": f"打不开音乐库目录：{music_root}"}

    f = info["fields"]
    index = _load_index(info)
    table, shards = info["shard_table"], info["shards"]
    key_col = f["content_key"]

    conn = _open_ro(info["music_db"])
    try:
        sql = (f'SELECT a."{f["path"]}" AS path, t."{f["track_pk"]}" AS tid '
               f'FROM "track" t JOIN "audio_file" a '
               f'ON t."{info["af_fk"]}" = a."{f["af_pk"]}"')
        try:
            rows = conn.execute(sql).fetchall()
        except sqlite3.Error:
            # 外键列名猜错时退回按 rowid 对齐（两个表都是同序插入的）
            rows = conn.execute(
                f'SELECT a."{f["path"]}" AS path, t."{f["track_pk"]}" AS tid '
                f'FROM "track" t JOIN "audio_file" a ON t.rowid = a.rowid').fetchall()
    except sqlite3.Error as e:
        return {"error": f"连表查询失败：{e}。"}
    finally:
        conn.close()

    todo, same, no_lrc, no_row, multi = [], 0, 0, [], []
    for r in rows:
        tid = r["tid"]
        p = Path(str(r["path"]))
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        lrc = p.with_suffix(".lrc")
        if not _is_file(lrc):
            no_lrc += 1
            continue
        disk = _read_lrc(lrc)
        if disk is None:
            continue
        row = index.get(tid)
        if row is None:
            # 库里一行歌词都没有 —— 这是**可新建**的情形，不是故障
            no_row.append({"file": str(lrc), "track_id": tid, "path": str(p)})
            continue
        if row.get("extra"):
            multi.append({"file": str(lrc), "track_id": tid,
                          "rows": 1 + len(row["extra"])})
            continue
        cur, _sh = _shard_content(shards, table, key_col, row["stored_guid"])
        if not _sh:
            no_row.append({"file": str(lrc), "track_id": tid, "path": str(p),
                           "note": "索引行的 stored_guid 落不到任何分片"})
            continue
        if disk.strip() == cur.strip():
            same += 1
            continue
        todo.append({
            "file": str(lrc), "track_id": tid, "lyric_id": row["id"],
            "stored_guid": row["stored_guid"], "shard": _sh.name,
            "old_len": len(cur), "new_len": len(disk),
            "old_sha": hashlib.sha256(cur.encode()).hexdigest()[:12],
            "new_sha": hashlib.sha256(disk.encode()).hexdigest()[:12],
            "new_content": disk, "old_content": cur,
        })

    return {"app_data": str(ad), "appmeta": str(am), "music_root": str(mr), "fields": f,
            "shard_table": table, "shard_cols": info["shard_cols"],
            "main_cols": info["main_cols"],
            "index_rows": len(index),
            "total_tracks": len(rows), "todo": todo, "same": same,
            "no_lrc": no_lrc, "no_row": no_row, "multi": multi}


def format_plan(pl: dict) -> str:
    if "error" in pl:
        return f"❌ {pl['error']}"
    out = [f"音乐库：{pl['music_root']}",
           f"主库：  {pl['app_data']}", f"歌词分片：{pl['appmeta']}", ""]
    out.append(f"  库里共 {pl['total_tracks']} 首，其中 {pl['index_rows']} 行歌词索引")
    out.append(f"  · 磁盘上没有 .lrc：{pl['no_lrc']} 首（跳过）")
    out.append(f"  · 磁盘与库里一致：{pl['same']} 首（跳过）")
    out.append(f"  · **需要回填：{len(pl['todo'])} 首**（原地改正文，保留 lyric_id）")
    if pl["no_row"]:
        out.append(f"  · 库里**没有歌词行**、但磁盘有 .lrc：{len(pl['no_row'])} 首")
        out.append("    这一桶要分清两种情形 —— 它们在这一步都**不会**被动：")
        out.append("      ① 这首歌本来就没刮到过歌词（常态）→ 加 --allow-insert 可以给它们建行")
        out.append("      ② 索引行的 stored_guid 落不到任何分片 → 先搞清为什么，别建")
    if pl["multi"]:
        out.append(f"  · ⚠ 一个 track 对到多行歌词：{len(pl['multi'])} 首（不会动它们）")
    if pl["todo"]:
        out += ["", "前 10 条（每条都注明它凭什么匹配到那一行）："]
        for t in pl["todo"][:10]:
            out.append(f"  · {Path(t['file']).name}")
            out.append(f"      track.id={t['track_id']} → lyric.id={t['lyric_id']}"
                       f" → {t['shard']} 的 {pl['shard_table']}")
            out.append(f"      正文 {t['old_len']} 字（{t['old_sha']}）→ {t['new_len']} 字（{t['new_sha']}）")
        if len(pl["todo"]) > 10:
            out.append(f"  …另外 {len(pl['todo']) - 10} 条")
    if pl["no_row"]:
        out += ["", "库里没有歌词行的（前 5 条）："]
        for t in pl["no_row"][:5]:
            extra = f"  （{t['note']}）" if t.get("note") else ""
            out.append(f"  · {t['file']}  (track.id={t['track_id']}){extra}")
    out += ["", "确认上面每一条都匹配对了，再加 --apply 真正写入。" if pl["todo"] else "",
            "（这一步没有写任何东西）"]
    return "\n".join(out)


# ---------------------------------------------------------------- 写入
def _learn_guid_format(conn: sqlite3.Connection, lyric_table: str,
                       guid_col: str, stored_col: str) -> dict:
    """从**真实行**里学 guid / stored_guid 的字面格式，用来造新行。

    不硬编码长度和字符集 —— 飞牛换个版本就可能变。学不到就返回空，
    调用方会拒绝新建（宁可不动，也不要插一行格式不对的记录）。
    """
    try:
        rows = conn.execute(
            f'SELECT "{guid_col}" AS g, "{stored_col}" AS s FROM "{lyric_table}" '
            f'WHERE "{guid_col}" IS NOT NULL AND "{stored_col}" IS NOT NULL LIMIT 200'
        ).fetchall()
    except sqlite3.Error:
        return {}
    pats = []
    for r in rows:
        g, s = str(r["g"]), str(r["s"])
        if g and s:
            pats.append((len(g), len(s), s[:1].lower() == g[:1].lower()))
    if not pats:
        return {}
    # 只认"绝大多数行都一致"的形态；不一致就说明我们没看懂，不学
    glen = max(set(p[0] for p in pats), key=lambda x: sum(1 for p in pats if p[0] == x))
    slen = max(set(p[1] for p in pats), key=lambda x: sum(1 for p in pats if p[1] == x))
    same_first = sum(1 for p in pats if p[2]) / len(pats) >= 0.95
    conf = sum(1 for p in pats if p[0] == glen and p[1] == slen) / len(pats)
    if conf < 0.95:
        return {}
    return {"guid_len": glen, "stored_len": slen, "same_first": same_first, "n": len(pats)}


def _new_guid_pair(fmt: dict) -> tuple[str, str]:
    """按学到的格式造一对新 guid。`stored_guid` 决定落哪个分片。"""
    raw = uuid.uuid4().hex
    g = (raw * 4)[:fmt["guid_len"]]
    s = (raw[::-1] * 4)[:fmt["stored_len"]]
    if fmt.get("same_first"):
        s = g[0] + s[1:]
    return g, s


def apply_plan(app_data: str | None, appmeta: str | None, music_root: str | None,
               *, allow_insert: bool = False, backup_keep: int = 5) -> dict:
    """执行回填：备份 → 原地更新（可选：给没歌词的歌新建行）→ 回读校验。**会写东西。**

    - 对**已经有歌词行**的歌：`UPDATE <分片>.<正文表> SET content=? WHERE <key>=?`
      （只换正文，`lyric.id` 与 `stored_guid` 都不动 → 保住歌单与歌词偏移）
    - 对**一行都没有**的歌（要 `allow_insert=True`）：
      `INSERT lyric`（索引）+ `INSERT <分片>.<正文表>`（正文），`source=2`
    - **不碰 `source`** 已有行的 source（1=内嵌 2=旁挂 3=刮削 4=手动关联），改写它会让
      "哪些是本地旁挂"的判断失真
    - **绝不碰 `audio_file` / `track`** —— 那才会换 guid、才会丢歌单
    - **不给已有歌词行的歌建新行** —— 偏好表指向旧 id，用户会以为没生效（真机实测）
    """
    pl = plan(app_data, appmeta, music_root)
    if "error" in pl:
        return {"error": pl["error"]}
    todo = pl["todo"]
    inserts = pl["no_row"] if allow_insert else []
    # 有 note 的 no_row 说明是"stored_guid 落不到分片"，那是故障不是新建对象
    inserts = [t for t in inserts if not t.get("note")]
    if not todo and not inserts:
        return {"changed": 0, "inserted": 0, "skipped": 0,
                "message": "没有需要回填的（磁盘与库里已经一致）"}

    ad, am = Path(pl["app_data"]), Path(pl["appmeta"])
    f = pl["fields"]
    table = pl["shard_table"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # 备份放在 @appmeta 下（存储池），顺带把主库也复制一份过来 ——
    # 两者本来就在不同文件系统上，跨盘复制正好多一层保护。
    backup_dir = am / f"fnlrc-backup-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    music_db = _music_db(ad)
    if music_db and _is_file(music_db):
        shutil.copy2(music_db, backup_dir / "music.db")
    touched = {t["shard"] for t in todo}
    for shard in touched:
        src = am / "lyric-sqlite" / shard
        if _is_file(src):
            shutil.copy2(src, backup_dir / shard)
    # 旧正文也留一份纯文本，方便单曲粒度回滚
    with (backup_dir / "old-content.tsv").open("w", encoding="utf-8") as fh:
        for t in todo:
            fh.write(f"{t['lyric_id']}\t{t['file']}\t{t['old_sha']}\t"
                     f"{t['old_content'].replace(chr(10), chr(92) + 'n')}\n")

    changed, inserted, failed = 0, 0, []
    key_col, ccol = f["content_key"], f["content"]

    # ---- ① 原地更新（已有行）----
    by_shard: dict[str, list[dict]] = {}
    for t in todo:
        by_shard.setdefault(t["shard"], []).append(t)
    for shard_name, items in by_shard.items():
        shard = am / "lyric-sqlite" / shard_name
        conn = _open_rw(shard)
        try:
            upd = f'UPDATE "{table}" SET "{ccol}" = ? WHERE "{key_col}" = ?'
            for t in items:
                try:
                    with conn:                      # 短事务，逐条提交
                        conn.execute(upd, (t["new_content"].encode("utf-8"),
                                           t["stored_guid"]))
                    got = conn.execute(
                        f'SELECT "{ccol}" AS c FROM "{table}" WHERE "{key_col}" = ?',
                        (t["stored_guid"],)).fetchone()
                    want = hashlib.sha256(t["new_content"].encode()).hexdigest()[:12]
                    if hashlib.sha256(_text(got["c"]).encode()).hexdigest()[:12] == want:
                        changed += 1
                    else:
                        failed.append({**t, "why": "回读不一致（正文没写进去）"})
                except sqlite3.Error as e:
                    failed.append({**t, "why": f"{type(e).__name__}: {e}"})
        finally:
            conn.close()

    # ---- ② 新建行（没有歌词的歌）----
    insert_report: list[dict] = []
    if inserts:
        main = _open_rw(music_db)
        try:
            fmt = _learn_guid_format(main, "lyric", f["lyric_guid"], f["stored_guid"])
            if not fmt:
                insert_report.append({"why": "学不出 lyric 的 guid 格式（样本不足或形态不一致），"
                                             "拒绝新建 —— 宁可不动，也不要插一行格式不对的记录"})
            else:
                ly_cols = pl["main_cols"]["lyric"]
                for t in inserts:
                    disk = _read_lrc(Path(t["file"]))
                    if disk is None:
                        continue
                    g, sg = _new_guid_pair(fmt)
                    shard = am / "lyric-sqlite" / (shard_name_for(sg) or "")
                    if not _is_file(shard):
                        insert_report.append({"file": t["file"],
                                              "why": f"分片 {shard.name} 不存在"})
                        continue
                    try:
                        now = int(time.time())
                        cols = ["guid", "track_id", "source", "stored_guid"]
                        vals = [g, t["track_id"], INSERT_SOURCE, sg]
                        for opt in ("created_at", "updated_at"):
                            if opt in ly_cols:
                                cols.append(opt)
                                vals.append(now)
                        ph = ", ".join("?" * len(cols))
                        with main:
                            main.execute(
                                f'INSERT INTO "lyric" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                                f'VALUES ({ph})', vals)
                        sc = _open_rw(shard)
                        try:
                            with sc:
                                sc.execute(
                                    f'INSERT INTO "{table}" ("{key_col}", "{ccol}") VALUES (?, ?)',
                                    (sg, disk.encode("utf-8")))
                        finally:
                            sc.close()
                        # 回读：走应用会走的那条链
                        chk = _shard_content([shard], table, key_col, sg)[0]
                        if chk and chk.strip() == disk.strip():
                            inserted += 1
                            insert_report.append({"file": t["file"], "track_id": t["track_id"],
                                                  "lyric_guid": g, "stored_guid": sg,
                                                  "shard": shard.name, "ok": True})
                        else:
                            failed.append({"file": t["file"], "why": "新建后回读不一致"})
                    except sqlite3.Error as e:
                        failed.append({"file": t["file"], "why": f"{type(e).__name__}: {e}"})
        finally:
            main.close()

    # 新建行动过主库 → 补一份写后的主库进备份目录
    if inserted:
        try:
            shutil.copy2(music_db, backup_dir / "music.db.after-insert")
        except OSError:
            pass

    _prune_backups(am, backup_keep)
    return {"changed": changed, "inserted": inserted, "failed": failed,
            "insert_report": insert_report,
            "backup_dir": str(backup_dir),
            "skipped": pl["same"] + pl["no_lrc"]}


def _prune_backups(appmeta: Path, keep: int) -> None:
    """只保留最近几次备份 —— 每次都复制一份 music.db 会很快把盘吃满。"""
    for old in sorted(appmeta.glob("fnlrc-backup-*"), reverse=True)[keep:]:
        shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------- 单文件入口
def main(argv: list[str] | None = None) -> int:
    """让这个文件能**单独**运行（零依赖、不需要装 fnlrc）。"""
    # 输出的都是中文。NAS 上 stdout 本来就是 UTF-8；Windows 控制台/重定向可能不是，
    # 那里遇到编码不了的字会直接抛 UnicodeEncodeError —— 换成 replace，宁可显示成 ? 也别崩。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass

    ap = argparse.ArgumentParser(
        prog="fnos.py",
        description="飞牛音乐歌词库：探测 / 差异计划 / 原地回填。默认只读，什么都不改。")
    ap.add_argument("--app-data", help="主库 music.db 所在目录"
                                       "（默认 /usr/local/apps/@appdata/trim.music）")
    ap.add_argument("--appmeta", help="歌词分片 lyric-sqlite/ 所在目录"
                                      "（默认 /vol2/@appmeta/trim.music）")
    ap.add_argument("--data-dir", help="（旧参数）把它同时当作上面两个目录找")
    ap.add_argument("--music-root", help="音乐库目录；给了才会对比磁盘上的 .lrc")
    ap.add_argument("--allow-insert", action="store_true",
                    help="给**一行歌词都没有**的歌新建 lyric 行。默认不动它们。"
                         "已有歌词的歌永远不会被新建（偏好表指向旧 id，会静默无效）")
    ap.add_argument("--apply", action="store_true",
                    help="**真的写入**。不加的话全程只读。")
    ap.add_argument("--json", help="把结果写成 JSON")
    a = ap.parse_args(argv)

    import json
    app_data = a.app_data or a.data_dir
    appmeta = a.appmeta or a.data_dir

    if a.apply:
        if not a.music_root:
            print("❌ --apply 必须同时给 --music-root")
            return 1
        out = apply_plan(app_data, appmeta, a.music_root, allow_insert=a.allow_insert)
        if "error" in out:
            print(f"❌ {out['error']}")
            return 1
        if not out["changed"] and not out.get("inserted") and out.get("message"):
            print(f"ℹ {out['message']} —— 没有写任何东西。")
            return 0
        print(f"✅ 回填完成：{out['changed']} 首原地更新"
              f"，{out.get('inserted', 0)} 首新建歌词行（跳过 {out.get('skipped', 0)} 首）")
        print(f"   备份在：{out['backup_dir']}")
        if out.get("insert_report"):
            print("   新建明细：")
            for x in out["insert_report"][:10]:
                if x.get("ok"):
                    print(f"     · {Path(x['file']).name} → {x['shard']} "
                          f"(stored_guid={x['stored_guid'][:12]}…)")
                else:
                    print(f"     · ⚠ {x.get('file', '')}：{x['why']}")
        if out["failed"]:
            print(f"   ⚠ 有 {len(out['failed'])} 首失败：")
            for x in out["failed"][:10]:
                print(f"     · {Path(x['file']).name}：{x['why']}")
        print("\n如果飞牛里没立刻变化，重启一下飞牛音乐应用。")
    elif a.music_root:
        pl = plan(app_data, appmeta, a.music_root)
        print(format_plan(pl))
        out = pl
    else:
        rep = probe(app_data, appmeta)
        print(format_report(rep))
        out = rep

    if a.json:
        slim = {k: v for k, v in out.items() if k != "todo"}
        if "todo" in out:
            slim["todo"] = [{k: v for k, v in t.items()
                             if k not in ("new_content", "old_content")} for t in out["todo"]]
        Path(a.json).write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已写出 JSON: {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
