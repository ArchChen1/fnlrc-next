"""i18n 的回归用例。

钉死三件事：
1. 各语言的键集合与 FALLBACK（en）**完全一致**，不多不少 —— 漏翻会立刻发现；
2. 缺 key 时回退英文、再缺显示 key，**绝不空白、绝不抛异常**；
3. 占位符能对上（`{n}` 之类两边一致），写错的翻译不会把命令打断。
"""
from __future__ import annotations

import re

import pytest

from fnlrc import i18n


def test_每个语言的键集合与fallback一致():
    """多一个键 = 没人用的死文案；少一个键 = 漏翻译。两种都要当场发现。"""
    base = set(i18n._CATALOGS[i18n.FALLBACK])
    for lang, catalog in i18n._CATALOGS.items():
        if lang == i18n.FALLBACK:
            continue
        extra = set(catalog) - base
        missing = base - set(catalog)
        assert not extra, f"{lang} 有多余的键：{sorted(extra)[:10]}"
        assert not missing, f"{lang} 缺这些键：{sorted(missing)[:10]}"


def test_missing_keys为空():
    assert i18n.missing_keys() == []


def test_所有键都非空():
    for lang, catalog in i18n._CATALOGS.items():
        for k, v in catalog.items():
            assert isinstance(v, str) and v.strip(), f"{lang}:{k} 是空的"


def test_占位符两边一致():
    """`{n}` 在 zh 里写成 `{num}` 会 KeyError —— 静态检查比运行时才发现好。"""
    placeholders = re.compile(r"\{(\w+)\}")
    base = i18n._CATALOGS[i18n.FALLBACK]
    for lang, catalog in i18n._CATALOGS.items():
        if lang == i18n.FALLBACK:
            continue
        for key, text in catalog.items():
            want = set(placeholders.findall(base[key]))
            got = set(placeholders.findall(text))
            assert want == got, f"{lang}:{key} 占位符不一致：期望 {want}，实际 {got}"


@pytest.fixture(autouse=True)
def reset_lang():
    old = i18n.get_lang()
    yield
    i18n.set_lang(old)


def test_取中文文案():
    i18n.set_lang("zh")
    assert i18n.t("status.written") == "✓ 双语"


def test_取英文文案():
    i18n.set_lang("en")
    assert i18n.t("status.written") == "✓ Bilingual"


def test_带参数的文案():
    i18n.set_lang("en")
    assert i18n.t("cli.scan.total", n=42) == "Audio files: 42"
    i18n.set_lang("zh")
    assert i18n.t("cli.scan.total", n=42) == "音频文件: 42 首"


def test_缺key显示key本身而不空白():
    i18n.set_lang("zh")
    assert i18n.t("no.such.key") == "no.such.key"


def test_缺key回退到英文():
    """某语言漏了一条时，展示英文而不是空白 —— 决策 D2。"""
    i18n._CATALOGS["zh"]  # 现在的 zh 是完整的，这里临时挖掉一条来验证回退
    saved = i18n._CATALOGS["zh"].pop("status.written")
    try:
        i18n.set_lang("zh")
        assert i18n.t("status.written") == "✓ Bilingual"
    finally:
        i18n._CATALOGS["zh"]["status.written"] = saved


def test_参数对不上时不抛异常():
    i18n.set_lang("en")
    # 故意少传参数：宁可原样显示，也不要把 CLI 打断
    assert "{n}" in i18n.t("cli.scan.total")


@pytest.mark.parametrize("raw,want", [
    ("zh", "zh"), ("zh-CN", "zh"), ("zh_Hans_CN", "zh"),
    ("en", "en"), ("en-US", "en"), ("EN", "en"),
    ("ja", ""), ("", ""), (None, ""),
])
def test_语言代码归一化(raw, want):
    assert i18n.normalize(raw) == want


def test_不支持的语言不改变当前语言():
    i18n.set_lang("zh")
    assert i18n.set_lang("klingon") == "zh"


def test_环境变量可指定语言(monkeypatch):
    monkeypatch.setenv("FNLRC_LANG", "en-US")
    assert i18n.detect() == "en"


def test_默认是中文(monkeypatch):
    """老用户升级后不该突然变成英文界面。"""
    monkeypatch.delenv("FNLRC_LANG", raising=False)
    monkeypatch.setattr(i18n, "_config_lang", lambda: "")
    assert i18n.detect() == "zh"


def test_配置里的语言生效(monkeypatch):
    monkeypatch.delenv("FNLRC_LANG", raising=False)
    monkeypatch.setattr(i18n, "_config_lang", lambda: "en")
    assert i18n.detect() == "en"


def test_新增语言只需一个文件():
    """决策 D3：结构预留。这里模拟加一个语言，验证登记一行即可工作。"""
    fake = dict(i18n._CATALOGS[i18n.FALLBACK])
    fake["status.written"] = "XX"
    i18n._CATALOGS["xx"] = fake
    try:
        assert i18n.set_lang("xx") == "xx"
        assert i18n.t("status.written") == "XX"
        assert "xx" in i18n.available()
    finally:
        i18n._CATALOGS.pop("xx", None)


# ---------------------------------------------------------------- CLI 层


def _help(argv):
    """跑一次 `--help`，拿它输出的文本（argparse 会 SystemExit(0)）。"""
    import contextlib
    import io as _io

    from fnlrc import cli

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        cli.main(argv)
    return buf.getvalue()


def test_help跟随lang参数():
    """`fnlrc --lang en --help` 必须整段英文。

    argparse 的 help= 在 build_parser() 里就取了一次 t()，所以语言必须
    **在构建解析器之前**定下来 —— 否则 --lang 对帮助文本完全无效。
    这条用例就是钉住那个顺序。
    """
    en = _help(["--lang", "en", "--help"])
    assert "library overview" in en
    assert "库概览" not in en

    zh = _help(["--lang", "zh", "--help"])
    assert "库概览" in zh
    assert "library overview" not in zh


def test_help支持等号写法():
    assert "library overview" in _help(["--lang=en", "--help"])


def test_lang放在子命令后面也不报错():
    """`fnlrc scan --lang en` 现在不该 succeeded（缺 --root），但不该因解析器崩掉。"""
    from fnlrc import cli

    with pytest.raises(SystemExit) as e:
        cli.main(["scan", "--lang", "en"])
    assert e.value.code == 2          # 缺 --root，是用法错误


def test_子命令说明全部走i18n():
    """cli.py 里不该再出现写死的中文 help=。"""
    import io as _io
    import pathlib
    import re

    src = _io.open(pathlib.Path(__file__).parent.parent / "fnlrc" / "cli.py",
                   encoding="utf-8").read()
    bad = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not re.search(r"[\u4e00-\u9fff]", line):
            continue          # 注释是写给维护者看的，不要求翻译
        if "help=" in line or "description=" in line or "add_parser(" in line:
            bad.append(stripped)
    assert not bad, "这些 help 还没走 i18n：\n" + "\n".join(bad)
