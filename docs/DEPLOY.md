# 部署指南（速查）

> 这一篇是**速查表**。想要一步步的教程，看这两篇：
> [GUIDE-DESKTOP.md](GUIDE-DESKTOP.md)（桌面版）、[GUIDE-WEB.md](GUIDE-WEB.md)（网页版）。
> 界面怎么操作看 [GUIDE.md](GUIDE.md)。

三种形态共用同一份核心代码，按使用场景任选：

| 形态 | 适合谁 | 优点 | 限制 |
|---|---|---|---|
| **桌面单 exe** | 普通用户 | 双击就用，无需 Python/命令行 | 只在本机跑；跨机器要重新下载 |
| **Docker / 网页版** | 有 NAS 或服务器的人 | 跑在 NAS 上直接读写本机音乐库，绕开 SMB 权限问题；多设备用浏览器访问 | 需要会 docker 一条命令 |
| **CLI / cron** | 想自动化的人 | 可定时、可脚本化 | 需要命令行 |

---

## 一、桌面版（Windows 单 exe）

### 打包步骤

```bash
# 1) 环境
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -r requirements.txt pyinstaller

# 2) 打包（scripts/build_windows.bat 等价于此）
.venv/Scripts/pyinstaller.exe --clean --noconfirm scripts/fnlrc.spec
# 产物：dist/fnlrc.exe
```

### `fnlrc.spec` 关键点

```python
a = Analysis(
    ['fnlrc/__main__.py'],
    datas=[('fnlrc/web', 'fnlrc/web')],      # ★ 前端必须打进包里
    hiddenimports=['uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
                   'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on'],
    ...
)
exe = EXE(..., console=False, name='fnlrc')   # console=False → 无黑框
```

- **`datas` 必须包含 `fnlrc/web`**，否则打包后打开是白屏
- 读静态文件要兼容打包环境：`base = getattr(sys, '_MEIPASS', os.path.dirname(__file__))`
- **uvicorn 需要显式 hiddenimports**，自动探测在冻结环境下会失败
- `console=False` 后所有输出写日志文件（`%LOCALAPPDATA%\fnlrc\logs\fnlrc.log`）

### 运行时依赖

| 依赖 | 是否必须 | 缺失后果 |
|---|---|---|
| **WebView2 Runtime** | Windows 桌面版必须 | 窗口打不开。Win10/11 通常自带；缺失时提示去微软官网安装 |
| 网络 | 抓歌词需要 | 离线时只能做「乱码修复 + 格式规范化」 |

### 分发注意事项

- 单文件 exe 约 **60–120MB**（含 Python 运行时 + 前端）；首启动会解压到临时目录，稍慢
- PyInstaller 产物**常被杀软误报**（这是 PyInstaller 的普遍问题）：建议发布时同时提供 zip 版、附上 SHA256、可选代码签名
- 用户数据目录：`%LOCALAPPDATA%\fnlrc\`（配置、日志、报告、预览输出）

---

## 二、Docker / 网页版

### 构建与运行

仓库里已有 `Dockerfile` 与 `docker-compose.yml`：

```bash
docker build -t fnlrc .
docker run -d --name fnlrc -p 127.0.0.1:8080:8080 \
  -v /vol2/1000/音乐:/music \
  -v fnlrc-data:/data \
  --env-file fnlrc.env \
  fnlrc
```

- **端口默认只映射到回环**（`127.0.0.1:8080`）。要让局域网访问就改成 `8080:8080`，
  但那就必须靠访问令牌保护
- **不要在命令行里写 `-e FNLRC_REMOTE_API_KEY=...`** —— 它会留在 shell history 和
  `docker inspect` 里。用 `--env-file fnlrc.env`
- **不要**把桌面依赖（pywebview）装进镜像
- 镜像基础是 `slim`，最终约 150–200MB

### 第一次打开：需要访问令牌

网页版不是从本机打开的，所以**不会自动带上令牌**。在服务器上取一次：

```bash
docker logs fnlrc 2>&1 | grep -i token
# 或进容器执行： docker exec fnlrc python -m fnlrc token
```

浏览器打开 `http://<NAS-IP>:8080`，首次会让你把令牌填进去（存在 localStorage）。
从本机（回环）打开 `http://127.0.0.1:8080` 则会自动注入，不用手填。

> 这个令牌和 API key 一样敏感 —— 只凭它就能触发写入和回滚。别贴进聊天、别提交到仓库。
> 怀疑泄露就 `docker exec fnlrc python -m fnlrc token --rotate` 重置。

### 用自己的大模型 API（可选）

不配也能跑 —— 默认只用源站自带的译词。想补翻源站漏掉的行就填一个：

```bash
printf 'FNLRC_REMOTE_API_KEY=sk-xxxx
' > fnlrc.env
docker run ... --env-file fnlrc.env ...
# 或者在网页版「设置 → 我的 API」里填 Base URL / 模型名 / Key
```

用的是 OpenAI 兼容协议，所以 DeepSeek 之外，自建的 vLLM / one-api / LM Studio
也能填进来，只要把 Base URL 指到那个端点。

