# 网页版：部署和使用

## 什么时候该用网页版

- 你想让工具**跑在 NAS 上**，直接读写本机的音乐目录。这样绕开了 SMB 和 WebDAV 的权限问题，速度也快。
- 你有好几台设备（电脑、手机、平板），想都能打开看进度。
- 你不想在 Windows 上装东西。

界面和桌面版完全一样，看 [GUIDE.md](GUIDE.md)。这一篇只讲部署和网页版特有的那些事。

---

## 一、部署

仓库里有现成的 `Dockerfile` 和 `docker-compose.yml`。

```bash
git clone <仓库地址> && cd fnlrc
docker build -t fnlrc .
```

然后在飞牛的 Docker 界面里新建容器，或者用命令行：

```bash
docker run -d --name fnlrc \
  -p 127.0.0.1:8080:8080 \
  -v /vol2/1000/音乐:/music \
  -v fnlrc-data:/data \
  fnlrc
```

三个地方要改：

**`-v /vol2/1000/音乐:/music`** —— 冒号左边换成**你 NAS 上音乐目录的真实路径**。

怎么知道真实路径？在飞牛的文件管理器里进到那个目录，看地址栏。飞牛上通常是 `/vol2/1000/音乐` 这个形状（`vol2` 是存储池编号，`1000` 是用户 ID）。

冒号右边 `:/music` 是容器内部的路径，随便叫什么都行，**但记住它**——待会儿在界面里要填的是这个。

**`-p 127.0.0.1:8080:8080`** —— 意思是只在 NAS 本机开放 8080 端口。

想让局域网里其他设备也能打开，改成 `-p 8080:8080`。但**改了就必须靠访问令牌保护**，别裸奔。

**`-v fnlrc-data:/data`** —— 任务历史、配置、令牌都存在这里。别省掉，不然容器一重建什么都没了。

### 用 compose 更省事

`docker-compose.yml` 里已经写好了，改一下音乐目录的路径，然后：

```bash
docker compose up -d
```

---

## 二、第一次打开：拿令牌

浏览器访问 `http://<NAS-IP>:8080`。

**第一次打开**会看到一个三步引导（欢迎 → 填令牌 → 选音乐库），一步步带你填完，不用自己摸索。

去服务器上取令牌：

```bash
docker logs fnlrc 2>&1 | grep -i token
```

或者在 NAS 的 Docker 界面里看容器日志，开头那几行就有。也可以进容器里问：

```bash
docker exec fnlrc python -m fnlrc token
```

拿到之后粘进引导里，点「验证并继续」。它存在浏览器里，下次不用再填。

**如果你是从 NAS 本机打开的**（`http://127.0.0.1:8080`），引导会**跳过令牌那一步** ——
程序已经自动把令牌注进去，因为那本来就是你自己那台机器。

> 已经配置过的环境不会看到引导，直接进主界面；如果哪天 `config.json` 被改坏了，
> 再次打开会自动回到引导，走完顺便修复。

> ⚠️ 这个令牌和 API key 一样重要。任何拿到它的人都能触发扫描和**写入**你的音乐库。别截图发出去，别提交到仓库。
>
> 怀疑泄露了就重置：
>
> ```bash
> docker exec fnlrc python -m fnlrc token --rotate
> ```
>
> 重置之后所有设备都要重新填一次。

---

## 三、在界面里填音乐库路径

第一屏那个输入框，**填容器里的路径**，也就是你刚才映射的右边那半：`/music`。

不是 NAS 的真实路径（`/vol2/1000/音乐`），也不是 UNC 路径。填错了会提示「打不开这个路径」。

填对之后点扫描，正常的话第二屏会显示扫到了多少首歌。

### 如果扫不到东西

按这个顺序查：

1. **映射对不对**。`docker exec fnlrc ls /music` 看看里面有没有东西。
2. **权限对不对**。容器默认用 root 跑，一般没问题。但如果你在 compose 里写了 `user: "1000:1000"`，那这个 uid 对音乐目录得有读权限。
3. **路径拼错**。多一层少一层目录很常见。

---

## 四、先测写入权限

部署完之后**第一件事**：右上角「设置 ⚙」→「测试写入权限」。

它会试着在你的音乐目录里建一个临时文件再删掉。SMB 只读挂载、容器权限不足、目录被占用——这些坑都能在这里提前发现，不用等跑了半小时才报错。

报错的话它会告诉你具体是哪种失败。

常见的两种：

**目录是只读挂载的** —— `-v` 那一行去掉 `:ro`。

**目录属主不是容器用户** —— 要么改目录属主，要么在 compose 里加 `user: "你的uid:你的gid"`。飞牛上一般是 `1000:1000`。

