"""CLI 退出码与选歌顺序。

退出码是 cron 判断成败的唯一依据（`0` 正常 / `1` 用法或致命 / `2` 跑完但有失败项），
而它此前**零测试覆盖** —— 改坏了没有任何东西会响。

`--only` 与 `--limit` 的**顺序**也在这一层：两者单独用都正常，合起来才坏
（实测 `--only 英语 --limit 3` 会空选 + 退出码 1），所以要有回归钉住。
"""
from __future__ import annotations

import pytest

from fnlrc import cli
from fnlrc import library as lib
from fnlrc import pipeline as pl


# ---------------------------------------------------------------- 夹具
def make_lib(tmp_path, layout):
    """layout: {"中文": 2, "英语": 40} → 造出对应数量的假音频。"""
    root = tmp_path / "音乐"
    for top, n in layout.items():
        (root / top).mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (root / top / f"song{i:03d}.flac").write_bytes(b"")
    return root


@pytest.fixture
def lib_root(tmp_path):
    return make_lib(tmp_path, {"中文": 3, "英语": 40})


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(pl.P, "netease_fetch", lambda *a, **k: None)
    monkeypatch.setattr(pl.P, "lrclib_fetch", lambda *a, **k: None)


# ---------------------------------------------------------------- 选歌顺序
def test_only与limit一起用时先筛后截(lib_root):
    """`--only 英语 --limit 3` 必须给出 3 首英语歌。

    反例：先 `scan(limit=3)` 截断，再拿 `英语` 过滤 —— 前 3 个文件不在 英语/ 下，
    结果是空选、退出码 1。两者单独用都正常，合起来才坏。
    """
    picked = lib.select(str(lib_root), only="英语", limit=3)
    assert len(picked) == 3
    assert all(s.rel.startswith("英语") for s in picked), [s.rel for s in picked]


def test_只有limit时数量对且不做过滤(lib_root):
    picked = lib.select(str(lib_root), limit=5)
    assert len(picked) == 5


def test_只有only时给出全部命中的(lib_root):
    picked = lib.select(str(lib_root), only="中文")
    assert len(picked) == 3
    assert all(s.rel.startswith("中文") for s in picked)


def test_only不区分大小写且匹配子串(lib_root):
    assert len(lib.select(str(lib_root), only="英")) == 40
    assert len(lib.select(str(lib_root), only="SONG")) == len(lib.select(str(lib_root)))


def test_既没有only也没有limit时给全库(lib_root):
    assert len(lib.select(str(lib_root))) == 43


# ---------------------------------------------------------------- 退出码
def test_路径不存在时退出码1(tmp_path, capsys):
    assert cli.main(["run", "--root", str(tmp_path / "并不存在")]) == 1
    assert cli.main(["scan", "--root", str(tmp_path / "并不存在")]) == 1


def test_空目录时退出码1(tmp_path):
    empty = tmp_path / "空"
    empty.mkdir()
    assert cli.main(["run", "--root", str(empty)]) == 1


