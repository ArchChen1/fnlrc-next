"""HTTP 层与持久层。

重点全在**安全**上：key 不回显、写操作要令牌、跨站要拦、令牌只在回环访问时注入。
这些都是"错了会真出事"的地方，所以逐条钉死。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from fnlrc import store


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
