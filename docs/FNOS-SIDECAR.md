# 为什么写完歌词飞牛没变化，以及怎么办

> **一句话**：飞牛只在**歌曲首次入库时**读一次同目录的 `.lrc`。已经入库的歌，
> 之后再怎么写文件都不会生效 —— 扫描、改 mtime、重写文件、把文件移出再移回，**全部无效**。
>
> 机制与实测矩阵见 [`TECH-NOTES.md` §2.3](TECH-NOTES.md)。本文只讲**怎么办**。
>
> **看不懂命令行？** 把 [AGENT-RUNBOOK.md](AGENT-RUNBOOK.md) 交给你服务器上部署的 AI 助手
> （Hermes、Claude Code 之类），让它照着做。

---

## 先看这个决定树

```
这首歌已经在飞牛库里了吗？
│
├─ 没有（新歌）
│    → 【路线 1】先补完、再入库        零风险，天然生效
│
└─ 已经在库里了
     │
     ├─ 歌单/收藏/播放历史可以接受重建？
     │    → 【路线 4】删库重扫         必然有效，界面就能做，但 guid 全变
     │
     └─ 不能丢
          → 【路线 3】改它的歌词库     歌单能保住，但要碰另一个应用的数据库
               ├─ 先找飞牛自己的接口（路径 ①）—— 找到就不用碰 DB，最优
               └─ 找不到 → fnlrc 的容器把两处数据目录都挂进去写（路径 ②）
```

> ~~路线 2：用飞牛自己的「手动关联本地歌词」功能~~
> —— **已排除**。实测飞牛的 UI 没有做这个入口，走不通。

---

## 路线 1 · 新歌：先补完、再入库（唯一零风险的正解）

```
下载 → 放到暂存目录（不在飞牛音乐库里）
     → 用 fnlrc 补完（.lrc 与音频同目录）
     → 整首移进音乐库          ← sidecar 与音频一起被首次解析，天然生效
```

**为什么有效**：歌词来源的优先级是 `内嵌 > 旁挂 > 在线刮削`（对应 `lyric.source`
的 `1`/`2`/`3`），而这套优先级只在首次解析时评估一次。让"首次解析"发生在
sidecar 已经就位之后，问题就不存在。

**歌单会不会丢**：不会。新歌还没有任何歌单/收藏记录。

**代价**：改变了整理习惯。如果你是"边下边听"，得先攒到暂存目录。

---

## 路线 2 · 用它自己的歌词功能（已排除）

理论上最理想 —— 让飞牛用它自己的代码路径把歌词写进去，零 DB 风险、guid 不变、偏好自动同步。

**实测结果：UI 里根本没做这个入口。** 二进制里虽然有 `source=4 用户手动关联` 这个取值，
但前端没有暴露出来，用户点不到。

所以这条路现在只能寄希望于"路径 ①"：找到它**后端**的接口（UI 没做不等于后端没实现）。
找到的话，它仍然是所有方案里最好的一个。

## 路线 3 · 改它的歌词库（唯一能同时保住歌单的技术路径）

**前提**：路线 2 走不通（实测：飞牛的 UI 根本没做"手动关联本地歌词"这个入口），
且你确实要保住歌单。

### 怎么执行：四种方式，按推荐程度排

#### 方式 ① · SSH 上去跑一个单文件（**最顺**）

`fnos.py` **只依赖 Python 标准库** —— 没有第三方依赖，所以它就是**一个文件**，
拷到 NAS 上就能跑，不用装 fnlrc、不用改 Docker 配置、不用动现有容器。

```bash
# 0) 先确认飞牛开了 SSH（设置里有开关）
# 1) 把 fnlrc/fnos.py 传到 NAS 上，比如：
#    scp fnos.py 你的用户名@NAS的IP:/tmp/
ssh 你的用户名@NAS的IP

# 2) 先看现状（只读，什么都不改）
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music

# 3) 算差异，看清楚每一首会怎么改（仍然只读）
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music --music-root /vol2/1000/音乐

# 4) 确认无误后再写
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music --music-root /vol2/1000/音乐 --apply
```

**为什么推荐这条**：它是唯一"零安装、零配置改动"的路径。用户只要有 SSH 就能做，
不需要理解 Docker、不需要重建容器。

> ⚠️ **不要**用 `curl ... | sudo python3 -` 这种形式。这个脚本会改另一个应用的数据库，
> 用户应该先把它下载下来、看一眼、再运行。**能审阅**是安全的一部分。

如果 NAS 上没有 Python3（飞牛基于 Debian，通常自带），用一次性容器替代：

