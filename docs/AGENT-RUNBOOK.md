# 给 AI 助手的操作手册：把歌词回填进飞牛

> 这份是写给**跑在用户服务器上的 AI 助手**（Hermes / Claude Code / 类似的 agent）看的。
> 人看的话请走 [GUIDE-WEB.md](GUIDE-WEB.md) 或 [GUIDE-DESKTOP.md](GUIDE-DESKTOP.md)。
>
> 如果你是人类用户在找"怎么让 AI 帮我做"，把这份文件连同用户的诉求一起交给它就行。

---

## 你的任务

用户在自己的飞牛 NAS 上跑过 `fnlrc`，把带中文翻译的双语 `.lrc` 写到了音乐文件旁边。
但**飞牛不会重新读已经入库歌曲的 `.lrc`**（它只在歌曲首次入库时读一次），所以歌词没生效。

你要做的是：把这些 `.lrc` 的内容写进飞牛的歌词数据库，**同时保住用户的歌单、收藏和播放历史**。

---

## 先记住三条铁律

1. **先只读，再确认，最后才写。** 不加 `--apply` 的命令绝不会写一个字节。不要在没看到差异清单之前执行写入。
2. **改歌词数据时保留 `lyric.id`。** 歌单、收藏、播放历史、歌词偏移都以 `lyric_id` 为外键，id 不变它们就都不受影响。已有歌词的歌**原地改正文**，不新建行。
3. **绝不碰 `audio_file` / `track` / `album` / `artist`。** 那几张表描述的是"这首歌本身"，动了就会换 `guid`，用户的歌单就废了。

---

## 环境事实

先确认你面对的确实是这个环境，**对不上就停下来问用户**。

**关键：数据在三个地方，而且分属不同的文件系统。** 这是最容易搞错的一点 ——
早期版本以为"一个目录里能找到全部"，真机上直接判成结构不符。

| 项 | 值 | 在哪个盘 |
|---|---|---|
| 系统 | 飞牛 OS（fnOS），Debian 系 | — |
| **主库** | `/usr/local/apps/@appdata/trim.music/db/music.db` | 系统盘 `/`（ext4） |
| 主库别名 | `/var/apps/trim.music/var/db/music.db`（软链到上面那个） | 同上 |
| **歌词分片** | `/vol2/@appmeta/trim.music/lyric-sqlite/lyric-<NN>.db`，16 个 | 存储池 `/vol2`（btrfs） |
| **音乐库** | `/vol2/1000/音乐` | 存储池 |
| 分片规则 | `lyric.stored_guid` 的**首个十六进制字符** → `"lyric-%02x.db"`（**两位零填充**，`bc8…` → `lyric-0b.db`） | — |
| 关键表 | 主库：`audio_file`（路径）、`track`（guid）、**`lyric`（索引，不含正文）**；分片：**`lyric_content`（正文）** | — |
| 关联链 | `audio_file.id` ← `track.audio_file_id`；`track.id` ← `lyric.track_id`；`lyric.stored_guid` → 分片文件 → `lyric_content.guid` | — |
| 偏好表 | 主库里的 `user_track_lyric_preference` / `user_track_lyric_offset`，以 `lyric_id` 为键 | — |
| 权限 | `@appmeta` 是 `770 root:root`、分片 755/644、`music.db` 644。容器要以 **root** 跑 |

> **三个最容易搞错的地方**（本项目都错过）：
> 1. **主库的 `lyric` 表里没有正文**，正文在分片的 `lyric_content` 表。
> 2. **分片名两位零填充**：`"lyric-%02x.db" % int(stored_guid[0], 16)`。漏掉零填充会错掉大部分。
> 3. **全库没有 `isLRC` 这个列。** 别去设它。

**注意**：`/vol2/@appdata` 里**没有** `trim.music`（那里只有别的应用）。主库在系统盘的
`/usr/local/apps/@appdata`，别找错地方。

### 快速确认

