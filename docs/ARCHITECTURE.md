# 架构设计

> 目标：**一套核心，三端复用**（CLI / 桌面 GUI / 网页版），逻辑与界面彻底解耦，任一端都不需要重复实现业务规则。

---

## 一、设计原则（按优先级排序）

1. **绝不破坏用户的库**：所有写入前备份、所有操作可回滚、可预览不落盘
2. **核心零 UI 依赖**：`fnlrc/*.py` 只依赖标准库 + `requests` + `mutagen`，不 import 任何 Web/桌面框架
3. **零配置可用**：不填任何 key、不装任何东西，也要比现状好（源站译词 + 乱码修复 + 规则校验）
4. **能力可插拔**：数据源和翻译后端都是「插件式」的，一个源挂了不影响整体
5. **慢就是稳**：批量任务串行 + 全局节流，不做花哨并发（v0.1）

---

## 二、模块结构

```
fnlrc/
├── fnlrc/                    ← 核心（无 UI 依赖）
│   ├── __init__.py           版本号、对外导出
│   ├── lrc.py                ★ LRC 解析 / 规整 / 双语合成 / 飞牛规则校验
│   ├── library.py            ★ 扫描 / 标签 / 编码嗅探 / 纯音乐判定 / 库统计
│   ├── providers/            ★ 歌词源（协议 + 注册表 + 内置源 + 插件加载）
│   │   ├── __init__.py        公共工具（脱敏/节流/打分/对齐）+ 翻译后端 + fetch_lyrics 编排
│   │   ├── base.py            Provider 协议 + LyricResult/Candidate 数据类
│   │   ├── registry.py        源注册表（按 priority 排序）
│   │   ├── loader.py          发现 / 加载 $FNLRC_DATA/plugins（失败隔离）
│   │   └── builtin/           内置源：netease.py / lrclib.py（官方也是插件）
│   ├── pipeline.py           ★ 单曲决策 + 批量执行（三端唯一入口）
│   ├── manual.py             人工翻译：导出待译 → 校验 → 按原时间轴合成 + 记忆库
│   ├── watcher.py            监听文件夹：纯轮询、静默期、搬运、重试、状态落盘
│   ├── fnos.py               飞牛歌词库：探测 / 差异计划 / 原地回填（**零第三方依赖**）
│   ├── store.py               配置 / 访问令牌 / 任务报告
│   ├── i18n/                  ★ 界面文案（每语言一个文件）+ t()/set_lang()/detect()
│   │   ├── __init__.py         加载器：键查询、回退英文、语言探测
│   │   ├── zh.py               简体中文词表
│   │   └── en.py               英文词表（FALLBACK，键最全）
│   ├── server.py               FastAPI 应用（HTTP + SSE + 静态前端）
│   ├── desktop.py              pywebview 桌面壳
│   ├── cli.py                  命令行
│   ├── __main__.py             入口分发（桌面 / Web / CLI）
│   └── web/                    单页 UI（桌面与网页共用同一份）
│       ├── index.html          骨架与文案
│       ├── styles.css
│       └── js/                 ES module 拆分：state / core / fsbrowser / screens / panels / manual / main
│           └── i18n/           前端词表：index.js（t/applyI18n）+ zh.js / en.js
├── docs/                      文档（含本文）
├── scripts/                   打包脚本（PyInstaller / Docker）+ 诊断工具
├── tests/                     回归用例（含飞牛规则边界）
├── pyproject.toml
└── README.md
```

### 模块职责

