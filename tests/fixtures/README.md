# tests/fixtures/ · 离线样本

用于**离线契约测试**：不联网也能验证解析、打分、编码嗅探的逻辑。

⚠️ **这些是合成样本，不是录制下来的真实响应。** 第三方内容会变、也含个人信息，
所以只保留"结构一致"这一点，字段值都是编的。

## 文件清单

| 文件 | 内容 | 用于验证 | 消费者 |
|---|---|---|---|
| `netease_search.json` | 搜索响应：同名曲、翻唱版、器乐版各一条 | 打分函数的排序与淘汰 | `test_matching.py` |
| `netease_lyric.json` | 歌词响应：`lrc` + `tlyric` + `romalrc` + `klyric`，含制作名单行 | 时间戳配对、制作名单剔除、译文取用 | `test_matching.py` |
| `netease_lyric_roman_mixed.json` | `lrc` 字段里**罗马音与原文混排** | 行角色分类（`split_orig_roles`） | `test_matching.py` |
| `lrclib_get.json` | `/api/get` 命中响应（`syncedLyrics` + `instrumental: false`） | `syncedLyrics` 解析、`instrumental` 标记、时长硬淘汰 | `test_matching.py` |
| `lrc_gbk.lrc` | GBK 编码的中文歌词 | 编码嗅探（要能正确解码，不能当乱码） | `test_encoding.py` |
| `lrc_broken_mojibake.lrc` | 占位文本被**按 latin-1 误读**后的乱码 | `looks_broken()` 的 `mojibake:*-as-latin-1` | `test_encoding.py` |
| `lrc_broken_gbk_as_big5.lrc` | GBK 字节被**按 Big5 解码**后存成 UTF-8 | `looks_broken()` 的 `mojibake:gbk-as-big5` | `test_encoding.py` |

最后一条是**真机实测到的形态**，也是最容易漏掉的一种：它是**合法 UTF-8**、
没有替换字符、也不是单字节那类乱码，只做 `latin-1` 方向的反推抓不到。
生成方式（需要复现时照这个来）：

```python
orig = "此歌曲为没有填词的纯音乐，请您欣赏"
mojibake = f"[00:00.00]{orig}\n".encode("gbk").decode("big5", "ignore")   # → 存成 UTF-8
```

## 约定

放**输入样本**，不是期望输出的快照 —— **不要写**「断言歌词文本等于某段固定文字」
这类变更检测器（第三方内容会变）。要断言的是**关系与行为**：

- 排序（哪个候选排第一）
- 淘汰（时长差 520s 的 Live 版必须被丢掉）
- 配对（同一时间戳两行 → 一组）
- 降级（不成对 → 退回单语，而不是什么都不写）
- 异常不外泄（API key 不得出现在任何 note / 日志 / 报告里）

## 怎么加新样本

优先**手工构造**（像上面那样，字段结构照真实响应抄，值编造）。
真要录制的话用 `scripts/smoke_sources.py`，并把响应体存下来 —— 注意脱敏，
别提交个人信息。加完记得在 `tests/conftest.py` 的 `fixture_json` / `fixture_bytes`
之外，把上表补一行。