```bash
ls -l /usr/local/apps/@appdata/trim.music/db/music.db
ls /vol2/@appmeta/trim.music/lyric-sqlite/ | head
```

两个都有输出才算找对了。存储池编号（`vol2`）可能不同，用 `ls -d /vol*/@appmeta/trim.music` 看有几个。

---

## 工具

用一个单文件脚本 `fnos.py`。**只依赖 Python 标准库**，不需要 pip、不需要 Docker。

```bash
python3 --version          # 需要 3.8+
```

没有 `fnos.py` 的话，去项目的 `fnlrc/fnos.py` 拿。或者在有 Docker 的机器上：

```bash
docker run --rm -v /usr/local/apps/@appdata/trim.music:/fnos-data/appdata \
                -v /vol2/@appmeta/trim.music:/fnos-data/appmeta \
                -v /vol2/1000/音乐:/music -v /tmp:/srv \
  python:3.12-slim python /srv/fnos.py \
    --app-data /fnos-data/appdata --appmeta /fnos-data/appmeta --music-root /music
```

**两个 `-v` 都不能省。** 主库在系统盘、分片在存储池，是两个文件系统。

---

## 步骤

### Step 0 · 问清楚用户的目标

在做任何事之前确认两件事：

1. **音乐库的真实路径**是什么（不是 `\\NAS\音乐`，不是映射盘符）。通常在 `/vol2/1000/音乐` 这个形状。
2. 用户**能不能接受歌单重建**。
   - 能 → 告诉他"删库重扫"更简单（飞牛界面里操作），但要先说清代价：所有 `guid` 会变，歌单、收藏、播放历史全失效。
   - 不能 → 走下面的回填流程。

**不要跳过这一步。** 用户如果根本不在乎歌单，你就是在做一件多余且有风险的事。

### Step 1 · 探测结构

```bash
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music
```

**期望输出**（关键几行）：

```
  飞牛数据位置：
  ✅ 主库目录：/usr/local/apps/@appdata/trim.music（…/@appdata/trim.music/db/music.db）
  ✅ 歌词分片目录：/vol2/@appmeta/trim.music/lyric-sqlite/
  主库共 25 张表；歌词分片 16 个
  字段对应关系（按名字模糊匹配出来的）：
    · path = path                · track_guid = guid
    · stored_guid = stored_guid  · source = source
    · lyric_track_fk = track_id  · lyric_pk = id
    · content = content          · content_key = guid
    · user_track_lyric_preference：有
    · track：980 行
    · lyric：660 行
  分片正文表「lyric_content」的列：guid, content, created_at
  主库列：audio_file = id, path, size, …
  主库列：track = id, guid, audio_file_id, …
  主库列：lyric = id, guid, track_id, source, stored_guid, created_at, updated_at
    ✅ 关联自检：用 lyric.stored_guid 去分片查正文，命中 50/50（100%）
    ❌ 关联自检：用 lyric.guid 去分片查正文，命中 0/50（0%）
  分片行数合计 660（抽样验证编号规则）：
    · lyric-00.db：24 行，编号命中 20/20
  ✅ 结构探测通过
  结论：结构正常，可以做回填。
```

**注意前两行是「主库目录」和「歌词分片目录」两条，各自独立打勾。** 只有一条打勾 =
另一处没挂进来 —— 那正是 2026-09 之前那份 runbook 判「结构不符」的原因。

**「关联自检」那两行是自证**：它把「用 `stored_guid` 还是 `guid` 去分片查正文」两种假设都测一遍、
都报命中率。真机上第一种是 100%、第二种是 0%。**如果第一种明显低于 98%，停下来。**

**这一步的判据：**