| 模块 | 负责 | 明确不负责 |
|---|---|---|
| `lrc.py` | 文本层的 LRC 一切：解析、去制作名单、时间戳规整、双语合成、规则校验 | 网络、文件系统、UI |
| `library.py` | 文件系统层的库：遍历、标签、编码、乱码识别、语言预期、统计 | 网络、写入 |
| `providers/` | 外部世界的边界：**歌词源**（网易云、LRCLIB、社区插件）经 `Provider` 协议产出 `pairs`；`fetch_lyrics` 按 priority 串行降级；远端翻译后端（DeepSeek）、匹配打分与**译文对齐** | 决策（要不要翻、要不要写） |
| `pipeline.py` | **决策与编排**：调 `fetch_lyrics` 选源 → 校验 → 译文对齐/翻译降级 → 重试 → 写/不写 → 结果 | IO 细节、UI、具体某个源 |
| `manual.py` | **人工翻译**：`collect` 收集缺译文行 → `export`/`parse` 的 CSV/JSON 往返 → `merge` 校验并按**原时间戳**合成 → 记忆库（默认关） | 联网抓词（那是 providers）、要不要写库（由 CLI/HTTP 决定） |
| `watcher.py` | **监听文件夹**：轮询目录、判定"写完了沒"、调 `pipeline.process_song`、搬运已处理的文件、状态落盘去重、事件日志落盘保留（`WatchLog`） | 决策逻辑（复用 pipeline）、文件系统事件（纯轮询，见 ADR-019） |
| `fnos.py` | 飞牛数据库的只读探测、差异计划、原地回填（已有歌词）与**补建歌词行**（没有歌词的歌，要 `--allow-insert`） | 歌词的获取与翻译（它只负责"把已生成的 `.lrc` 搬进飞牛"） |
| `store.py` | 任务与结果的持久化、配置与令牌、回滚 | 业务逻辑 |
| `i18n/` | **界面文案**：键查询 `t()`、语言切换、优先级探测（`--lang` > `FNLRC_LANG` > config > 系统）；缺译为英文、再缺为键名 | 业务逻辑；**数据不翻译**（`status` 等仍是英文枚举，见 ADR-022） |
| `web/js/i18n/` | 前端同一套键名 + `applyI18n()`（`data-i18n` 属性）+ `setLang()` 即时切换 | 动态渲染（由各模块在渲染时调 `t()`） |
| `server.py` | HTTP 契约、SSE 进度、静态资源 | 业务逻辑（调用 pipeline） |
| `desktop.py` | 起本地服务 + 原生窗口 + 文件夹对话框 | 业务逻辑 |

**依赖方向严格单向**：`pipeline → providers/library/lrc`，`server/cli/desktop/watcher → pipeline`。核心永远不知道 UI 的存在。

`fnos.py` 刻意**不 import 本项目任何东西** —— 只用标准库，所以能单独 `scp` 到 NAS 上跑（见 [FNOS-SIDECAR.md](FNOS-SIDECAR.md)）。

---

## 三、数据模型

```python
@dataclass
class Song:                     # 一首歌的静态信息（library.load_song）
    path: str                   # 音频绝对路径
    lrc_path: str               # 同名 .lrc 路径（同目录同主名）
    rel: str                    # 相对库根的路径（UI 展示、报告用）
    title / artist / album: str
    language: str | None        # 标签原文，如 jpn/eng/rus/zxx
    duration: float | None
    instrumental: bool
    embedded_lyrics: str | None # 内嵌歌词（有则外挂会被压住）
    expected_lang: str | None   # zh/ja/latin/ru/instrumental/None
    expected_lang_source: str | None
                                # "tag" = 来自 language 标签，"dir" = 来自目录名猜测。
                                # 两者可信度差很多（实测「英语/」目录里躺着日语歌），
                                # 报告与 UI 要分开标注

@dataclass
class Config:                   # 一次任务的配置（pipeline.Config）
    write: bool                 # False = 只写 dump_dir 预览
    dump_dir: str | None
    force: bool
    translate: str              # source（默认，不调后端）| remote（自有 API）| none
    remote_base_url / remote_model: str
    remote_api_key: str         # field(repr=False) —— repr 里不会出现
    remote_timeout: int
    deadline_s: int = 25            # 单曲**抓取**总时限
    translate_deadline_s: int = 180 # 翻译阶段的独立预算（见下）
    backup: bool = True
    keep_local: bool = True         # 外文单语歌词沿用本地时间轴，只补译文（ADR-016）
    limit: int | None               # 只处理前 N 首
```

> **`--only` 不在 `Config` 里**，它在**选歌**那一步就生效（`library.select`）。
> 早先它被塞进 `Config` 但没有任何消费者 —— 网页版因此promise了一个假接口。
> 选歌顺序（先按路径筛、再截断）**只在 `library.select` 写一次**，CLI 与网页版都调它。

