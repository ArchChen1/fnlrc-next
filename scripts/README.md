# scripts/ · 诊断工具与打包

这里的脚本都是**真实补完任务里跑出来的**，不是示例代码。

## 体检与诊断（日常最常用）

| 脚本 | 干什么 | 什么时候用 |
|---|---|---|
| `scan_library.py` | 库概览：总数、已有双语/单语/缺歌词/乱码/纯音乐，按目录分布；`--show-broken` 列出坏文件；`--spread` 预览跨目录抽样顺序 | 动手之前先看清库；换新库时 |
| `probe_write.py` | **测试目标目录能不能写** + 抽查标签完整度。`--subdirs N` 控制抽查几个子目录（默认 8，`--subdirs 0` 完全不查） | 部署后第一件事；写入报错时 |
| `scan_languages.py` | `language` 标签分布与缺失清单（含按目录推断的结果） | 想让「语言交叉校验」更准；P1 语言标签补全的输入 |
| `smoke_sources.py` | 数据源连通性与真实延迟（网易云 / LRCLIB / 已配置的翻译后端） | 大批量 `no_lyrics`/`timeout` 时先跑这个 |
| `debug_song.py` | **单曲全链路调试**：标签→预期语言→各源候选与打分→选中→语言校验→生成结果与双行判定 | 某首歌没配上/配错了/没双行时，抓一份它的输出即可定位 |

```bash
python scripts/scan_library.py   --root "\\\\NAS\\音乐" --show-broken 10
python scripts/probe_write.py    --root "\\\\NAS\\音乐" --sample 8
python scripts/scan_languages.py --root /vol2/1000/音乐 --list-missing 20
python scripts/smoke_sources.py
python scripts/debug_song.py     --file "\\\\NAS\\音乐\\日语\\某歌.flac" --preview 12
```

## 打包

| 文件 | 干什么 |
|---|---|
| `fnlrc.spec` | PyInstaller 配置（Windows 单 exe）。`datas` 必须含 `fnlrc/web`，否则打包后白屏 |
| `build_windows.bat` | 在仓库根目录执行 `scripts\build_windows.bat`，产物 `dist\fnlrc.exe` |

详见 [`docs/DEPLOY.md`](../docs/DEPLOY.md)。

---

## 约定

- 所有脚本**不写死路径**，一律通过 `--root` / `--file` 传入
- 编排逻辑（要不要翻、要不要写、写哪里）只存在一份：`fnlrc/pipeline.py`；脚本只调用它，不复制规则
- 诊断脚本**不改动音乐库内容**（`debug_song.py` 只写到 `_debug_out/`）

### 一个例外，说清楚

`probe_write.py` 会**建一个探针文件再删掉**（测目录能不能写，只能这么测）。
库内不会留东西，但**在开了回收站的系统上，删除会进回收站** —— 飞牛就是这样。
真机上实测到 17 条 `fnlrc_write_test.tmp` 躺在 `/vol2/1000/.@#local/trash/音乐/` 里。

所以它默认**只抽查 8 个子目录**（不是全部）。想彻底不留痕迹就 `--subdirs 0`，
只测根目录。清回收站里的残留：

```bash
find /vol2/1000/.@#local/trash/音乐 -name 'fnlrc_write_test.tmp' -delete
```