### 直接跑在飞牛 NAS 上（推荐）

1. 飞牛 → Docker → 新建容器（或用上面的 compose）
2. 挂载音乐目录：**宿主机路径就是 `/vol2/1000/音乐`**（在飞牛文件管理器里能看到的那个路径）
3. 端口映射 `8080:8080`
4. `docker logs fnlrc | grep -i token` 拿令牌
5. 浏览器打开 `http://<NAS-IP>:8080`，填入令牌

**为什么推荐跑在 NAS 上**：
- 音乐目录是**本地路径**，不用经过 SMB/WebDAV，速度与权限问题一次性消失
- 飞牛里挂载 SMB 的用户和 WebDAV 的账号常常不是同一套凭据（实测踩过），在容器里直接读写可绕开

**权限**：容器内以 root 或指定 uid 运行；若目录属主是 `1000`，可用 `user: "1000:1000"` 或确保容器用户对音乐目录有写权限。
部署后第一件事建议先跑一次写入测试：

```bash
curl "http://127.0.0.1:8080/api/write-test?path=/music"
```

> 它会建一个 `fnlrc_write_test.tmp` 再删掉。**库内不留东西**，但在飞牛上删除会进回收站，
> 回收站里会多一条同名记录，属正常现象。

#### 想用「回填到飞牛」的话，要再加两个挂载

飞牛的数据在**两处、两个文件系统**上，只挂一处探测就会报「没找到」：

```yaml
volumes:
  - /vol2/1000/音乐:/music                                    # 音乐库
  - /usr/local/apps/@appdata/trim.music:/fnos-data/appdata     # 主库 music.db（系统盘）
  - /vol2/@appmeta/trim.music:/fnos-data/appmeta               # 歌词分片（存储池）
```

然后启动容器时加 `-e FNLRC_ENABLE_FNOS_WRITE=1`（默认关着，因为这是写另一个应用的数据库）。
设置面板里填 `/fnos-data/appdata` 和 `/fnos-data/appmeta`，先点「① 检测结构」和「② 预览差异」
（都只读），确认无误再点「③ 执行回填」。详见 [GUIDE-WEB.md](GUIDE-WEB.md) 第七、八节。

### 远端模式（PC 上跑 UI，连 NAS）

- 把 NAS 共享挂载为盘符（`Z:\`）或 UNC（`\\NAS\音乐`），Web UI 里选这个路径
- 或部署容器到任意一台机器，用 `-v` 挂载挂过来的 SMB/WebDAV 路径
- ⚠️ Windows 下**不要**指望用 shell 重定向写 UNC 路径（会失败）；本项目一律走 Python 原生路径读写，已验证可写

---

## 三、CLI / 定时任务

```bash
# 每天凌晨 3 点补一次（补新歌）
0 3 * * *  cd /opt/fnlrc && .venv/bin/python -m fnlrc --cli run \
             --root /vol2/1000/音乐 --write --translate source >> /var/log/fnlrc.log 2>&1
```

- 现有合规双语文件会自动跳过 → 日常运行只处理新增/异常的歌，通常几十秒完成
- `no_lyrics` / `timeout` 会留在报告里（`--report report.json`）。**二次补跑还没做**，
  要重试就重新跑一次全库（已成功的会跳过），或用 `--only <目录片段> --limit N` 缩小范围
- 退出码：`0` 正常 / `1` 用法或致命错误 / `2` 跑完但有失败项 —— 可直接用来判断要不要告警

---

## 四、排错速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 桌面版打开白屏 | `fnlrc/web` 没打进包 | 检查 spec 的 `datas` |
| 桌面版窗口不出现 | 缺 WebView2 | 安装 WebView2 Runtime |
| 打包后启动即崩 | uvicorn 动态模块没打进 | 补 `hiddenimports`（见上） |
| Web UI 能打开但选了目录没反应 | 容器没挂载音乐目录 | 检查 `-v` 映射与路径 |
| 写文件失败 permission denied | 容器用户对目录无写权限 | 调 `user:`/目录属主，或用「设置 → 测试写入权限」 |
| 大量 `no_lyrics` | 文件名词脏 / 冷门曲 / 外网不通 | 检查能否访问 lrclib.net；换「我的 API」补翻 |
| 大量 `timeout` | 网络慢或被限流 | 调大单曲超时、调大节流间隔 |
| 写完飞牛没变化 | **这是正常的**：飞牛只在歌曲首次入库时读一次旁挂 `.lrc`，已入库的路径不会再解析 | 新歌先补完再入库；已入库的歌见 [FNOS-SIDECAR.md](FNOS-SIDECAR.md)（三条出路，各有代价） |
| 已入库的歌想立即生效 | 需要让飞牛重新解析该曲 | 只能删库重扫（所有 `guid` 会变，歌单/收藏/历史失效），或改它的歌词库并保留 `lyric_id`（要碰另一个应用的 DB）|
| 杀软报毒 | PyInstaller 通病 | 加白名单 / 用 zip 版 / 代码签名 |
| 服务端口被占 | 8080 被占用 | 桌面版自动选随机端口；网页版换 `-p 8081:8080` |