| 看到 | 含义 | 你要做什么 |
|---|---|---|
| `✅ 结构探测通过` + `结论：结构正常` | 一切正常 | 继续 Step 2 |
| 只有「✅ 主库目录」没有「✅ 歌词分片目录」（或反之） | **另一处没挂进来** | 按上面的 `docker run` 把两个 `-v` 都加上 |
| `❌ 没找到主库（music.db）` | 主库路径不对 | 用 `ls -d /vol*/@appdata/trim.music` 找一遍；它通常不在存储池，而在 `/usr/local/apps/@appdata` |
| `❌ 没找到歌词分片目录（lyric-sqlite/）` | 分片路径不对 | 用 `ls -d /vol*/@appmeta/trim.music` 找 |
| 关联自检不是 `100%` / 不足 `98%` | 分片 key 列的假设不对 | **停。** 把输出发出来 |
| `结论：结构与预期不符` | 飞牛改过表结构 | **停。** 把完整输出发给用户，不要尝试写入 |
| 分片那几行「编号命中」不是 `N/N` | 分片规则变了 | **停。** 发输出给用户 |

### Step 2 · 看差异（仍然只读）

```bash
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music --music-root /vol2/1000/音乐
```

**期望输出的样子：**

```
音乐库：/vol2/1000/音乐
主库：  /usr/local/apps/@appdata/trim.music
歌词分片：/vol2/@appmeta/trim.music

  库里共 520 首，其中 378 首能在分片里找到歌词行
  · 磁盘上没有 .lrc：207 首（跳过）
  · 磁盘与库里一致：0 首（跳过）
  · **需要回填：312 首**

前 10 条（每条都注明它凭什么匹配到那一行）：
  · 残酷な天使のテーゼ.lrc
      track.id=41 → lyric.id=100 → lyric-0a.db 的 lyric_content
      正文 612 字（868d839e5747）→ 1204 字（bb445b9e9a00）
  ...

（这一步没有写任何东西）
```

**你要做的判断：**

- 「需要回填」的条数**合不合理**？如果用户说"我刚补完 300 首"，这里显示 312 就对得上。如果显示 0，说明磁盘上的 `.lrc` 跟库里已经一致，不用做。
- 抽查几条「匹配到哪一行」——`guid` 和 `lyric.id` 的对应关系应该是稳定的。有疑问就把样本发给用户确认。
- **「磁盘有 .lrc 但库里查不到歌词行」这一桶不要一律当成故障。** 它含两种情形：
  1. **这首歌在库里本来就没有歌词记录**（从没刮到过）。这是**常态** —— 真机上 520 首里有 142 首是这样，磁盘上却可能有 `.lrc`。
  2. `stored_guid` 和 `track.guid` 的对应关系与我们的假设不符。**这才是要停下来的信号。**

  怎么粗判：看「其中 N 首能在分片里找到歌词行」那个绝对数。`总数 - N` 如果和这一桶的条数差不多，就是情形 1，正常；只有差额远小于这一桶，才需要怀疑情形 2。

  **情形 1 是最大的一块价值**（真机上 520 首里 142 首，约四分之一），而且**可以填** —— 见 Step 4 的「给没有歌词的歌新建行」。

**把这份差异清单摘要给用户看，等他确认。** 这是唯一一次他能反悔的机会。

### Step 3 · 备份

```bash
cp /usr/local/apps/@appdata/trim.music/db/music.db /vol2/music.db.bak-$(date +%F-%H%M)
cp -r /vol2/@appmeta/trim.music/lyric-sqlite/ /vol2/lyric-sqlite.bak-$(date +%F-%H%M)/
```

**先确认备份确实生成了**（`ls -la /vol2/*.bak-*`），再往下走。脚本自己也会备份，但那是第二层保险。

### Step 4 · 执行

**先跑不带 `--allow-insert` 的**（只改已有歌词的正文，风险最小）：

```bash
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music --music-root /vol2/1000/音乐 --apply
```

**期望输出：**

```
✅ 回填完成：312 首原地更新，0 首新建歌词行（跳过 207 首）
   备份在：/vol2/@appmeta/trim.music/fnlrc-backup-20260915-104233

如果飞牛里没立刻变化，重启一下飞牛音乐应用。
```