```bash
docker run --rm \
  -v /usr/local/apps/@appdata/trim.music:/fnos-data/appdata \
  -v /vol2/@appmeta/trim.music:/fnos-data/appmeta \
  -v /vol2/1000/音乐:/music -v /tmp:/srv \
  python:3.12-slim python /srv/fnos.py \
    --app-data /fnos-data/appdata --appmeta /fnos-data/appmeta --music-root /music
```

（把 `fnos.py` 也挂进去，或者先 `docker cp` 进容器。**两个数据目录都要挂** ——
主库在系统盘、歌词正文在存储池，是两个文件系统。）

#### 方式 ② · 让 fnlrc 的容器去写（如果你已经在用网页版）

`@appdata` 和 `@appmeta` 都是**应用的私有目录**，SMB 里看不到、普通用户也没权限 ——
所以桌面版（跑在你 PC 上）够不到它们。但 fnlrc 的 Docker 版就跑在飞牛上，加两行挂载就行：

```bash
docker run -d --name fnlrc \
  -p 127.0.0.1:8080:8080 \
  -v /vol2/1000/音乐:/music \
  -v /usr/local/apps/@appdata/trim.music:/fnos-data/appdata \
  -v /vol2/@appmeta/trim.music:/fnos-data/appmeta \
  fnlrc
```

（后两个挂载是这次多加的。**两个都要** —— 只挂 `@appmeta` 会找不到 `music.db`，
只挂 `@appdata` 会找不到歌词正文，探测会直接报「没找到」。）然后：

```bash
docker exec fnlrc python -m fnlrc fnos --app-data /fnos-data/appdata --appmeta /fnos-data/appmeta --music-root /music --apply
```

#### 方式 ③ · 走飞牛自己的 HTTP 接口（理论上最优，但未验证）

`source=4`（用户手动关联）这个取值**存在于数据结构里**，说明后端很可能实现了"设置歌词"，
只是 UI 没暴露。如果找得到那个接口：

- fnlrc 调它 → **飞牛用它自己的代码写库** → 零 DB 风险
- 偏好由它自己同步，连"改了但播放器还显示旧歌词"都不会有

**怎么找**：浏览器打开飞牛音乐的网页端 → F12 → Network → 做一次会改变歌词的操作，
看它 POST 到哪、带什么 body。或者翻 `fnmusic` 前端资源里的 API 路由表。

> 找到的话它比上面两条都好 —— 值得先花 10 分钟找找看。

#### 方式 ④ · 桌面版直连（不推荐）

除非先把 `@appmeta` 共享出来 —— 那等于把应用的私有数据暴露到局域网，**不建议**。

---

### 探测报告怎么看

在写任何东西之前，先确认结构还对得上。飞牛一次升级就可能改表结构，猜着写会毁掉用户的库。

命令见上面的【方式 ①】。它**只读**打开（`file:...?mode=ro`，物理上写不进去），输出是这样：

```
  飞牛数据位置：
  ✅ 主库目录：/usr/local/apps/@appdata/trim.music（…/db/music.db）
  ✅ 歌词分片目录：/vol2/@appmeta/trim.music/lyric-sqlite/
  主库共 25 张表；歌词分片 16 个
  字段对应关系（按名字模糊匹配出来的）：
    · path = path                · track_guid = guid
    · stored_guid = stored_guid  · source = source
    · lyric_track_fk = track_id  · content_key = guid
    · lyric_pk = id              · content = content
    · user_track_lyric_preference：有
    · user_track_lyric_offset：有
    · audio_file：520 行
    · track：520 行
  分片正文表「lyric_content」的列：guid, content, created_at
  主库列：audio_file = id, path, size, …
          track = id, guid, audio_file_id, …
  分片行数合计 660（抽样验证编号规则）：
    · lyric-0.db：24 行，编号命中 20/20
    · lyric-1.db：41 行，编号命中 20/20
    …

  ✅ 结构探测通过
  结论：结构正常，可以做回填。
```

**「分片表名…的列」那两行是照实打出来的**，不是猜的。真机上报「结构不符」时，
把它连同上面的字段对应关系一起发出来，就能定位到具体卡在哪一行预期上。

**开头那两行是关键**：主库目录和歌词分片目录各自独立打勾。只有一条打勾 = 另一处没挂进来。
（2026-09 之前那份 runbook 只挂 `@appmeta`，所以它必然报「没找到 music.db」。）

三种结论：

| 结论 | 含义 | 下一步 |
|---|---|---|
| `ok` | 结构对得上 | 继续看差异计划 |
| `not_found` | 两个目录里至少有一个没找到 | 路径不对 —— 在飞牛上确认那两处，或把两个挂载都加上 |
| `unexpected_schema` | 结构与预期不符 | **不要写入**。把报告发出来，先搞清飞牛改了什么 |

### 差异计划：先证明它匹配对了