> 抓取与翻译为什么用两个时限：远端 API 翻一首长歌要按 chunk 发好几次请求，
> 共用 25 秒会把稍长的歌直接判成超时并降级为单语。

### 单曲结果（Report Row）

```python
{
  "file": "日语\\01 - 残酷な天使のテーゼ.flac",
  "title": "残酷な天使のテーゼ", "artist": "高橋洋子",
  "status": "written",           # 见下表
  "source": "netease",           # netease | lrclib | netease+translate
  "backend": "source",           # source | remote | none；keep-local 时会拼成 source+keep-local
  "pairs": 48,                   # 并行组数（飞牛双行判定用）
  "expected_lang": "ja", "lyric_lang": "ja",
  "validation": {"ok": true, "pair_ratio": 1.0, "leading_mono": 0, "karaoke_tags": false},
  "keep_local": false,           # true = 译文是按本地时间轴插的（status: written_keep_local）
  "note": "", "elapsed_s": 7.4
}
```

### 状态机（`status` 取值与 UI 处理）

| status | 含义 | UI 展示 | 后续动作 |
|---|---|---|---|
| `written` | ✅ 已写双语文件（正文按源站时间轴） | 绿色「双语 N 组」 | — |
| `written_keep_local` | ✅ 已写双语，但**沿用本地文件的时间轴与断句**，只把译文按同一时间戳插进去 | 绿色「双语·留本地轴」 | — |
| `written_mono` | ⚠ 写了单语（没有可用译文） | 黄色「单语」 | 可配翻译后端重跑 |
| `dumped` | 预览模式产出（未落库） | 蓝色「预览」 | 确认后再写 |
| `skipped_existing` | 已是合规双语/中文单语，跳过 | 灰色 | — |
| `skip_instrumental` | 纯音乐（`zxx` 或源站标记） | 灰色 | — |
| `no_lyrics` | 两个源都没有歌词 | 黄色 | 二轮补跑（清洗文件名） |
| `timeout` | 抓取超时未查完 | 黄色 | **可重试** |
| `lang_mismatch` | 外文歌抓到中文翻唱版歌词 | 🔴 待人工确认 | 人工指定源或跳过 |
| `validate_failed` | 双语合成未通过飞牛规则 | 🔴 | 检查成对比例 |
| `write_failed` | 写盘失败（权限/占用） | 🔴 | 检查权限 |
| `error` | 未预期异常 | 🔴 | 看日志 |

> `written_keep_local` 单独一档而不是并进 `written`：两者在"歌单/文件位置"上没差别，
> 但在**用户可见的后果**上有差别 —— 前者的原文行和时间戳一个都没动。
> 想要"只补译文、别碰我的轴"的用户需要能一眼分辨。汇总里两者都算「已写入」。
>
> **新增状态时务必同步 `run_batch()` 的统计分支** —— 那个 `if/elif` 链的 `else`
> 会把未知状态算成 `failed`。已有回归用例钉住这一点（`test_批量统计把保留本地轴算成已写入`）。

---

## 四、数据流

```
                        ┌─────────────┐
       库根路径 ───────▶│ library.scan│──▶ [Song, Song, ...]
                        └─────────────┘
                               │
                  (UI 先看：decide(song) 静态判定，不联网)
                               │
                        ┌──────▼──────┐
                        │ run_batch() │  逐首串行
                        └──────┬──────┘
                               │ 每首：
                    ┌──────────▼──────────┐
                    │ fetch_lyrics()      │  统一降级链：按 priority 问注册表里每个源
                    │  netease(10) → ...  │  网易云(原词+译词+罗马音) → LRCLIB(时间轴原词)
                    │  → lrclib(20) → ... │  单个源抛异常被隔离；全炸则报 error
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ 语言交叉校验         │  外文歌抓到中文版 → 拦截
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ 翻译：只补缺译文行   │  source（不调后端）/ remote（DeepSeek）
                    └──────────┬──────────┘
                               │
              ┌────────────────▼─────────────────┐
              │ 本地优先？（keep_local，默认开）  │
              │  本地是外文单语 .lrc 时：          │
              │  align_translation 把译文对到      │
              │  **本地时间轴**上 → validate       │
              └───────┬─────────────────┬────────┘
                      │ 通过            │ 不通过 / 不适用
                      │                 │
                      │        ┌────────▼──────────┐
                      │        │ lrc.build_bilingual│  同时间戳两行（源站轴）
                      │        │ lrc.validate       │  飞牛规则校验
                      │        └────────┬──────────┘
                      │                 │ 不通过 → 退回单语
                      └────────┬────────┘
                               │
                    ┌──────────▼──────────┐
                    │ writer: .bak + 写入  │
                    └──────────┬──────────┘
                               │
                        on_event(song) ──▶ SSE ──▶ UI 实时刷新

「本地优先」失败时**不静默**：原因会拼进结果的 note，让用户知道
"我以为保住了时间轴、其实被换掉了"。（`_keep_local_body` 的三个放弃理由）
```