**确认没问题之后**，再加 `--allow-insert` 给"一行歌词都没有"的歌建行：

```bash
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music --music-root /vol2/1000/音乐 --allow-insert --apply
```

**期望输出：**

```
✅ 回填完成：0 首原地更新，142 首新建歌词行（跳过 207 首）
   备份在：/vol2/@appmeta/trim.music/fnlrc-backup-20260915-110512
   新建明细：
     · 某首歌.lrc → lyric-0b.db (stored_guid=bc8f21…)
     ...
```

**判据：**

| 看到 | 含义 |
|---|---|
| `回填完成：N 首原地更新`，N > 0 | 成功。继续 Step 5 |
| `M 首新建歌词行`，M > 0 | 成功。**逐条看一眼「新建明细」里的分片名对不对** |
| `· ⚠ …：学不出 lyric 的 guid 格式…` | 拒绝新建。这是**保护**，不是故障 —— 把输出发出来，别硬来 |
| `ℹ 没有需要回填的` | 磁盘和库里已经一致，不用做 |
| `⚠ 有 N 首失败：` | 部分失败。看每条的 `why`，通常是回读校验不一致或数据库被占用 |
| `❌ ...` 开头 | 出错，没写任何东西。把整行发出来 |

### Step 5 · 验证

**验证一：`lyric.id` 与 `track.guid` 都没变。** 这是"歌单没丢"的直接证据。

```bash
# 回填前跑一次，回填后再跑一次，两次输出必须一致
sqlite3 /usr/local/apps/@appdata/trim.music/db/music.db \
  "SELECT id, track_id, stored_guid FROM lyric ORDER BY id LIMIT 20"
sqlite3 /usr/local/apps/@appdata/trim.music/db/music.db \
  "SELECT id, guid FROM track ORDER BY id LIMIT 20"
```

**验证二：正文真的换了。** 注意正文在**分片**的 `lyric_content` 表里，按 `guid` 取：

```bash
sqlite3 /vol2/@appmeta/trim.music/lyric-sqlite/lyric-0b.db \
  "SELECT length(content), typeof(content) FROM lyric_content WHERE guid = 'bc8f21…'"
```

长度应该和差异清单里写的「新 xx 字」对得上。`typeof` 是 `blob` 属正常（真机上就是这样）。

**验证三（用了 `--allow-insert` 才做）：新建的行在库里成对存在。**

```bash
# 索引行：应该在主库里
sqlite3 /usr/local/apps/@appdata/trim.music/db/music.db \
  "SELECT id, track_id, source, stored_guid FROM lyric WHERE track_id = <某个没歌词的 track.id>"
# 正文行：用上面拿到的 stored_guid 去它对应的分片查
sqlite3 /vol2/@appmeta/trim.music/lyric-sqlite/lyric-<NN>.db \
  "SELECT length(content) FROM lyric_content WHERE guid = '<上面那个 stored_guid>'"
```

`source` 应该是 **2**（同目录旁挂）。两边都在，才算真的成了。

### Step 6 · 汇报

给用户一份简短的结论，包含：

1. 改了多少首，备份在哪（**这条必须写**）
2. 有几首失败、为什么
3. 下一步该做什么：**回飞牛音乐重启一下应用**（如果歌词没立刻出现）
4. 一个提醒：如果飞牛的「自动下载歌词」开着，它可能又去在线抓一遍覆盖掉，建议关掉

---

## 失败处理

| 情况 | 怎么办 |
|---|---|
| 结构探测不过 | **停**。把输出给用户，不要尝试写入。飞牛可能改过版本 |
| 关联自检命中率低 / 匹配不上的很多 | **停**。说明 `stored_guid` → 分片这条链的假设不对，需要在真实数据上重新确认 |
| 写入时报 `database is locked` | 飞牛正在用这个库。稍等重试；或先停掉飞牛音乐应用，写完再启动 |
| 写入时 `disk I/O error` | 磁盘满或存储异常。检查 `df -h` |
| 部分失败（回读校验不一致） | 通常是被飞牛并发写覆盖了。重启飞牛应用后重跑一次 |
| 写完飞牛里没变化 | 重启飞牛音乐应用。如果还是没有，检查是不是这首歌的音频**内嵌了歌词**（内嵌优先级永远更高） |
| 用户说歌词变错乱了 | **立刻从备份恢复**（见下），然后把情况报给用户 |

