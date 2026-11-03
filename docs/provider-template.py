"""歌词源插件模板 —— 复制这个文件到你的插件目录，改几处就能用。

放置位置（默认）：`$FNLRC_DATA/plugins/你的源.py`
  Windows: `%LOCALAPPDATA%\\fnlrc\\plugins\\`
  其它:    `~/.local/share/fnlrc/plugins/`
  容器:    `$FNLRC_DATA/plugins/`（也可在设置里用 `plugins_dir` 指到别处）

放好后跑 `fnlrc provider list` 看它有没有被认出来；`fnlrc provider test 你的源 --file 某歌.flac`
跑一次契约自检。

改这三处：
  1. 类名与 name/label/priority
  2. available()  —— 缺 key / 未配置时返回 (False, "人话原因")
  3. fetch()      —— 返回 fnlrc.providers.base.LyricResult

只用标准库 + 本项目已带的 requests 就行；不要引入额外重依赖。
"""
from __future__ import annotations

from fnlrc import lrc as L
from fnlrc.providers import base           # req / score / duration_ok / lang_ok … 都在这
from fnlrc.providers.registry import register


class MyProvider:
    # ---- 必填元信息 ----
    name = "mysource"        # 唯一标识（小写、无空格）；也是 provider_keys 里的键名
    label = "我的歌词源"      # 显示名，会出现在 provider list 与报告里
    priority = 30            # 越小越先试（内置：网易云=10，LRCLIB=20）
    needs_key = False        # True 时 available() 应检查配置里的 key

    # 本源的限流间隔（秒）。第一次发请求前先声明，避免把源站打爆（验收标准 5）。
    min_interval = 1.0

    def available(self) -> tuple[bool, str]:
        """现在能不能用？缺 key / 被墙 / 未配置 → (False, "给用户看的原因")。"""
        # 需要 key 的源这样读（key 只存在服务端，不会进日志/报告）：
        #   from fnlrc import store
        #   key = (store.load_config().get("provider_keys") or {}).get(self.name)
        #   if not key:
        #       return False, "未配置 API key"
        return True, ""

    def fetch(self, song, cfg, *, deadline=None) -> "base.LyricResult | None":
        """取词。**必须**：尊重 deadline、用 base.req() 发请求、失败返回 None 而不是抛。

        song: fnlrc.library.Song —— 有 title / artist / album / duration / expected_lang
        cfg:  fnlrc.pipeline.Config
        返回的 pairs 形状固定为 [(毫秒, 原词, 译文|None, 罗马音|None), …]
        """
        # 声明一次节流间隔（幂等，重复调用无副作用）
        base.register_source_interval(self.name, self.min_interval)

        # —— 下面是示意代码，按你的源站改成真实调用 ——
        r = base.req("GET", "https://example.com/api/search", kind=self.name,
                     deadline=deadline, params={"q": f"{song.title} {song.artist}"},
                     timeout=10)
        if r is None or r.status_code != 200:
            return None
        try:
            items = (r.json() or {}).get("data") or []
        except Exception:
            return None

        # 用库里的公共打分挑候选；时长对不上的直接淘汰。
        best, best_score = None, 0.0
        for it in items:
            dur_ms = it.get("duration_ms") or 0
            if not base.duration_ok(dur_ms, song.duration):
                continue
            sc = base.score(it.get("name", ""), it.get("artist", ""),
                            dur_ms, song.title, song.artist, song.duration)
            if sc > best_score:
                best, best_score = it, sc
        if best is None or best_score < 4.0:
            return None

        # 把源站歌词解析成 (ms, 原词, 译文, 罗马音)。这里假设它直接给了时间轴原词。
        pairs = []
        for line in best.get("lines") or []:
            ms = int(line["time_ms"])
            pairs.append((ms, line["text"], line.get("trans"), line.get("roman")))
        if not pairs:
            return None

        return base.LyricResult(
            source=self.name,
            pairs=pairs,
            matched=f"{best.get('artist')} - {best.get('name')}",
            score=round(best_score, 2),
            lyric_lang=L.detect_lang(" ".join(p[1] for p in pairs)),
            candidates=[],
            alternatives={},
        )


# 方式 1：import 即注册（最简单）
register(MyProvider())

# 方式 2（等价）：定义 register() 钩子，由加载器调用
# def register():
#     from fnlrc.providers.registry import register as reg
#     reg(MyProvider())