> ℹ️ 这个测试是「建一个 `fnlrc_write_test.tmp` 再删掉」。**库内不会留东西**，
> 但在飞牛上删除会进回收站 —— 所以回收站里会出现一条同名记录，属正常现象，清空回收站即可。

---

## 五、配 API key（可选）

不配也能用——默认只用网易云自带的中文翻译，零成本。

想补翻源站漏掉的行，两个办法：

**办法一：环境变量（推荐）**

在容器旁边放一个 `fnlrc.env`：

```
FNLRC_REMOTE_API_KEY=sk-xxxx
```

然后加 `--env-file fnlrc.env` 启动。

别用 `-e FNLRC_REMOTE_API_KEY=...` 写在命令行里——那个会留在 shell 历史和 `docker inspect` 的输出里。

用环境变量的话，「设置 → 我的 API」里的 key 输入框会**变成只读**，显示「已由环境变量配置」。这是故意的，避免一份 key 两个地方。

**办法二：在界面里填**

打开「设置 ⚙」，填 Key（Base URL 和模型名已经填好了默认值），点「测试连接」验证。

不管哪种办法，**key 只存在服务端**。浏览器拿不到回显，页面上只能看到「已配置：`sk-123***cdef`」这样的打码值。

---

## 六、打开飞牛的 SSH

后面几节要用命令行。飞牛是 Debian 系，**SSH 默认是关着的**，先在飞牛里打开：

1. 飞牛桌面 → 「系统设置」
2. 找「终端与远程」或者「SSH」（不同版本位置略有差别）
3. 把开关打开，端口默认 22
4. 记下你的登录用户名（就是你平时登录飞牛那个账号）

然后在电脑上连：

```bash
ssh 你的用户名@NAS的IP
```

Windows 10/11 自带 `ssh` 命令，直接在 PowerShell 或终端里敲就行。连上之后会问密码，就是你的飞牛登录密码。

能用之后，再把文件传上去（在**你自己的电脑上**执行，不是在 NAS 上）：

```bash
scp fnlrc/fnos.py 你的用户名@NAS的IP:/tmp/
```

> ⚠️ SSH 打开了就意味着多了一个入口。用完之后如果不需要，回飞牛里把它关掉。密码别设太简单。

**不想碰命令行的话**，看你服务器上有没有部署 AI 助手（Hermes、Claude Code 之类）。
有的话，把这篇文档连同 [AGENT-RUNBOOK.md](AGENT-RUNBOOK.md) 一起丢给它，让它照着做——
那份是专门写给 AI 看的，每一步该跑什么、怎么判断成功失败都写清楚了。

---

## 七、容器能不能访问飞牛的数据目录

要把结果回填到飞牛，容器得能读到飞牛的**两处**数据：

- 主库：`/usr/local/apps/@appdata/trim.music`（在系统盘 `/` 上）
- 歌词分片：`/vol2/@appmeta/trim.music`（在存储池 `/vol2` 上）

它们在不同文件系统上，所以**容器要挂两个卷**。只挂 `@appmeta` 会找不到 `music.db`，
只挂 `@appdata` 会找不到歌词正文 —— 这俩都会让探测报"没找到"或"结构不符"。

**这一步有可能被拦。** 我没法替你验证（手上没有飞牛环境），但你花 30 秒就能自己确认。

**把这两个目录都挂进去**（在原来的 `docker run` 里多加两行）：

```bash
docker run -d --name fnlrc \
  -p 127.0.0.1:8080:8080 \
  -v /vol2/1000/音乐:/music \
  -v /usr/local/apps/@appdata/trim.music:/fnos-data/appdata \
  -v /vol2/@appmeta/trim.music:/fnos-data/appmeta \
  -v fnlrc-data:/data \
  fnlrc
```

（后两个 `-v` 是新增的。**两个都要** —— 主库在系统盘，歌词正文在存储池，缺哪个都跑不通。）

**然后跑一次探测**（只读，什么都不改）：

```bash
docker exec fnlrc python -m fnlrc fnos --app-data /fnos-data/appdata --appmeta /fnos-data/appmeta
```

三种结果对应三种情况：

| 你看到的 | 意思 | 怎么办 |
|---|---|---|
| `✅ 主库目录…` + `✅ 歌词分片目录…` + `结构探测通过` | 没被拦，两处都挂对了 | 继续下一节 |
| 只有一处打勾 | **另一处没挂进来** | 按上面的 `docker run` 把两个 `-v` 都加上 |
| `没找到主库（music.db）` | 主库没挂进来（或者挂成了 `/vol2/@appdata`，那里没有这个应用） | 按上面的 `docker run` 检查第一个 `-v` |
| `结构与预期不符` | 挂载没问题，但飞牛改过表结构 | **别往下走**，把输出发出来 |