### 从备份恢复

```bash
B=/vol2/@appmeta/trim.music/fnlrc-backup-20260915-104233     # 换成实际的备份目录
cp $B/music.db /usr/local/apps/@appdata/trim.music/db/music.db
cp $B/lyric-*.db /vol2/@appmeta/trim.music/lyric-sqlite/
```

只恢复某一首：从 `$B/old-content.tsv` 里按 `lyric_id` 找到旧正文，写回去。

---

## 绝对不要做的事

- **不要在没跑过差异预览的情况下加 `--apply`。**
- **不要写 `audio_file` / `track` / `album` / `artist`。** 那会换 guid，用户的歌单会废。
- **不要在结构探测失败后"试着写一下"。** 表结构变了还硬写，可能毁掉用户的库。
- **不要跳过备份。**
- **不要替用户决定"删库重扫也行"。** 那是他的歌单，让他自己选。

### 关于「新建歌词行」：三种情况，做法不同

| 情况 | 正确做法 | 依据 |
|---|---|---|
| 该曲**已有**歌词，只是内容要换 | **原地 UPDATE 正文**（`stored_guid` 不动） | 无偏好问题，还保住了用户的歌词偏移（`user_track_lyric_offset` 以 `lyric_id` 为键）。零副作用，首选 |
| 该曲**一行歌词都没有** | **INSERT** 索引行 + INSERT 分片正文，`source=2` | 真机实测：应用立刻接受，不用碰偏好表 |
| 需要**多版本共存** | 新建行 **且必须 upsert `user_track_lyric_preference`** | 不改偏好则 `preferred` 停在旧行，用户以为没生效 |

**最后一种是必须避开的坑。** 真机实测：给一首已有歌词的歌插第二条，接口显示
`版本数=2` 但 `preferred` 仍是旧那条 —— **用户看不到新版本**；必须
`upsert user_track_lyric_preference(user_id, track_id, lyric_id=<新行>)` 才会切过去。

**所以本工具的画线是**：只做前两种。对**已有歌词行**的歌，一律原地更新，从不新建 ——
从根上不碰多版本那个坑。`--allow-insert` 只对"一行都没有"的歌生效。

### 两条实现细节

1. **`user_id` 从 `user` 表取**（真机上 `Home=1`、`lian=2`），偏好表的唯一键是
   `(user_id, track_id)` —— 要动偏好就得 upsert，不能 insert。
   （本工具目前**不需要**动偏好表，因为不新建多版本。）
2. **分片名两位零填充**：`"lyric-%02x.db" % int(guid[0], 16)`。
   `bc8…` → `lyric-0b.db`。真机第一次统计时漏了零填充，错了 660 条。

---

## 背景（想理解原理时看）

- 飞牛把旁挂 `.lrc` 当作**首次解析音频时的一次性输入**，路径入库后就不再重读。扫描、改 mtime、重写文件、移出再移回，全部无效。
- 唯一会重新解析的是**新路径**。所以对已入库的歌，只能改它的数据库。
- 改数据库有两个切入口：删记录重扫（换 guid，丢歌单），或者改歌词数据本身（保 `lyric_id`，保歌单）。**我们走后者。**
- 完整实测矩阵见 [TECH-NOTES.md](TECH-NOTES.md) 2.3 与 2.5；决策记录见 [DECISIONS.md](DECISIONS.md) ADR-015 / ADR-017；用户向的说明见 [FNOS-SIDECAR.md](FNOS-SIDECAR.md)。