---

## 五、HTTP API 设计（server.py）

前后端契约先定下来，桌面端和网页端完全一致。

| 方法 | 路径 | 说明 | 要令牌 |
|---|---|---|---|
| `GET` | `/` | 返回 `web/index.html`（**回环访问时注入访问令牌**） | — |
| `GET` | `/styles.css`、`/js/*` | 静态资源（前端是多文件，见 UI-SPEC §5） | — |
| `GET` | `/api/version` | 版本、可用的翻译后端、**界面语言**（`langs` / `default_lang`） | — |
| `GET` | `/api/auth` | **只读的令牌校验点**：令牌 + 同源校验（与写操作同一口径），无副作用。首次引导用它确认令牌 | ✅ |
| `POST` | `/api/scan` | `{root, limit?}` → 库统计（总数、已有双语、缺歌词、乱码、纯音乐、按目录分布） | — |
| `POST` | `/api/preview` | `{root, limit?}` → 每首歌的**静态判定**（`decide()`，不联网、秒回） | — |
| `POST` | `/api/run` | `{root, write, force, translate, keep_local?, limit?, only?}` → `{job_id}`，后台线程执行。`only` 是路径子串过滤，与 `limit` 一起用时**先筛后截** | ✅ |
| `GET` | `/api/jobs/{id}/events` | **SSE** 进度流（`EventSource` 不能带自定义头，令牌走查询串） | — |
| `GET` | `/api/jobs/{id}` | 任务快照（进度、统计、可续接） | — |
| `GET` | `/api/jobs` | 历史任务列表 | — |
| `POST` | `/api/jobs/{id}/stop` | 优雅停止（当前曲处理完即停） | ✅ |
| `GET` | `/api/report/{id}?status=&format=csv` | 结果列表，支持按状态筛选与 CSV 导出 | — |
| `POST` | `/api/rollback` | `{files: [...]}` → 从 `.bak` 恢复 | ✅ |
| `GET` | `/api/config` | 当前配置。**绝不回显 API key**，只给 `remote_configured` + `remote_key_masked`；含 `setup_needed` 供前端判断是否走引导，`lang` / `langs` 供界面语言选择 | — |
| `POST` | `/api/config` | 更新配置。`remote_api_key: "••••"` 表示"不修改" | ✅ |
| `POST` | `/api/test-remote` | 验证 Base URL / key / 模型名，返回**可区分的原因**（错 key / 余额不足 / 模型名错 / 限流） | ✅ |
| `GET` | `/api/fs?path=` | 目录浏览（网页版选目录用；桌面版走原生对话框） | — |
| `GET` | `/api/write-test?path=` | 测试目标目录能否写入（SMB 只读挂载 / 容器权限会在这里现形） | — |
| `POST` | `/api/fnos/preview` | `{app_data, appmeta, music_root?}` → 飞牛歌词库的**只读**探测或差异计划 | — |
| `POST` | `/api/fnos/apply` | `{app_data, appmeta, music_root, expect_changed}` → 原地回填 | ✅ |
| `GET` | `/api/watch/status` | 监听运行状态（`running` / `counts` / `seen` / `handled` / 各配置项） | — |
| `POST` | `/api/watch/start` | 按**已保存的** watch 配置启动监听线程（请求体被忽略）；校验目录存在、搬运目标存在、不同目录 | ✅ |
| `POST` | `/api/watch/stop` | 停止监听线程（当前这首处理完即停） | ✅ |
| `GET` | `/api/watch/events` | **SSE** 监听事件流（`{type:"watch",event,file,rel,result}`；令牌走查询串） | — |
| `GET` | `/api/watch/log` | 读回落的监听日志（`{events:[{ts,event,status,name,note,…}],count,hours}`）；关掉面板/重启后可回看 | — |
| `POST` | `/api/watch/log/clear` | 清空监听日志 | ✅ |
| `GET` | `/api/probe/ollama` | 410 Gone —— 本地模型后端已移除，保留此路由只为给旧前端一个明确的答复 | — |