### 挂载被拦了怎么办

先分清是哪一种：

**① 飞牛的 Docker 界面不让填 `@appmeta` 这种路径。**
有些版本的图形界面只让选「共享文件夹」里的路径。绕过办法：别用界面，用命令行建容器（上面的 `docker run`）。

**② 挂载命令执行了，但容器里看不到东西。**
在 NAS 上先确认这个路径本身存在、能读：

```bash
sudo ls -la /usr/local/apps/@appdata/trim.music/db/
sudo ls -la /vol2/@appmeta/trim.music/lyric-sqlite/ | head
```

能列出东西的话，再看看容器里：

```bash
docker exec fnlrc ls -la /fnos-data/appdata/db/
docker exec fnlrc ls -la /fnos-data/appmeta/lyric-sqlite/ | head
```

如果 NAS 上能看、容器里是空的，那就是挂载被挡了——可能是 fnOS 对 `@appmeta` 做了保护，也可能 Docker 的挂载点没配对。这种情况下**别硬来**，改用下面那条路。

**③ 干脆不用容器。**

在 NAS 本机上直接跑那个单文件就行，绕开 Docker 的挂载层：

```bash
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music --music-root /vol2/1000/音乐
```

（`fnos.py` 只要标准库，`scp` 上去就能跑。怎么打开 SSH 见上一节。）

**④ 写入的时候还要能写。**
读得到不等于写得进去。`@appmeta` 一般是 root 属主、权限 700，容器默认以 root 跑所以没问题；
但如果你在 compose 里写了 `user: "1000:1000"`，就写不了了——去掉那一行，或者确保那个 uid 对目录有写权限。

---

## 八、把结果回填到飞牛（进阶）

**这一节只在你需要处理「已经在飞牛库里」的歌时才看。**

先说背景，一句话：飞牛只在歌**第一次入库**时读一次同目录的 `.lrc`。已经入库的歌，你再怎么写 `.lrc` 它都不看。完整机制和实测记录见 [FNOS-SIDECAR.md](FNOS-SIDECAR.md)。

所以有这么个功能：直接把歌词写进飞牛的数据库，绕过"它不重读文件"这个限制。

**它的好处是能保住歌单。** 因为它只改歌词正文、保留 `lyric_id`——歌单、收藏、播放历史、歌词偏移都以那个 id 为键，id 不变就什么都不变。

### 界面上也能点

如果你已经在用网页版，这三步在**设置面板里就能点**，不用敲命令：

打开「设置 ⚙」，拉到底部的「回填到飞牛」：

1. 填两行路径（就是上面 `-v` 映射出来的右边那半）：
   - 「主库目录（music.db 所在）」→ `/fnos-data/appdata`
   - 「歌词分片目录（lyric-sqlite 所在）」→ `/fnos-data/appmeta`
2. 点「① 检测结构」——只读，确认表结构对不对
3. 点「② 预览差异」——只读，列出会改哪些歌、每首匹配到哪一行
4. 确认无误后点「③ 执行回填」——会弹一个确认框，把后果再说一遍

还有一个勾选框：「**给没有歌词的歌新建歌词行**」，默认**不勾**。

不勾时只改已有歌词的正文（风险最小，优先这么跑）。勾上之后，「库里一行歌词都没有」的歌
也会被填上 —— 真机实测应用能立刻认，这批在真机库里约占四分之一，正是最想补的那部分。

> ⚠️ **已有歌词的歌永远不会被新建。** 真机实测：给一首已有歌词的歌插第二条，
> 接口显示版本数变成 2，但 `preferred` **仍停在旧那条** —— 用户看不到新版本，
> 必须同时改偏好表才行。本工具不做这件事，对已有歌词的歌一律原地更新。
>
> 改动这个勾选框之后要**重新点一次「② 预览差异」**：差异数会变，
> 第③步会拿它跟当下重新算出的结果对账。

**默认是点不动的。** 服务端要显式开启才能写：

```bash
docker run ... -e FNLRC_ENABLE_FNOS_WRITE=1 ...
```

这是故意的：它会写另一个应用的数据库，属于高权限操作，不该默认开着。
开了之后，任何拿到令牌的人都能通过网页触发写入——你自己权衡。

界面的第③步还会**核对差异数**：如果从你预览到现在，库里的状态变了（比如你在飞牛里手动改了歌词），
它会拒绝执行并让你重新预览，不会照着过期的结果去写。

### 命令行也能跑

不想用界面，或者想把过程记下来，用命令更清楚：

**第一步：探测结构**

```bash
docker exec fnlrc python -m fnlrc fnos --app-data /fnos-data/appdata --appmeta /fnos-data/appmeta
```

（这两个路径是上面 `-v` 映射出来的。要是你映射成了别的名字，换成你自己的。）

