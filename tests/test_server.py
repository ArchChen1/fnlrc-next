"""HTTP 层与持久层。

重点全在**安全**上：key 不回显、写操作要令牌、跨站要拦、令牌只在回环访问时注入。
这些都是"错了会真出事"的地方，所以逐条钉死。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from fnlrc import store
from fnlrc import server as server_mod


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """把数据目录挪到临时目录，别污染开发机上的真实配置。"""
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    monkeypatch.delenv("FNLRC_REMOTE_API_KEY", raising=False)
    from fnlrc import server
    return server


@pytest.fixture
def client(isolated):
    return TestClient(isolated.app)


@pytest.fixture
def token(isolated):
    return store.get_token()


@pytest.fixture
def auth(token):
    return {"X-FNLRC-Token": token}


# ---------------------------------------------------------------- 配置不回显 key
def test_config不含api_key(client, tmp_path):
    store.save_config({"remote_api_key": "sk-SECRET-VALUE", "remote_model": "deepseek-chat"})
    body = client.get("/api/config").text
    assert "sk-SECRET-VALUE" not in body
    data = json.loads(body)
    assert data["remote_configured"] is True
    assert data["remote_key_masked"].startswith("sk-SEC")
    assert "sk-SECRET-VALUE" not in data["remote_key_masked"]


def test_保存配置需要令牌(client):
    r = client.post("/api/config", json={"remote_model": "x"})
    assert r.status_code == 401


def test_占位符表示不修改key(client, auth):
    store.save_config({"remote_api_key": "sk-ORIGINAL"})
    r = client.post("/api/config", json={"remote_api_key": "••••", "remote_model": "m2"}, headers=auth)
    assert r.status_code == 200
    assert store.load_config()["remote_api_key"] == "sk-ORIGINAL"   # 没被占位符覆盖


def test_能写入新key(client, auth):
    client.post("/api/config", json={"remote_api_key": "sk-NEW"}, headers=auth)
    assert store.load_config()["remote_api_key"] == "sk-NEW"


def test_环境变量优先且网页不可改(isolated, client, auth, monkeypatch):
    monkeypatch.setenv("FNLRC_REMOTE_API_KEY", "sk-FROM-ENV")
    store.save_config({"remote_api_key": "sk-FROM-FILE"})
    assert store.effective_api_key() == "sk-FROM-ENV"
    r = client.post("/api/config", json={"remote_api_key": "sk-OTHER"}, headers=auth)
    assert r.status_code == 409
    data = client.get("/api/config").json()
    assert data["remote_locked_by_env"] is True


def test_base_url里的查询串会被剥掉(client, auth):
    client.post("/api/config", json={
        "remote_base_url": "https://gw.example.com/v1?api_key=leaked"}, headers=auth)
    assert store.load_config()["remote_base_url"] == "https://gw.example.com/v1"


# ---------------------------------------------------------------- 令牌与同源
def test_无令牌拒绝写操作(client):
    assert client.post("/api/run", json={"root": "x"}).status_code == 401


def test_错令牌拒绝(client):
    assert client.post("/api/run", json={"root": "x"},
                       headers={"X-FNLRC-Token": "wrong"}).status_code == 401


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "http://evil.example"}, 403),
    ({"Origin": "http://127.0.0.1:9999", "Host": "127.0.0.1:8777"}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
])
def test_跨站请求被拒(client, token, headers, status):
    h = dict(headers, **{"X-FNLRC-Token": token})
    assert client.post("/api/run", json={"root": "x"}, headers=h).status_code == status


@pytest.mark.parametrize("origin,host", [
    ("http://127.0.0.1:8777", "127.0.0.1:8777"),
    ("http://localhost:8777", "127.0.0.1:8777"),   # 回环别名必须互认
    ("http://127.0.0.1:8777", "localhost:8777"),
])
def test_同源请求放行(client, token, origin, host):
    """localhost 与 127.0.0.1 是同一个回环接口的两种写法，字符串却不相等 ——
    按字符串比会把浏览器发的合法写操作全部拦掉（实测踩过）。"""
    r = client.post("/api/run", json={"root": "x"},
                    headers={"Origin": origin, "Host": host, "X-FNLRC-Token": token})
    assert r.status_code != 403


def test_无Origin视为非浏览器请求(client, token):
    """curl / cron 不带 Origin，不应被跨站规则误伤。"""
    r = client.post("/api/run", json={"root": "x"}, headers={"X-FNLRC-Token": token})
    assert r.status_code != 403


def test_令牌只在回环访问时注入HTML(isolated, token):
    # TestClient 默认的对端是 testclient:50000（非回环），要显式指定来源地址
    c = TestClient(isolated.app, client=("127.0.0.1", 12345))
    html = c.get("/", headers={"Host": "127.0.0.1:8777"}).text
    assert token in html
    assert "%%FNLRC_TOKEN%%" not in html


def test_非回环访问不注入令牌(isolated):
    """局域网/容器场景不能把令牌塞进 HTML —— 那样任何能访问端口的人都拿到写权限。"""
    c = TestClient(isolated.app, client=("192.168.1.50", 12345))
    html = c.get("/", headers={"Host": "192.168.1.50:8080"}).text
    assert 'window.__FNLRC_TOKEN__=""' in html


# ---------------------------------------------------------------- 只读接口
def test_version(isolated, client):
    d = client.get("/api/version").json()
    assert "version" in d
    assert d["translate_backends"] == ["source", "remote", "none"]


def test_ollama相关字段已从配置里移除(client):
    """本地模型后端已下线，配置里不该再出现它的字段。"""
    d = client.get("/api/config").json()
    for gone in ("local_model", "repair_model", "ollama_base_url", "ollama_keep_alive"):
        assert gone not in d


def test_ollama探测路由返回410(client):
    """旧前端调它时要拿到明确的"已移除"，而不是让人以为服务坏了。"""
    r = client.get("/api/probe/ollama")
    assert r.status_code == 410
    assert "已移除" in r.json()["detail"]


def test_scan拒绝不存在的路径(client):
    r = client.post("/api/scan", json={"root": "Z:\\definitely\\not\\here"})
    assert r.status_code == 400


def test_scan拒绝空路径(client):
    assert client.post("/api/scan", json={"root": ""}).status_code == 400


def test_job不存在时404(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_静态前端文件可访问(client):
    assert client.get("/styles.css").status_code == 200
    assert client.get("/js/main.js").status_code == 200


# ---------------------------------------------------------------- 持久层
def test_token生成并被复用(tmp_path, monkeypatch):
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    t1 = store.get_token()
    assert t1 and len(t1) >= 20
    assert store.get_token() == t1
    assert store.get_token(create=False) == t1
    rotated = store.new_token()
    assert rotated != t1
    assert store.get_token() == rotated


def test_token_create_false时不创建(tmp_path, monkeypatch):
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path / "fresh"))
    assert store.get_token(create=False) == ""


def test_config白名单(tmp_path, monkeypatch):
    """不在白名单里的键不落盘 —— 漏掉一个敏感字段就是泄露。"""
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    store.save_config({"remote_api_key": "sk-x", "evil_field": "boom", "password": "p"})
    raw = json.loads(store.config_path().read_text(encoding="utf-8"))
    assert raw["remote_api_key"] == "sk-x"
    assert "evil_field" not in raw and "password" not in raw


# ---------------------------------------------------------------- 首次使用引导（M1 前置）
def test_全新环境需要引导(tmp_path, monkeypatch):
    """没有 config.json、没扫过库 → 需要走引导。"""
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path / "fresh"))
    assert store.setup_needed() is True


def test_配置损坏需要引导(tmp_path, monkeypatch):
    """config.json 存在但读不出来（损坏）→ 重走引导，顺便自动修复。"""
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    store.config_path().write_text("{ not json", encoding="utf-8")
    assert store.config_error() is True
    assert store.setup_needed() is True


def test_已有last_root不需要引导(tmp_path, monkeypatch):
    """扫过库（有 last_root）→ 属于老用户，不打扰。"""
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    store.save_config({"last_root": "/music"})
    assert store.setup_needed() is False


def test_标记完成后不再引导(tmp_path, monkeypatch):
    """点过「完成」/「跳过」→ 记 setup_done，之后即使没配置也不再引导。"""
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path / "fresh"))
    store.save_config({"setup_done": True, "setup_version": 1})
    assert store.setup_needed() is False


def test_config接口暴露setup_needed(client):
    d = client.get("/api/config").json()
    assert d["setup_needed"] is True          # 全新环境


def test_auth校验端点要令牌且无副作用(client, auth):
    """令牌对 → 200；不对 / 缺失 → 401（与写操作同一套守卫）。"""
    assert client.get("/api/auth").status_code == 401
    assert client.get("/api/auth", headers={"X-FNLRC-Token": "wrong"}).status_code == 401
    r = client.get("/api/auth", headers=auth)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_auth不写入任何配置(client, auth):
    """校验端点必须只读 —— 它会在引导里被反复调用，不能有副作用。"""
    store.save_config({"last_root": "/music"})
    before = store.load_config()
    client.get("/api/auth", headers=auth)
    assert store.load_config() == before


def test_config损坏时返回空而不是崩(tmp_path, monkeypatch):
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    store.config_path().write_text("{ not json", encoding="utf-8")
    assert store.load_config() == {}


def test_报告可存可取(tmp_path, monkeypatch):
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    store.save_report("abc123", {"job_id": "abc123", "root": "/m",
                                 "started_at": 1700000000.0,
                                 "stats": {"written": 3}, "results": [{"file": "a"}]})
    rep = store.load_report("abc123")
    assert rep["stats"]["written"] == 3
    listed = store.list_reports()
    assert listed and listed[0]["job_id"] == "abc123"


def test_报告不存在返回None(tmp_path, monkeypatch):
    monkeypatch.setenv("FNLRC_DATA", str(tmp_path))
    assert store.load_report("nope") is None


def test_mask_key只露头尾(tmp_path, monkeypatch):
    from fnlrc import providers as P
    assert P.mask_key("sk-1234567890abcdef") == "sk-123***cdef"
    assert P.mask_key("short") == "*****"
    assert P.mask_key("") == ""
    assert P.mask_key(None) == ""


# ---------------------------------------------------------------- 回填到飞牛
@pytest.fixture
def fnlib(tmp_path):
    """造一个形状与真机一致的库：主库和歌词分片在**两个**目录里。"""
    from test_fnos import build
    ad, am, music = tmp_path / "appdata", tmp_path / "appmeta", tmp_path / "音乐"
    build(ad, am, music, songs=3)
    return ad, am, music


def test_没填数据目录时提示(client):
    d = client.post("/api/fnos/preview", json={}).json()
    assert d["ok"] is False
    assert "两处" in d["error"] or "数据目录" in d["error"]


def test_探测阶段(client, fnlib):
    ad, am, _ = fnlib
    d = client.post("/api/fnos/preview",
                    json={"app_data": str(ad), "appmeta": str(am)}).json()
    assert d["ok"] is True and d["stage"] == "probe"
    assert "结构正常" in d["text"]


def test_只给一处时给出可读的提示(client, fnlib, tmp_path):
    """真机上踩到的坑：只挂了 @appmeta。报错要说清缺哪一处。

    app_data 要传一个**明确的空目录**而不是省略 —— 省略会触发自动探测，
    在真装了飞牛的机器上它会找到真的 music.db，这条测试就红了。
    """
    _, am, _ = fnlib
    empty = tmp_path / "空的"
    empty.mkdir(exist_ok=True)
    d = client.post("/api/fnos/preview",
                    json={"app_data": str(empty), "appmeta": str(am)}).json()
    assert d["ok"] is False
    assert "appdata" in d["text"]


def test_结构不符时如实报告(client, tmp_path):
    ad, am, music = tmp_path / "a", tmp_path / "m", tmp_path / "音乐"
    from test_fnos import build
    import sqlite3
    build(ad, am, music)
    conn = sqlite3.connect(ad / "db" / "music.db")
    conn.execute("DROP TABLE track")
    conn.commit()
    conn.close()
    d = client.post("/api/fnos/preview",
                    json={"app_data": str(ad), "appmeta": str(am)}).json()
    assert d["ok"] is False
    assert d["verdict"] == "unexpected_schema"


def test_差异预览阶段(client, fnlib):
    ad, am, music = fnlib
    d = client.post("/api/fnos/preview",
                    json={"app_data": str(ad), "appmeta": str(am), "music_root": str(music)}).json()
    assert d["ok"] is True and d["stage"] == "plan"
    assert d["changed"] == 3
    assert len(d["samples"]) == 3
    assert "lyric.id" in d["text"]
    assert d["shard_table"] == "lyric_content"
    assert d["samples"][0]["stored_guid"] and d["samples"][0]["track_id"] == 0
    assert d["write_enabled"] is False      # 默认关着


def test_预览只读(client, fnlib):
    ad, am, music = fnlib
    def snap():
        out = {}
        for r in (ad, am):
            out.update({p: p.read_bytes() for p in sorted(r.rglob("*.db"))})
        return out
    before = snap()
    client.post("/api/fnos/preview",
                json={"app_data": str(ad), "appmeta": str(am), "music_root": str(music)})
    assert before == snap()


def test_执行要令牌(client, fnlib):
    ad, am, music = fnlib
    r = client.post("/api/fnos/apply",
                    json={"app_data": str(ad), "appmeta": str(am),
                          "music_root": str(music), "expect_changed": 3})
    assert r.status_code == 401


def test_服务端没开启时拒绝(client, auth, fnlib, monkeypatch):
    """默认必须写不进去 —— 这是"写另一个应用的数据库"的默认开关。"""
    monkeypatch.delenv("FNLRC_ENABLE_FNOS_WRITE", raising=False)
    ad, am, music = fnlib
    r = client.post("/api/fnos/apply", headers=auth,
                    json={"app_data": str(ad), "appmeta": str(am),
                          "music_root": str(music), "expect_changed": 3})
    assert r.status_code == 403
    assert "FNLRC_ENABLE_FNOS_WRITE" in r.json()["detail"]


def test_差异数对不上时拒绝(client, auth, fnlib, monkeypatch):
    """你看到的差异数变了，说明状态变了 —— 让前端重新预览，而不是照着旧结果写。"""
    monkeypatch.setenv("FNLRC_ENABLE_FNOS_WRITE", "1")
    ad, am, music = fnlib
    r = client.post("/api/fnos/apply", headers=auth,
                    json={"app_data": str(ad), "appmeta": str(am),
                          "music_root": str(music), "expect_changed": 99})
    assert r.status_code == 409
    assert "重新预览" in r.json()["detail"]


def test_开启后能真的回填(client, auth, fnlib, monkeypatch):
    monkeypatch.setenv("FNLRC_ENABLE_FNOS_WRITE", "1")
    ad, am, music = fnlib
    r = client.post("/api/fnos/apply", headers=auth,
                    json={"app_data": str(ad), "appmeta": str(am),
                          "music_root": str(music), "expect_changed": 3})
    assert r.status_code == 200
    d = r.json()
    assert d["changed"] == 3
    # 正文在**分片**的 `lyric_content` 表里，按 stored_guid 取；
    # 主库那张 `lyric` 只是索引，里面没有正文。
    import sqlite3
    from fnlrc import fnos
    conn = sqlite3.connect(ad / "db" / "music.db")
    shard_guid = conn.execute("SELECT stored_guid FROM lyric WHERE id = 100").fetchone()[0]
    conn.close()
    shard = am / "lyric-sqlite" / fnos.shard_name_for(shard_guid)
    conn = sqlite3.connect(shard)
    content = conn.execute("SELECT content FROM lyric_content WHERE guid = ?",
                           (shard_guid,)).fetchone()[0]
    conn.close()
    assert "[00:01.00]" in fnos._text(content)


def test_保留本地时间轴可配置并能持久化(client, auth):
    """设置里能改、能读回；默认开。"""
    assert client.get("/api/config").json()["keep_local"] is True
    client.post("/api/config", json={"keep_local": False}, headers=auth)
    assert client.get("/api/config").json()["keep_local"] is False
    client.post("/api/config", json={"keep_local": True}, headers=auth)
    assert client.get("/api/config").json()["keep_local"] is True


def test_请求体里的保留开关优先于服务端配置(isolated):
    """网页版勾选框要能**这一次**就覆盖服务端存的配置，不用先保存设置。"""
    from fnlrc import server as sv

    assert sv._config_from({"keep_local": False}).keep_local is False
    assert sv._config_from({"keep_local": True}).keep_local is True
    # 不传就跟随服务端配置
    assert sv._config_from({}).keep_local is True


@pytest.fixture
def fnlib_with_gaps(tmp_path):
    """3 首里只有 1 首有歌词索引行 —— 真机上那 142 首那种情形。"""
    from test_fnos import build
    ad, am, music = tmp_path / "g_appdata", tmp_path / "g_appmeta", tmp_path / "g_音乐"
    build(ad, am, music, songs=3, lyrics_for=[0])
    return ad, am, music


def test_回填接口支持新建歌词行(client, auth, fnlib_with_gaps, monkeypatch):
    """没有歌词行的歌可以建行 —— 真机实测通过的那条路。"""
    monkeypatch.setenv("FNLRC_ENABLE_FNOS_WRITE", "1")
    ad, am, music = fnlib_with_gaps
    d = client.post("/api/fnos/preview",
                    json={"app_data": str(ad), "appmeta": str(am),
                          "music_root": str(music)}).json()
    assert d["shard_table"] == "lyric_content"
    r = client.post("/api/fnos/apply", headers=auth,
                    json={"app_data": str(ad), "appmeta": str(am), "music_root": str(music),
                          "expect_changed": d["changed"] + d["insertable"],
                          "allow_insert": True})
    assert r.status_code == 200
    assert r.json()["inserted"] == d["insertable"] == 2
    assert all(x["ok"] for x in r.json()["insert_report"])


def test_不开启新建时差异数只算原地更新(client, auth, fnlib_with_gaps, monkeypatch):
    """差异数对账要跟着 `allow_insert` 走，否则会误判成"状态变了"。"""
    monkeypatch.setenv("FNLRC_ENABLE_FNOS_WRITE", "1")
    ad, am, music = fnlib_with_gaps
    d = client.post("/api/fnos/preview",
                    json={"app_data": str(ad), "appmeta": str(am),
                          "music_root": str(music)}).json()
    assert d["changed"] == 1 and d["insertable"] == 2
    r = client.post("/api/fnos/apply", headers=auth,        # 不带 allow_insert
                    json={"app_data": str(ad), "appmeta": str(am),
                          "music_root": str(music), "expect_changed": d["changed"]})
    assert r.status_code == 200
    assert r.json()["inserted"] == 0
    # 带上 allow_insert 就必须按"原地 + 新建"的总数对账
    r2 = client.post("/api/fnos/apply", headers=auth,
                     json={"app_data": str(ad), "appmeta": str(am), "music_root": str(music),
                           "expect_changed": d["changed"], "allow_insert": True})
    assert r2.status_code == 409, "总数对不上时必须拒绝，不能照着过期结果写"


# ---------------------------------------------------------------- 监听文件夹接口
def test_监听默认没在跑(client):
    d = client.get("/api/watch/status").json()
    assert d["running"] is False
    assert d["handled"] == 0
    cfg = client.get("/api/config").json()
    assert cfg["watch_running"] is False
    assert cfg["watch"]["watch_after"] == "keep"


def test_启动停止需要令牌(client):
    assert client.post("/api/watch/start").status_code == 401
    assert client.post("/api/watch/stop").status_code == 401


def test_没填目录时拒绝启动(client, auth):
    r = client.post("/api/watch/start", headers=auth)
    assert r.status_code == 400
    assert "目录" in r.json()["detail"]


def test_目录不存在时拒绝启动(client, auth, tmp_path):
    store.save_config({"watch_root": str(tmp_path / "nope")})
    r = client.post("/api/watch/start", headers=auth)
    assert r.status_code == 400


def test_搬运到不存在目录时拒绝(client, auth, tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    store.save_config({"watch_root": str(root), "watch_after": "move",
                       "watch_move_to": str(tmp_path / "nope")})
    r = client.post("/api/watch/start", headers=auth)
    assert r.status_code == 400


def test_启动后状态变为running(client, auth, tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    store.save_config({"watch_root": str(root), "watch_poll_s": 0.3, "watch_quiet_s": 0})
    r = client.post("/api/watch/start", headers=auth)
    assert r.status_code == 200
    assert r.json()["status"]["running"] is True
    assert client.get("/api/watch/status").json()["running"] is True
    # 收工
    assert client.post("/api/watch/stop", headers=auth).status_code == 200
    assert client.get("/api/watch/status").json()["running"] is False


def test_已在监听时改配置要热更新到运行中的watcher(client, auth, tmp_path):
    """回归：取消勾选「预览模式」后再点「保存并开始监测」，新配置必须真的生效。

    老实现里 `/api/watch/start` 在 already=True 时直接 return，
    存进 config.json 的 watch_write 永远到不了正在跑的那个 watcher ——
    界面显示"已写入"，实际还只写预览目录。
    """
    root = tmp_path / "inbox"
    root.mkdir()
    store.save_config({"watch_root": str(root), "watch_write": False,
                       "watch_poll_s": 0.3, "watch_quiet_s": 0})

    assert client.post("/api/watch/start", headers=auth).status_code == 200
    w = server_mod._watch["watcher"]
    assert w.cfg.write is False, "先按预览模式跑"

    # 用户取消勾选预览模式 → 前端先存配置，再点「保存并开始监测」
    client.post("/api/config", headers=auth, json={"watch_write": True})
    r = client.post("/api/watch/start", headers=auth)
    assert r.json().get("already") is True
    assert server_mod._watch["watcher"] is w, "应就地换配置，而不是重建线程"
    assert w.cfg.write is True, "热更新后必须真的开始写库"

    # 反向：切回预览也要立刻生效
    client.post("/api/config", headers=auth, json={"watch_write": False})
    client.post("/api/watch/start", headers=auth)
    assert w.cfg.write is False

    client.post("/api/watch/stop", headers=auth)


def test_重启后能拿到上次的音乐库路径(client, auth, tmp_path):
    """回归：桌面版每次启动绑随机端口，localStorage 按 origin 隔离 → 重启即丢。

    服务端 config.json 里的 last_root 才是跨启动持久的那一份，
    /api/config 必须把它交给前端。
    """
    lib = tmp_path / "music"
    lib.mkdir()
    (lib / "song.flac").write_bytes(b"")
    r = client.post("/api/scan", json={"root": str(lib), "persist": True})
    assert r.status_code == 200

    d = client.get("/api/config").json()
    assert d["last_root"] == str(lib), "重启后前端要靠它把路径填回来"

    # 「立即执行」不落盘：这次选的库不该顶掉长期配置
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.flac").write_bytes(b"")
    client.post("/api/scan", json={"root": str(other), "persist": False})
    assert client.get("/api/config").json()["last_root"] == str(lib)


# ---------------------------------------------------------------- 人工翻译接口（M3）
@pytest.fixture
def mlib(tmp_path):
    """一个只含 1 首日文单语歌的迷你库，歌词 4 行。"""
    root = tmp_path / "music"
    (root / "日语").mkdir(parents=True)
    audio = root / "日语" / "song.flac"
    audio.write_bytes(b"")
    lrc = root / "日语" / "song.lrc"
    lrc.write_text("".join(f"[00:1{i}.00]ああ サンプル{i}\n" for i in range(4)),
                   encoding="utf-8")
    return root, audio, lrc


def test_读取歌词行_无需令牌(client, mlib):
    """只读接口：返回用户自己库里的歌词文本，不该要求令牌。"""
    root, _a, _l = mlib
    r = client.post("/api/manual/lines", json={"root": str(root), "files": ["日语/song.flac"]})
    assert r.status_code == 200
    song = r.json()["songs"][0]
    assert song["title"] and song["expected_lang"] == "ja"
    assert len(song["lines"]) == 4
    assert song["lines"][0]["original"] == "ああ サンプル0"
    assert song["lines"][0]["translation"] == ""
    assert song["lines"][0]["time"] == "00:10.00"


def test_读取不存在的文件如实报错(client, mlib):
    root, _a, _l = mlib
    d = client.post("/api/manual/lines",
                    json={"root": str(root), "files": ["日语/nope.flac"]}).json()
    assert d["songs"][0]["error"]


def test_保存译文需要令牌(client, mlib):
    root, _a, _l = mlib
    r = client.post("/api/manual/save", json={
        "root": str(root), "file": "日语/song.flac",
        "lines": [{"index": 1, "original": "ああ サンプル0", "translation": "译文一"}]})
    assert r.status_code == 401


def test_保存译文写回文件并备份(client, auth, mlib):
    root, _a, lrc = mlib
    r = client.post("/api/manual/save", headers=auth, json={
        "root": str(root), "file": "日语/song.flac",
        "lines": [{"index": 1, "original": "ああ サンプル0", "translation": "译文一"},
                  {"index": 2, "original": "ああ サンプル1", "translation": "译文二"}]})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["lines"] == 2
    body = lrc.read_text(encoding="utf-8")
    assert "译文一" in body and "译文二" in body
    assert (root / "日语" / "song.lrc.bak").exists(), "覆盖前必须备份"
    # 时间轴没动
    assert body.count("[00:10.00]") == 2 and body.count("[00:12.00]") == 1


def test_保存时改过的原文被拦下(client, auth, mlib):
    root, _a, lrc = mlib
    before = lrc.read_text(encoding="utf-8")
    d = client.post("/api/manual/save", headers=auth, json={
        "root": str(root), "file": "日语/song.flac",
        "lines": [{"index": 1, "original": "被人改过", "translation": "x"}]}).json()
    assert d["ok"] is False and d["problems"]
    assert lrc.read_text(encoding="utf-8") == before, "校验不过时绝不能动原文件"


def test_保存时可选择关闭备份(client, auth, mlib):
    root, _a, _l = mlib
    client.post("/api/manual/save", headers=auth, json={
        "root": str(root), "file": "日语/song.flac",
        "lines": [{"index": 1, "original": "ああ サンプル0", "translation": "译文"}],
        "backup": False})
    assert not (root / "日语" / "song.lrc.bak").exists()
