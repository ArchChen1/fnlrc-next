"""极轻量国际化：每语言一个 `STRINGS` 字典文件，键相同、值不同。

为什么不用 gettext：本项目前端**零构建**、后端也尽量零工具链。
.po 要编译成 .mo 多一道构建步骤，而这里只有几种语言、文案量有限 ——
一个 dict 就够。将来真要上 gettext，键值结构不变，迁移成本低。

用法：
    from .i18n import t, set_lang
    print(t("cli.scan.root", root="/music"))

约定：
- 键名与前端 `fnlrc/web/js/i18n/*.js` **完全一致**（`status.written` 两边都用），
  便于对照维护，也方便将来用脚本校验两边的键是否齐。
- 缺 key 时回退到 FALLBACK 语言（英文），再缺就显示 key 本身 —— **绝不显示空白**。
- 数据（报告 JSON / CSV / `status` 枚举值）**不翻译**，只有展示层翻译。
"""

from __future__ import annotations

import os
import sys

from . import en, zh

# 显式登记每个语言 —— 新增语言 = 新增一个文件 + 这里加一行（决策 D3）
_CATALOGS: dict[str, dict[str, str]] = {"zh": zh.STRINGS, "en": en.STRINGS}

FALLBACK = "en"

# 语言代码 → 该语言自己的名字（切换下拉用，永远用本语言书写，不翻译）
LANGS: list[tuple[str, str]] = [("zh", "中文"), ("en", "English")]

_current = "zh"


def available() -> list[str]:
    """已登记的语言代码。"""
    return list(_CATALOGS)


def normalize(lang: str | None) -> str:
    """把 `zh-CN` / `en_US` / `EN` 这类写法收敛成受支持的语言代码。"""
    if not lang:
        return ""
    low = str(lang).strip().lower().replace("_", "-")
    if low in _CATALOGS:
        return low
    head = low.split("-")[0]
    return head if head in _CATALOGS else ""


def detect() -> str:
    """优先级：环境变量 FNLRC_LANG > 配置里的 lang > zh（默认保持中文，老用户不受影响）。"""
    for candidate in (os.environ.get("FNLRC_LANG"), _config_lang()):
        got = normalize(candidate)
        if got:
            return got
    return "zh"


def _config_lang() -> str:
    """读配置文件里的 lang。放在函数里 import，避免 store ↔ i18n 循环依赖。"""
    try:
        from .. import store

        return str(store.load_config().get("lang") or "")
    except Exception:
        return ""


def set_lang(lang: str | None) -> str:
    """切换语言。传了不支持的值就保持当前语言不变（不抛异常）。"""
    global _current
    got = normalize(lang)
    if got:
        _current = got
    return _current


def get_lang() -> str:
    return _current


def t(key: str, /, **kwargs) -> str:
    """取一条文案。

    查不到就回退 FALLBACK，再查不到就返回 key 本身 —— 漏翻译只会显示一个
    可读的键名，不会空白、不会报错（决策 D2）。
    """
    s = _CATALOGS.get(_current, {}).get(key)
    if s is None:
        s = _CATALOGS[FALLBACK].get(key)
    if s is None:
        s = key
    if not kwargs:
        return s
    try:
        return s.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        # 占位符对不上时宁可显示原文，也不要因为翻译文件写错而把命令打断
        return s


def missing_keys() -> list[str]:
    """所有**非 FALLBACK** 语言里缺的键 —— 给测试和 `--lang` 自检用。"""
    out: list[str] = []
    base = _CATALOGS[FALLBACK]
    for lang, catalog in _CATALOGS.items():
        if lang == FALLBACK:
            continue
        out.extend(f"{lang}:{k}" for k in base if k not in catalog)
    return sorted(out)


def _configure_console() -> None:
    """英文/符号在 cp936 控制台会 UnicodeEncodeError —— 宁可显示 ? 也别崩。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


__all__ = [
    "FALLBACK", "LANGS", "available", "normalize", "detect", "set_lang",
    "get_lang", "t", "missing_keys", "_configure_console",
]