加 `--music-root` 之后，它会列出**每一首会怎么改**，并注明**凭什么匹配到那一行**：

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
```

有三类**不会被处理**，而且会明确列出来，不会静默跳过：

| 情况 | 处理 |
|---|---|
| 磁盘有 `.lrc` 但库里**没有**这首歌的歌词行 | 列在「没有歌词行」里，默认**不动**；加 `--allow-insert` 可以给它建行 |
| 一个 guid 对到了多行歌词 | 列在「多行」里，**不动** —— 猜哪一行是对的太危险 |
| 磁盘与库里完全一致 | 跳过 |

**「查不到歌词行」这一桶要分清两种情形**，别一律当成故障：

1. **这首歌在库里本来就没有歌词记录**（从没刮到过）。这是**常态** —— 真机上 520 首里有 142 首是这样，而磁盘上可能有 `.lrc`。
2. `stored_guid` 和 `track.guid` 的对应关系与假设不符。这才是要停下来的信号。

粗判方法：看「其中 N 首能在分片里找到歌词行」。`总数 − N` 若与这一桶条数相当，就是情形 1。

**情形 1 可以填。** 2026-09 受控实测确认：给"一行歌词都没有"的歌 `INSERT` 一条歌词记录
（主库索引行 + 分片正文，`source=2`），应用**立即接受**，而且不用碰偏好表。
加 `--allow-insert` 打开这条能力。真机上 142 首（约四分之一）属于这一类。

⚠️ 但**已有歌词**的歌绝不要新建行：实测版本数变成 2、`preferred` 仍停在旧那条，
用户会以为没生效 —— 必须同时 upsert `user_track_lyric_preference` 才行。本工具不做这件事，
对已有歌词的歌一律原地更新。

**这一步仍然什么都没写。** 确认每条都匹配对了，再加 `--apply`。

### 写入做了什么

```sql
-- ① 已有歌词的歌：原地改正文（主库的索引行一个字都不动）
UPDATE "lyric_content" SET "content" = ? WHERE "guid" = ?;   -- 在 lyric-<NN>.db 里
```

只做这一件事。四条要守住的：

- **正文在分片的 `lyric_content` 表**，不在主库的 `lyric` 表（那张只是索引）
- 分片定位：`"lyric-%02x.db" % int(stored_guid[0], 16)` —— **两位零填充**
- **保留 `lyric_id` 与 `stored_guid`**：`user_track_lyric_preference` / `user_track_lyric_offset`
  都以 `lyric_id` 为键。改成"新增一行"而不动偏好，偏好仍指向旧行，播放器还是显示旧版本
- **绝不碰 `audio_file` / `track`** —— 那才会换 guid、才会丢歌单

**给"一行歌词都没有"的歌**（要 `--allow-insert`）才多两步：主库 `INSERT INTO lyric`
（`guid` / `track_id` / `source=2` / `stored_guid` / 时间戳）+ 分片
`INSERT INTO lyric_content (guid, content)`。**已有歌词的歌永远不会走这两步。**

写完之后**逐条回读校验**（正文 sha256），对不上的会列进失败清单。

### 回滚

每次 `--apply` 都会在数据目录下建一个 `fnlrc-backup-<时间戳>/`，里面有：

- `music.db` —— 主库整份
- `lyric-<NN>.db` —— 这次涉及到的每个分片
- `old-content.tsv` —— 每一首的旧正文（纯文本，方便单曲粒度找回）

整体回退：把备份里的文件拷回去覆盖。只回退某一首：拿 `old-content.tsv` 里那一行重新写回。
备份只保留最近 5 次，不会无限堆积。

### 必须配套的安全措施

| 措施 | 为什么 |
|---|---|
| 写前备份 `music.db` + 目标分片 | 出错能整体回退 |
| dump 旧正文到 sidecar 目录 | 单曲粒度回滚 |
| 连接设 `busy_timeout` | 应用在跑，WAL 并发写 |
| 写完回读校验（正文 sha256） | 确认真的写进去了 |
| 默认不新建歌词行，要 `--allow-insert` 显式开 | 新建会往另一个应用的库里插记录 |
| 启动时探测分片规则与表结构 | 飞牛升级改结构时**必须拒绝执行**，而不是写坏 |
| 默认 dry-run，`--apply` 才动手 | 先看清楚要改什么 |

### 两个容易忽略的坑

1. **关掉自动下载歌词**。如果飞牛开着 `auto_download_lyric`，它可能在你写完之后
   又去在线抓一遍、把你的双语歌词覆盖成单语版。回填前先确认这个开关的状态。
2. **改完如果没立刻生效**，重启一下飞牛音乐应用 —— 实测是"立即生效、无需重启"，
   但不同版本可能对歌词有内存缓存。重启是安全的兜底动作。

### 代价（要如实告诉用户）

- 要访问 NAS 上的两个应用私有目录（`/usr/local/apps/@appdata/trim.music/` 和
  `/vol2/@appmeta/trim.music/`），通常需要提权
- 这是在**改另一个应用的数据库**，飞牛一次升级就可能失效
- 所以它只能是显式开启的高级功能，不能是默认行为

---

## 路线 4 · 删库重扫（最后手段）

把音乐库从飞牛里**移除再重新添加**。删除库会清掉 `audio_file` / `track` / `album` /
`artist` / `lyric` 记录，重新添加后所有文件都是"新路径" → 全量重新解析 → 所有 `.lrc` 都被重读。

**必然有效，界面就能做，零 DB 操作。** 但代价很直接：

> **所有 `track.guid` 会变** → 歌单、收藏、播放历史、歌词版本偏好、歌词偏移
> （`user_track_lyric_preference` / `user_track_lyric_offset`）**全部失效**。

### 动手之前，先把这几件事弄清楚

这份清单不是劝你别做 —— 是让你**算清楚代价再决定**。不同人的库差别很大。

- [ ] **我的歌单是怎么建的？**
      - 按目录/规则自动重建的（例如每晚同步脚本）→ **能自愈，代价很小**
      - 手工一首一首加的 → **会丢，要重建**
- [ ] **一共多少个歌单、多少首歌在里面？** 手工重建要多久？
- [ ] **有多少收藏 / 红心？** 这些**不会**自动恢复
- [ ] **播放历史重要吗？** 会清零
- [ ] **我给哪些歌调过歌词偏移或换过歌词版本？** 这些偏好以 `lyric_id` 为键，会失效
- [ ] **元数据偏好是「云端」还是「本地」？** 如果是云端，重扫会**重新联网刮削**一遍，
      可能把你手工改过的封面/流派/年份覆盖掉
- [ ] **库有多大？** 全量重扫需要多久（本项目实测 520 首约 8.9 秒/首的抓取部分不算在内，
      重扫本身取决于 NAS 的磁盘与 CPU）

### 一定要做的

```bash
# 备份这两处，缺一不可 —— 主库在系统盘，歌词正文在存储池
cp /usr/local/apps/@appdata/trim.music/db/music.db  music.db.bak-$(date +%F)
cp -r /vol2/@appmeta/trim.music/lyric-sqlite/  lyric-sqlite.bak-$(date +%F)/
```

出了任何问题，这两个备份就是你的后悔药。

---

## 附：几条已知常量（给要写工具的人）

| 项 | 值 |
|---|---|
| **主库**（歌曲、路径） | `/usr/local/apps/@appdata/trim.music/db/music.db`（系统盘 `/`，ext4）；别名 `/var/apps/trim.music/var/db/music.db` |
| **歌词正文** | `/vol2/@appmeta/trim.music/lyric-sqlite/lyric-<NN>.db`（16 个），表名 **`lyric_content`**（存储池 `/vol2`，btrfs + trimacl） |
| 主库的 `lyric` 表 | **只是索引**：`id, guid, track_id, source, stored_guid, created_at, updated_at` —— 没有正文列 |
| 关联链 | `audio_file.id` ← `track.audio_file_id`；`track.id` ← `lyric.track_id`；`lyric.stored_guid` → 分片 → `lyric_content.guid` |
| 分片号 | `stored_guid` 的第一个十六进制字符（实测 660/660） |
| `lyric.source` | `1`=内嵌、`2`=同目录旁挂、`3`=在线刮削、`4`=用户手动关联 |
| 偏好的外键 | `user_track_lyric_preference` / `user_track_lyric_offset` 都在**主库**里，以 **`lyric_id`** 为键 |
| 旁挂歌词读取点 | `core/pkg/scraper/local/sidecar.go` 的 `lookupSidecarLyrics` |
| 文件监听 | `sharedLibraryFSEventHandler` 只维护"文件存在状态"，实测对音乐库目录里的写入/移动无反应 |

> ⚠️ **主库和歌词正文在两块盘上**（`/` 和 `/vol2`），所以任何容器/工具都要挂两处。
> `/vol2/@appdata` 里**没有** `trim.music` —— 别去那儿找 `music.db`。
> 这是 2026-09 真机核实的结果；早先的版本以为"一个 data-dir 里能找到全部"，那是错的。
>
> ⚠️ 这些是从**某一个飞牛版本**实测/抽取出来的。写工具时必须在启动阶段探测校验，
> 不匹配就拒绝执行 —— 不要假定它们永远成立。
>
> 交付的真机结构指纹：`fnOS-音乐-1.0.1-结构指纹.json`（25 张表 + 每张表的列、
> 16 个分片的编号命中统计、逐项权限）。