`/api/run` 的 `keep_local` 省略时**跟随服务端配置**（默认开）。这样网页版的勾选框
能在"这一次"就生效，不用先保存设置；而没带这个字段的旧前端也不会被改变行为。

`/api/fnos/apply` 是**唯一会写另一个应用数据库**的接口，所以守卫比其它写操作多两道：
① 服务端要显式设 `FNLRC_ENABLE_FNOS_WRITE=1`（默认关）；② 请求里的 `expect_changed`
必须与**当下重新算出的**差异数一致 —— 预览之后库里状态变了就拒绝执行（409），
不会照着过期结果去写。

`/api/watch/start` **读已保存的配置**、忽略请求体。这样界面显示与实际行为不会分裂：
面板点「开始监听」时会先把输入框的值 `POST /api/config` 存一次，再 start。
监听线程是**纯轮询**（不依赖文件系统事件，SMB 上也能用），把已处理文件的状态
落盘 `watch-state.json`，重启后不重跑未变化的文件（详见 `watcher.py` 与 ADR-019）。

### 写操作的守卫（三层，缺一不可）

> 对 `/api/fnos/apply` 还多两道：服务端要 `FNLRC_ENABLE_FNOS_WRITE=1`，
> 且请求里的 `expect_changed` 必须与**当下重算的**差异数一致（见上表下方）。

1. **访问令牌**（`X-FNLRC-Token`）：首次启动生成、落盘、打印一次到日志
2. **同源校验**：`Origin` 必须与 `Host` 头一致，`Sec-Fetch-Site` 不得是 `cross-site`。
   挡的是「浏览器里的第三方页面偷偷对你的 localhost 发请求」。
   注意 `localhost` 与 `127.0.0.1` 是同一个回环接口的两种写法，必须互认
3. **绑回环不是授权边界**：任何本机进程都能发请求，所以**即使只绑 127.0.0.1 也要做第 2 条**

### SSE 事件格式

```jsonc
{"type":"start","total":520}
{"type":"song","index":48,"total":520,
 "result":{"file":"日语\\…","status":"written","pairs":48,"source":"netease",
           "keep_local":false,"elapsed_s":6.2}}
{"type":"done","stats":{"written":312,"written_keep_local":57,"written_mono":12,
                        "skipped":88,"no_lyrics":61,"timeout":7,
                        "lang_mismatch":3,"failed":0,"elapsed_s":4230}}
{"type":"stopped","index":115}
{"type":"error","message":"…"}
```

`written_keep_local` 是 `written` 的**子集**（`written` 里已经有它），单独列出来是为了
让前端能显示"其中 N 首保住了本地时间轴"。

监听事件流（`/api/watch/events`）的格式不同 —— 它是**一路持续**的，不分 job：

```jsonc
{"type":"watch","event":"processing","file":"C:\\inbox\\歌.flac","rel":"歌.flac","result":null}
{"type":"watch","event":"done","file":"C:\\inbox\\歌.flac","rel":"歌.flac",
 "result":{"status":"written","note":"","pairs":48,"…":"…"}}
```

`event` 只有 `processing` 与 `done` 两种；`done` 事件的 `result` 就是 `process_song`
的返回（含 `trigger:"watch"` 与 `path`，见报告）。期间服务端每 15 秒发一个
`: keep-alive` 注释行保活。

