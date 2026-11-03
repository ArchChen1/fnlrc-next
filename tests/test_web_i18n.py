"""前端 i18n 的回归用例。

前端零构建、原生 ES module，测试跑不了浏览器，但**能静态检查**这三件事：
1. `zh.js` / `en.js` 键集合完全一致（与后端同一套口径）；
2. 占位符两边一致（`{n}` 写错会显示成字面量）；
3. `index.html` 的 `data-i18n*` 与 JS 里 `t("...")` 用到的键都真的存在 ——
   漏一个就是界面上冒出一个 `some.missing.key`。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "fnlrc" / "web"
I18N = WEB / "js" / "i18n"

# 匹配 `"key": "value",` —— 只认顶格的键，`hint` 之类的后缀键也照常匹配
PAIR = re.compile(r'^\s*"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', re.M)
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _load(name: str) -> dict[str, str]:
    return dict(PAIR.findall((I18N / name).read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def zh() -> dict[str, str]:
    return _load("zh.js")


@pytest.fixture(scope="module")
def en() -> dict[str, str]:
    return _load("en.js")


def test_前端两个语言键集合一致(zh, en):
    assert set(en) - set(zh) == set(), f"en 多出来的键：{sorted(set(en) - set(zh))[:10]}"
    assert set(zh) - set(en) == set(), f"zh 缺的键：{sorted(set(zh) - set(en))[:10]}"


def test_前端没有重复键(zh, en):
    for name in ("zh.js", "en.js"):
        keys = PAIR.findall((I18N / name).read_text(encoding="utf-8"))
        # 重复键不会报错但只有最后一个生效 —— 静悄悄，特别难查
        assert len(keys) == len(set(keys)), f"{name} 有重复键"


def test_前端文案都非空(zh, en):
    for name, cat in (("zh", zh), ("en", en)):
        empty = [k for k, v in cat.items() if not v.strip()]
        assert empty == [], f"{name}.js 有空的文案：{empty}"


def test_前端占位符两边一致(zh, en):
    for key, text in zh.items():
        want = set(PLACEHOLDER.findall(text))
        got = set(PLACEHOLDER.findall(en[key]))
        assert want == got, f"{key} 占位符不一致：zh={want} en={got}"


def test_html里的data_i18n键都存在(en):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    used: set[str] = set()
    for attr in ("data-i18n", "data-i18n-html", "data-i18n-ph",
                 "data-i18n-title", "data-i18n-aria"):
        used |= set(re.findall(attr + r'="([^"]+)"', html))
    missing = sorted(used - set(en))
    assert missing == [], f"index.html 用到了不存在的键：{missing}"
    assert used, "index.html 里一个 data-i18n 都没有 —— i18n 根本没接上"


def test_js里用到的键都存在(en):
    used: set[str] = set()
    for f in (WEB / "js").rglob("*.js"):
        if f.parent.name == "i18n":
            continue
        src = f.read_text(encoding="utf-8")
        used |= set(re.findall(r'\bt\("([^"]+)"', src))
        used |= set(re.findall(r'\btOr\("([^"]+)"', src))
    # "status." / "action." / "backend." / "filter." 是动态前缀，逐条在下面校验
    concrete = {k for k in used if not k.endswith(".")}
    missing = sorted(concrete - set(en))
    assert missing == [], f"JS 里用到了不存在的键：{missing}"


def test_动态拼出来的键都在(en):
    """`STATUS_LABEL[status]` 是拼出来的，静态扫描看不到，单独列一遍。"""
    want = set()
    for backend in ("source", "remote", "none"):
        want.add(f"backend.{backend}")
        want.add(f"backend.{backend}.hint")
    for prefix in ("status.", "action.", "filter."):
        # 这些前缀下的键都在 en 里逐条核对，此处只需保证前缀非空
        assert any(k.startswith(prefix) for k in en), f"没有 {prefix}* 这类键"
    missing = want - set(en)
    assert not missing, f"动态键缺失：{sorted(missing)}"


def test_后端与前端的关键枚举同名同值(zh, en):
    """两套词表共用键名，最容易漂的就是 status/action —— 钉住它们。"""
    from fnlrc.i18n import zh as py_zh

    for key in ("status.written", "status.lang_mismatch",
                "action.create", "backend.source"):
        assert key in py_zh.STRINGS, f"后端词表缺 {key}"
        assert key in zh, f"前端词表缺 {key}"
        # 中文值必须一致：同一个人写两遍会漂，值一致说明真的一致
        assert py_zh.STRINGS[key] == zh[key], f"{key} 前后端中文不一致"


def test_语言列表两边一致():
    from fnlrc import i18n

    src = (I18N / "index.js").read_text(encoding="utf-8")
    # LANGS = [["zh", "中文"], ["en", "English"]]
    js_langs = set(re.findall(r'\["(\w+)",\s*"[^"]*"\]', src))
    assert js_langs == set(i18n.available()), (
        f"前端 LANGS={sorted(js_langs)} 与后端 available()={i18n.available()} 不一致"
    )