def test_only与limit在CLI上不再空选(lib_root, tmp_path, offline, capsys):
    """回归：这条以前打印「没有要处理的歌」并返回 1。"""
    rc = cli.main(["run", "--root", str(lib_root), "--only", "英语", "--limit", "3",
                   "--dump", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert "没有要处理的歌" not in out
    assert rc == 0, out
    assert "3 首" in out


def test_正常跑完退出码0(lib_root, tmp_path, offline):
    assert cli.main(["run", "--root", str(lib_root), "--limit", "2",
                     "--dump", str(tmp_path / "out")]) == 0


def test_全部跳过也是退出码0(lib_root, tmp_path, offline):
    """没有失败项就是成功 —— 哪怕一首都没写（源站没词也是合法结果）。"""
    assert cli.main(["run", "--root", str(lib_root), "--limit", "1",
                     "--dump", str(tmp_path / "out")]) == 0


def test_有失败项时退出码2(lib_root, tmp_path, monkeypatch):
    """`failed > 0` 必须映射成 2 —— cron 靠这个区分「跑完了」和「跑出事」了。"""
    def boom(*a, **k):
        raise RuntimeError("模拟数据源崩溃")

    monkeypatch.setattr(pl.P, "netease_fetch", boom)
    rc = cli.main(["run", "--root", str(lib_root), "--limit", "1",
                   "--dump", str(tmp_path / "out")])
    assert rc == 2


def test_报告文件写了就能读回(lib_root, tmp_path, offline):
    """`--report` 产出的 JSON 要能被 `report` 子命令消费（下游脚本的正规用法）。"""
    import json

    rep = tmp_path / "report.json"
    assert cli.main(["run", "--root", str(lib_root), "--limit", "2",
                     "--dump", str(tmp_path / "out"), "--report", str(rep)]) == 0
    d = json.loads(rep.read_text(encoding="utf-8"))
    assert set(d) >= {"root", "stats", "results", "elapsed_s"}
    assert cli.main(["report", "--from", str(rep)]) == 0
    assert cli.main(["report", "--from", str(rep), "--csv", str(tmp_path / "r.csv")]) == 0
    assert (tmp_path / "r.csv").read_text(encoding="utf-8-sig").strip()


# ---------------------------------------------------------------- 无参数启动的分发
def test_无参数启动走原生窗口而不是退回浏览器(monkeypatch):
    """`pywebview` 是 PyPI **包名**，可导入的**模块名**是 `webview`。

    `__main__._auto()` 曾经写成 `import pywebview` —— 它永远 ImportError，
    于是**双击 exe（= 不带参数启动）永远退回浏览器模式**，原生窗口根本起不来。
    而 `--desktop` 走的是 `desktop.run()`，两处的 import 名不一致，
    所以只测 `--desktop` 永远发现不了这个 bug。

    这里把两条路都打桩成不同返回值，断言走的是桌面那条。
    """
    import sys
    import types

    from fnlrc import __main__ as m
    from fnlrc import cli, desktop

    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    monkeypatch.setattr(desktop, "run", lambda: 42)          # 走了桌面
    monkeypatch.setattr(cli, "serve", lambda **kw: 99)       # 退回了浏览器

    assert m.main([]) == 42, "无参数启动没有走原生窗口（是不是 import 名写错了？）"


def test_没有pywebview时退回浏览器模式(monkeypatch):
    """装了才有原生窗口；没装也不能崩，退回网页版至少还能用。"""
    import sys

    from fnlrc import __main__ as m
    from fnlrc import cli

    monkeypatch.setitem(sys.modules, "webview", None)        # import 会失败
    monkeypatch.setattr(cli, "serve", lambda **kw: 99)

    assert m.main([]) == 99


def test_桌面与主入口的模块名一致():
    """两处必须 import 同一个模块名 —— 它们不一致过，见上面那条用例。

    用 AST 看**真正的 import 语句**，不是搜字符串：注释里提到 `import pywebview`
    是在解释这个坑，不该被判成违规。
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "fnlrc"
    for f in ("__main__.py", "desktop.py"):
        tree = ast.parse((root / f).read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names}
        assert "pywebview" not in mods, f"{f} 把 PyPI 包名当成模块名导入了"


# ---------------------------------------------------------------- provider 子命令（M2）
@pytest.fixture
def isolated_data(monkeypatch, tmp_path):
    """把数据目录隔离到临时目录，避免加载到用户真实的 plugins/。"""
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path / "data"))
    from fnlrc import store
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path / "data")
    return tmp_path / "data"


def test_provider_list列出内置源(isolated_data, capsys):
    rc = cli.main(["provider", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "netease" in out and "lrclib" in out
    assert "网易云" in out and "LRCLIB" in out


def test_provider_list能认出用户插件(isolated_data, capsys):
    plugins = isolated_data / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "mysrc.py").write_text(
        "from fnlrc.providers import registry\n"
        "class P:\n"
        "    name='mysrc'; label='我的源'; priority=30; needs_key=False\n"
        "    def available(self): return False, '未配置'\n"
        "    def fetch(self, song, cfg, *, deadline=None): return None\n"
        "registry.register(P())\n", encoding="utf-8")
    rc = cli.main(["provider", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "mysrc" in out and "我的源" in out
    assert "未配置" in out          # 不可用的原因要显示出来


def test_provider_test未知源退出码1(isolated_data, tmp_path, capsys):
    f = tmp_path / "s.flac"
    f.write_bytes(b"")
    assert cli.main(["provider", "test", "不存在", "--file", str(f)]) == 1


def test_provider_test跑通并打印契约(isolated_data, tmp_path, monkeypatch, capsys):
    from fnlrc import library as lib
    from fnlrc.providers import base
    f = tmp_path / "s.flac"
    f.write_bytes(b"")

    def fake_fetch(self, song, cfg, *, deadline=None):
        return base.LyricResult(source="netease", matched="a - t", score=8.0,
                                lyric_lang="ja",
                                pairs=[(1000, "ああ", "译文", None)])

    from fnlrc.providers.builtin import netease as ne
    monkeypatch.setattr(ne.NeteaseProvider, "fetch", fake_fetch)

    rc = cli.main(["provider", "test", "netease", "--file", str(f)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "契约通过" in out
    assert "1 对" in out