同一条事件除了走 SSE，还会被 `watcher.WatchLog` 落盘到 `$FNLRC_DATA/watch-log.jsonl`：
**默认保留 48 小时**（`watch_log_hours`，`<=0` 表示不保存），按时间戳滚动裁剪，
`GET /api/watch/log` 读回最近若干条。CLI 的 `fnlrc watch` 与服务端共用同一份文件。

### 任务与并发

- v0.1：**单任务串行**（一个 job 独占，避免把第三方源打爆）
- 任务状态存 SQLite（`jobs` / `items` 两张表），进程重启后可查看历史
- 预留 `workers` 配置（多曲并发），但默认 1，且共享全局节流器

---

## 六、三端复用

| 端 | 启动方式 | UI 载体 | 说明 |
|---|---|---|---|
| CLI | `python -m fnlrc --cli run …` | 无 | 直接调 `pipeline.run_batch()`，适合 cron/脚本 |
| 桌面 | 双击 `fnlrc.exe` | **pywebview 原生窗口** | 主线程起窗口；后台线程起 uvicorn（绑定 `127.0.0.1` 随机空闲端口）；窗口加载 `http://127.0.0.1:<port>/` |
| 网页 | `docker run -p 8080:8080` | 浏览器 | 同一个 `server.py`，绑 `0.0.0.0` |

### 桌面壳的关键点

1. **随机端口**：避免与其他程序冲突；启动后把实际端口告诉窗口
2. **就绪探测**：轮询 `/api/version` 成功后再加载页面，避免白屏
3. **原生文件夹对话框**：通过 pywebview 的 JS bridge 暴露 `pick_folder()`，UI 里「浏览」按钮优先走原生对话框（网页版则退回 `/api/fs` 目录浏览）
4. **单实例**：已有进程在跑时唤起旧窗口（v0.2）
5. **无控制台**：PyInstaller `--windowed`，所有日志写文件（`%LOCALAPPDATA%\fnlrc\logs`）

### 打包策略

| 目标 | 工具 | 要点 |
|---|---|---|
| Windows exe | PyInstaller `--onefile --windowed` | 把 `fnlrc/web/` 作为 data 打包；`pywebview` 依赖系统 WebView2（Win10+ 一般自带）；可选依赖缺失时降级 |
| Linux | PyInstaller / AppImage | 需要系统 WebKitGTK（pywebview 依赖） |
| Docker | `python:3.12-slim` | `uvicorn` 起服务；`-v` 挂载音乐库；镜像内不含桌面依赖 |

**可选依赖降级**：`mutagen`（标签）缺失 → 退回文件名解析；`pywebview` 缺失 → 退回浏览器模式；`fastapi`/`uvicorn` 缺失 → 只能 Web/CLI 之前的核心逻辑（`pipeline` 本身不依赖它们）。

### 前端结构（M5 起：单页 + 五个 Tab）

```
index.html
└── div.app
    ├── header.topbar     品牌 + nav.tabs--top（桌面可见）+ 报告/设置/历史
    ├── main.panels       5 × section.panel#panel0..4，[hidden] 切换，页面自然滚动
    └── nav.tabs--bottom  同一组 data-tab，仅 <768px 显示，固定屏幕底部
```

- **只有一套 Tab 逻辑**：顶部与底部是两个容器、共享 `data-tab`，由 CSS 决定显隐。
- `screens.js` 的 `initTabs()` 接点击与键盘（← →），`go(i)` 只做「显隐面板 +
  同步 `aria-selected` + 记住 `fnlrc_tab`」；`requestTab(i)` 是用户主动跳转（走锁定检查）。
- Tab 锁定用 `data-locked="true"` + CSS 置灰，**不 disable** ——
  点了给 toast 提示缺哪一步，用户可以先预览后面的流程。
- **全部元素 id 保持原样**（`#root` / `#doScan` / `#stats` / `#filterTabs` …），
  各模块的 `$("#id")` 一行没改，回归面最小。
- 手机断点（<768px）：底部 Tab 固定、`.panels` 预留 `padding-bottom`、
  取消表格/日志的内部滚动、触控目标 ≥44px。

