# 桌面版：怎么拿到、怎么跑

## 先说清楚：现在没有现成的 exe

仓库里只有打包脚本，**还没有发布过编译好的 `fnlrc.exe`**。所以你得自己打一次包。

一次性的，五分钟。打完之后 exe 就在你手上，可以拷到别的机器上用。

不想折腾的话，用网页版也一样——同一份界面，只是跑在浏览器里。见 [GUIDE-WEB.md](GUIDE-WEB.md)。

---

## 一、准备

需要 Windows 10 或 11，加上 [uv](https://docs.astral.sh/uv/)（一个包管理工具，装一次就行）：

```bash
# 装了 winget 的话
winget install astral-sh.uv

# 或者用官方脚本（PowerShell）
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

uv 会自己下载 Python，所以**你不需要预装 Python**。

## 二、打包

把仓库下载下来（`git clone` 或者直接下 zip 解压），进到根目录，双击运行：

```
scripts\build_windows.bat
```

或者在命令行里：

```bash
scripts\build_windows.bat
```

它会做三件事：

1. 建一个 `.venv` 虚拟环境（Python 3.12）
2. 装依赖：`requests`、`mutagen`、`fastapi`、`uvicorn`，加上 `pywebview`（原生窗口）和 `pyinstaller`
3. 打包成 `dist\fnlrc.exe`

第一次跑要下载依赖，大概两三分钟。之后改代码再打包会快很多。

**产物是 `dist\fnlrc.exe`，单个文件，60–120MB。** 里面塞了 Python 运行时和整个前端，所以体积不小。

> 那个 `pywebview` 是必须的。`requirements.txt` 里它是注释掉的（因为服务器部署不需要它），
> 但打包脚本会显式装上——少了它，打出来的 exe 不会开原生窗口，只会弹个浏览器。

## 三、运行

双击 `dist\fnlrc.exe`。

会弹出一个原生窗口，加载界面的第一屏。**不会弹黑色的控制台窗口**——打包时设了 `console=False`。

首次启动会稍微慢一点（要把资源解压到临时目录），大概两三秒。

然后就是正常使用：选文件夹 → 扫描 → 看预览 → 开始补完。具体操作看 [GUIDE.md](GUIDE.md)。

### 如果窗口没出现

多半是缺 **WebView2 Runtime**。

Windows 11 和新版 Windows 10 一般自带，但不保证。缺了的话去微软官网下载安装：
搜「WebView2 Runtime 下载」，装 Evergreen Bootstrapper 那个。

装完再双击一次就行。

**如果 pywebview 也没装上**（比如你手动跑的打包命令，漏了它），程序会自动退回「打开浏览器」模式：
起一个本地服务，然后用你的默认浏览器打开。功能一模一样，只是没有原生窗口、也没有原生文件夹对话框。

## 四、它把东西存在哪

```
%LOCALAPPDATA%\fnlrc\
├── config.json      设置（API key 也在这里）
├── token            网页版的访问令牌
├── logs\fnlrc.log   日志
└── jobs\            每次跑完的报告
```

在资源管理器地址栏里粘 `%LOCALAPPDATA%\fnlrc` 就能打开。

**出问题先看 `logs\fnlrc.log`。** 因为打包后没有控制台，所有输出都在那里。

想重置配置，把 `config.json` 删掉就行。想彻底清干净，整个目录删掉。

> ⚠️ `config.json` 里可能有你的 API key。别把这个文件发给别人、别提交到 git。

## 五、分发的时候注意

如果你打包是想给别人用：

**杀毒软件可能报毒。** 这是 PyInstaller 的通病，不是这个项目的问题——它把 Python 运行时塞进单文件的做法，跟某些恶意软件的打包方式长得像。

能做的：

- 同时提供一个 zip 版（不是单文件，是文件夹），误报率低一些
- 附上 SHA256，让用户能核对
- 有条件的话做代码签名（要买证书，几百到几千一年）

**别用 UPX 压缩。** 打包脚本里已经关掉了，因为压过之后误报率明显上升。

## 六、桌面版特有的东西

**原生文件夹对话框。** 第一屏点「浏览…」弹的是系统对话框，跟网页版的目录浏览框不一样，更顺手。

**随机端口。** 程序内部会起一个本地服务（界面是从那儿加载的），端口是随机挑的空闲端口，不会跟别的东西撞。只绑 `127.0.0.1`，外面的机器访问不到。

**访问令牌照样有。** 桌面版也要令牌，写操作一样校验。只是因为它绑在回环、又是你自己那台机器，打开页面时令牌是自动注入的，你看不到它。

---

## 七、把结果弄进飞牛

这一节是桌面版用户最容易卡住的地方，所以写细一点。

桌面版处理的通常是 `\\NAS\音乐` 这种网络路径——写出来的 `.lrc` 确实落在 NAS 上了，
但**飞牛不会去读它**。原因见 [FNOS-SIDECAR.md](FNOS-SIDECAR.md)：
飞牛只在歌**第一次入库**时读一次同目录的 `.lrc`。

所以分两种情况。

### 情况一：这些歌还没进飞牛库（推荐做法）

那就简单——**处理完再入库**：

```
1. 新歌先放在一个暂存文件夹里（比如 \\NAS\暂存），别放进飞牛音乐库
2. 桌面版选这个暂存文件夹，补完
3. 补完之后，在飞牛的文件管理器里把歌整个移到音乐库
4. 回飞牛音乐点一次扫描
```

这样 `.lrc` 和音频是**一起**被飞牛第一次看到的，它自然会读。零风险，也不用碰任何数据库。

**以后都按这个顺序来，就不会遇到这个问题。**

### 情况二：歌已经在飞牛库里了

桌面版够不到飞牛的数据库——`/vol2/@appmeta/` 是应用的私有目录，SMB 里根本看不到，
只有登录到 NAS 本机、而且是 root 才能访问。

所以要走这条路：**把 `fnos.py` 传到 NAS 上，在 NAS 本机跑一次**。

#### 第一步：打开飞牛的 SSH

飞牛是 Debian 系，**SSH 默认关着**，得手动开：

1. 飞牛桌面 → 「系统设置」
2. 找「终端与远程」或者「SSH」（不同版本位置略有差别）
3. 打开开关，端口默认 22
4. 记住你的登录用户名，就是平时登飞牛那个

Windows 10/11 自带 `ssh` 命令，在 PowerShell 或终端里敲：

```bash
ssh 你的用户名@NAS的IP
```

会问密码，就是你的飞牛登录密码。进去了说明通了。

#### 第二步：把脚本传上去

**在你的 Windows 电脑上**（不是 NAS 上）执行，先切到仓库目录：

```bash
scp fnlrc\fnos.py 你的用户名@NAS的IP:/tmp/
```

传完可以回到 SSH 里确认一下：

```bash
ls -la /tmp/fnos.py
```

#### 第三步：先看，别急着写

SSH 连着 NAS，跑这三条。前两条**只读，什么都不改**。

```bash
# ① 确认结构对不对
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music
```

看到 `✅ 结构探测通过` 才能往下。如果是「结构与预期不符」，停手，把输出记下来。

```bash
# ② 看会改哪些歌
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music --music-root /vol2/1000/音乐
```

（`--music-root` 填你 NAS 上音乐库的真实路径，不是 `\\NAS\音乐`，也不是映射的盘符。）

它会列出每一首准备怎么改，而且注明**凭什么匹配到哪一行**：

```
  · 残酷な天使のテーゼ.lrc
      track.id=41 → lyric.id=100 → lyric-0a.db 的 lyric_content
      正文 612 字（868d839e5747）→ 1204 字（bb445b9e9a00）
```

**逐条核对。** 重点是「匹配到哪一行」对不对——如果一首歌配到了莫名其妙的地方，说明它的 guid 对不上，
那就得先搞清为什么。匹配不上的会被单独列出来，而且**不会被动**。

这一步还是没有写任何东西。

#### 第四步：自己再备份一次

脚本自己会备份，但你自己再备一层不亏：

```bash
cp /vol2/@appmeta/trim.music/music.db /vol2/music.db.bak-$(date +%F)
cp -r /vol2/@appmeta/trim.music/lyric-sqlite/ /vol2/lyric-sqlite.bak-$(date +%F)/
```

#### 第五步：真的写

```bash
# ③ 执行
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music --music-root /vol2/1000/音乐 --apply
```

它会先备份，再逐条写，然后逐条回读校验。跑完会告诉你改了多少首、备份在哪。

想连"库里一行歌词都没有"的歌一起填（真机库里约占四分之一），再加一个 `--allow-insert`：

```bash
sudo python3 /tmp/fnos.py --app-data /usr/local/apps/@appdata/trim.music --appmeta /vol2/@appmeta/trim.music --music-root /vol2/1000/音乐 --allow-insert --apply
```

它给这类歌新建一条歌词记录（主库索引行 + 分片正文，`source=2`）。**已有歌词的歌永远不会被新建** ——
真机实测那样做的话 `preferred` 仍指向旧行，用户会以为没生效。

```bash
# ④ 完事之后把 SSH 关掉（可选，但建议）
```

回飞牛里把 SSH 开关关掉。

#### 如果没生效

先重启一下飞牛音乐应用。实测是立即生效、不用重启的，但不同版本可能有内存缓存。

还有一个要先确认的：**飞牛的「自动下载歌词」开关如果是开着的**，它可能在你写完之后
又去在线抓一遍，把你的双语歌词覆盖成单语版。写之前先看一眼那个开关。

### 不想开 SSH 的话：用一次性容器

如果你的 NAS 上有 Docker（飞牛自带），可以不起常驻容器，就跑一次性的：

```bash
# 先把 fnos.py 放到 NAS 的 /tmp 下（随便什么方式，共享文件夹拷过去也行）
docker run --rm \
  -v /usr/local/apps/@appdata/trim.music:/fnos-data/appdata \
  -v /vol2/@appmeta/trim.music:/fnos-data/appmeta \
  -v /vol2/1000/音乐:/music \
  -v /tmp:/srv \
  python:3.12-slim \
  python /srv/fnos.py --app-data /fnos-data/appdata --appmeta /fnos-data/appmeta --music-root /music
```

两个数据目录**都要挂**：主库 `music.db` 在系统盘，歌词正文在存储池，是两个文件系统。
只挂一个，探测就会报「没找到」。

`--rm` 表示跑完就把容器删掉，不留东西。看差异没问题了，最后加上 `--apply` 再跑一次。

这条路不需要 SSH，但需要你能在 NAS 上执行 docker 命令（飞牛的 Docker 界面有「终端」，或者用 SSH）。

> ⚠️ 这是**改另一个应用的数据库**。它只改歌词正文、保留 `lyric_id`，所以歌单、收藏、
> 播放历史不受影响；飞牛升级改了表结构它会拒绝执行（不会硬写）。但风险仍然比"写个 `.lrc` 文件"高。
> **备份别省。**

---

## 排错

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 双击没反应 | 缺 WebView2 | 去微软官网装 WebView2 Runtime |
| 弹了浏览器而不是窗口 | 打包时没装 pywebview | 重跑 `scripts\build_windows.bat` |
| 打开是白屏 | 前端文件没打进包里 | 看 `logs\fnlrc.log`；重跑打包脚本（`datas` 那段没改过的话不会出这个问题） |
| 窗口出现但选了目录没反应 | 路径不对或没权限 | 先在第一屏确认路径能在资源管理器里打开 |
| 杀软报毒 | PyInstaller 通病 | 加白名单，或者用源码直接跑 |
| 启动就崩 | uvicorn 的模块没打进包 | 看日志；`scripts/fnlrc.spec` 里的 `HIDDEN` 那段就是为这个准备的 |

**不想打包的话**，直接在源码目录跑也是一样的：

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -r requirements.txt "pywebview>=5.0,<6"
.venv/Scripts/python.exe -m fnlrc --desktop
```

`--desktop` 就是开原生窗口；不带参数直接跑 `python -m fnlrc` 也行，它会优先开窗口。