它会报告表结构、分片规则对不对。如果显示 `unexpected_schema`，**就到此为止**，说明飞牛改过结构，得先搞清改了什么。

**第二步：看差异**

```bash
docker exec fnlrc python -m fnlrc fnos --app-data /fnos-data/appdata --appmeta /fnos-data/appmeta --music-root /music
```

它会列出每一首准备怎么改，而且**注明凭什么匹配到那一行**：

```
  · 残酷な天使のテーゼ.lrc
      track.id=41 → lyric.id=100 → lyric-0a.db 的 lyric_content
      正文 612 字（868d839e5747）→ 1204 字（bb445b9e9a00）
```

这一步仍然什么都没写。**逐条核对**，特别是「匹配到哪一行」对不对。

匹配不上的（磁盘有 `.lrc` 但库里找不到对应歌词行）会单独列出来，不会被动。

**第三步：真的写**

```bash
docker exec fnlrc python -m fnlrc fnos --app-data /fnos-data/appdata --appmeta /fnos-data/appmeta --music-root /music --apply
```

它会先备份（主库整份 + 这次涉及到的每个分片 + 旧正文的纯文本），然后逐条更新，再逐条回读校验。有失败的会列出来。

### 两条重要的提醒

**1. 先关掉飞牛的「自动下载歌词」。**

如果那个开关开着，飞牛可能在你写完之后又去在线抓一遍，把你的双语歌词覆盖成单语版。写之前确认一下它的状态。

**2. 写完如果没立刻生效，重启一下飞牛音乐应用。**

实测是立即生效、不用重启的。但不同版本可能有内存缓存，重启是安全的兜底动作。

### 出问题了怎么退回去

每次 `--apply` 都会在数据目录下建一个 `fnlrc-backup-<时间戳>/`：

```
fnlrc-backup-20260915-104233/
├── music.db              主库整份
├── lyric-0b.db           这次涉及到的分片
├── lyric-7.db
└── old-content.tsv       每一首的旧正文（纯文本）
```

**整体回退**：把备份里的 `.db` 文件拷回去覆盖。

```bash
B=/vol2/@appmeta/trim.music/fnlrc-backup-20260915-104233
cp $B/music.db /usr/local/apps/@appdata/trim.music/db/music.db
cp $B/lyric-*.db /vol2/@appmeta/trim.music/lyric-sqlite/
```

**只回退某一首**：从 `old-content.tsv` 里找到那一行，把旧正文写回去。

> ⚠️ 动手之前，**自己再手动备份一次**（脚本会备份，但多一层不亏）：
>
> ```bash
> cp /usr/local/apps/@appdata/trim.music/db/music.db /vol2/music.db.bak-$(date +%F)
> cp -r /vol2/@appmeta/trim.music/lyric-sqlite/ /vol2/lyric-sqlite.bak-$(date +%F)/
> ```

### 这个功能什么时候会失效

飞牛升级一次，如果改了数据库表结构，这个功能就用不了了。程序会先探测结构，**对不上就拒绝执行**，不会硬写坏你的库。

真失效了的话，就只剩「删库重扫」那条路（代价见 [FNOS-SIDECAR.md](FNOS-SIDECAR.md)）。

---

## 九、升级和卸载

**升级**：重新 `docker build`，然后停掉旧容器、用同样的 `docker run` 起新的。`fnlrc-data` 卷里的历史记录会保留。

**卸载**：`docker rm -f fnlrc`，然后删掉 `fnlrc-data` 卷。音乐库目录里的 `.lrc` 是你自己的文件，不会被删。

---

## 十、排错

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 打不开页面 | 端口没映射，或者容器没起来 | `docker ps` 看容器状态；`docker logs fnlrc` 看日志 |
| 页面打开但选目录没反应 | 音乐库没挂进去，或者路径填错 | `docker exec fnlrc ls /music` |
| 写文件失败 permission denied | 容器用户对目录没写权限 | 用「设置 → 测试写入权限」；调 `user:` 或目录属主 |
| 大量「无歌词」 | LRCLIB 在墙外连不上 | 正常现象，靠网易云也能跑 |
| 大量「超时」 | 网络慢或被限流 | 网络问题，或调大单曲超时 |
| 令牌不对 | 服务重启后令牌变了 | `docker logs fnlrc \| grep -i token` 重新取 |
| 局域网访问不了 | `-p` 只绑了回环 | 改成 `-p 8080:8080`（然后务必靠令牌保护） |
| 写完飞牛没变化 | 飞牛不重读已入库歌的 `.lrc` | 见上面第六节，或 [FNOS-SIDECAR.md](FNOS-SIDECAR.md) |

日志怎么看：

```bash
docker logs fnlrc --tail 100
```