### 界面语言（M4）

三端共用同一份词表结构，**每语言一个文件**：

| 端 | 词表位置 | 入口 | 缺译回退 |
|---|---|---|---|
| CLI / 服务端 | `fnlrc/i18n/<lang>.py` 的 `STRINGS` dict | `t("key", **vars)` | → 英文 → 键名本身 |
| 网页 | `fnlrc/web/js/i18n/<lang>.js` 的 `export default` | `t("key", vars)` / `applyI18n()` | → 英文 → 键名本身 |

- **优先级**：CLI `--lang` > `FNLRC_LANG` > `config.json` 的 `lang` > `zh`；
  网页 `localStorage["fnlrc_lang"]` > `navigator.language` > `zh`。
- **数据不翻译**：报告与 CSV 里的 `status` / `source` / `backend` 是英文枚举，
  只在展示层映射；`note` 保持中文。导出的文件不因语言而变（决策见 ADR-022）。
- **新增语言**：加一个文件 + 在两个加载器里各登记一行。前端是零构建的，
  浏览器不能扫目录，所以必须显式登记。
- **一致性由测试保证**：`tests/test_i18n.py`（后端键集合/占位符）、
  `tests/test_web_i18n.py`（前端键集合 + `index.html` 与 JS 里引用的键都存在 +
  前后端同名键中文值一致 + 语言列表一致）。

---

## 七、存储

- **v0.1：JSON**（`manifest.json` + `report.json`），简单、可直接看、好排错，满足「可重跑/可回滚」
- **v0.2：SQLite**（`jobs` / `items` 表），支持历史、筛选、多任务、续跑断点
- 回滚所需的最小信息：源文件路径 + `.bak` 路径 + 写入时间

---

## 八、安全与隐私

| 关注点 | 做法 |
|---|---|
| 第三方请求 | **全部由后端发起**（浏览器直连网易云会被 CORS 挡）；网页版即后端代理 |
| 凭据存放 | **只存在服务端**：环境变量 `FNLRC_REMOTE_API_KEY` 优先，否则 `config.json`（0600）。浏览器**永远拿不到回显**，只能问"配没配"和填新的 |
| 凭据外泄面 | `Config.remote_api_key` 带 `field(repr=False)`；`mask_secret()` 统一清洗日志/异常/报告字段；`remote_base_url` 落盘前剥掉查询串（挡 `?api_key=`）；`fnlrc/web/` 里没有任何地方能碰到 key |
| 写操作 | 访问令牌 + 同源校验（见第五章）。**绑回环不算授权边界** —— 本机任何进程都能发请求 |
| 网络暴露 | 桌面版只绑 `127.0.0.1`；容器部署建议只映射到回环，要对外必须让令牌生效 |
| 隐私 | 默认后端 `source` **不调任何后端**，歌词文本不出本机；只有显式选「我的 API」才会把歌词发给第三方 |
| 破坏性操作 | 写入前 `.bak` 备份；提供一键回滚；预览模式默认不落盘 |

---

## 九、可观测性

- 每首歌一行结构化日志：`[i/N] <status> <source> <file> 并行组=… 比例=… (耗时)`
- 结束后产出报告：统计 + 失败清单 + 待人工确认清单 + 可导出 CSV
- `timeout` / `no_lyrics` 自动成为「二轮补跑」的输入（清洗文件名 → 换关键词/换源）

---

## 十、演进方向

1. **二轮补跑器**：`no_lyrics`/`timeout` 自动重试（清洗后缀、换关键词、用专辑名/罗马音）
2. ~~**provider 插件化**：`providers/` 目录 + 注册表，社区可加源（QQ/酷狗/Musixmatch）~~ —— **已在 M2 落地**（见 ADR-020）。社区新源放 `$FNLRC_DATA/plugins/`，模板见 `docs/provider-template.py`
3. **翻译质量反馈**：记录人工修改，反哺关键词/模板
4. **并发**：共享节流器下的多曲并发（对网易云可行，对 LRCLIB 谨慎）
5. **飞牛应用化**：如果 fnOS 开放第三方应用打包，直接上架
