"""HTTP 层：FastAPI + SSE + 内置前端。

安全模型（三条，缺一不可）：

1. **API key 不出服务端**。浏览器只能问"配没配"和"填新的"，永远拿不回原值。
   `GET /api/config` 走白名单序列化，key 只以 `configured: true/false` 的形式出现。
2. **写操作要令牌**。`/api/run`、`/api/rollback`、`/api/config`(POST) 都校验
   `X-FNLRC-Token`；没有它就只能读。
3. **绑回环 ≠ 授权边界**。任何本机进程、或能触达 `http://127.0.0.1:<port>` 的网页
   （DNS rebinding / localhost CSRF）都能发请求，所以**即使只绑 127.0.0.1 也要做
   `Origin` / `Sec-Fetch-Site` 校验**。

令牌怎么给到前端：来自 **回环地址**的请求，`index.html` 里会被注入令牌（桌面版场景，
本来就是你自己这台机器）；来自其它地址（局域网 / 容器）则**不注入**，前端弹令牌输入框，
用户从 `docker logs` 里抄一次，存 localStorage。
"""
from __future__ import annotations

import csv
import io
import json
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import library as lib
from . import pipeline as pl
from . import providers as P
from . import store

app = FastAPI(title="fnlrc", version=__version__, docs_url=None, redoc_url=None)

WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "web"

# 任务表。v0.1 单任务串行，但结构上按 id 存，便于以后放开。
JOBS: dict[str, dict] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------- 安全
def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


def _same_origin(request: Request) -> bool:
    """判断请求是不是同源的。

    浏览器发的同源请求，`Origin` 与 `Host` 头必然一致 —— 这正是浏览器自己的
    同源策略，照抄它就行。

    ⚠️ 必须按 `Host` 头比，不能拿 Origin 的主机名去比 socket 对端地址：
    `localhost` 和 `127.0.0.1` 是同一个回环接口的两种写法，字符串却不相等。
    实测踩过 —— 浏览器从 localhost 打开时，写操作会被自己的 CSRF 校验全部拦掉。
    """
    origin = request.headers.get("origin")
    if not origin:
        return True          # 非浏览器发起的请求（curl / cron）没有 Origin
    o = urlsplit(origin)
    host_hdr = request.headers.get("host") or ""
    if o.netloc == host_hdr:
        return True
    o_host, _, o_port = o.netloc.partition(":")
    h_host, _, h_port = host_hdr.partition(":")
    return (_is_loopback(o_host) and _is_loopback(h_host)
            and (o_port or "80") == (h_port or "80"))


def require_token(request: Request, x_fnlrc_token: str | None = Header(default=None)):
    """写操作的守卫：令牌 + 同源校验。

    同源校验挡的是「浏览器里的第三方页面偷偷对你的 localhost 发请求」——
    那种请求会带上真实的 Origin，而且 Sec-Fetch-Site 会是 cross-site。
    """
    sfs = request.headers.get("sec-fetch-site")
    if sfs and sfs not in ("same-origin", "none"):
        raise HTTPException(403, "跨站请求被拒绝（Sec-Fetch-Site）")
    if not _same_origin(request):
        raise HTTPException(403, "跨站请求被拒绝（Origin 与 Host 不一致）")
    expect = store.get_token(create=False)
    if not expect or x_fnlrc_token != expect:
        raise HTTPException(401, "缺少或错误的访问令牌")


# ---------------------------------------------------------------- 前端
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    f = WEB_DIR / "index.html"
    if not f.is_file():
        return HTMLResponse("<h1>fnlrc</h1><p>前端文件缺失：fnlrc/web/index.html</p>",
                            status_code=500)
    html = f.read_text(encoding="utf-8")
    # 只在回环来源注入令牌；局域网/容器场景让用户自己填，避免任何能访问端口的人
    # 都能直接拿到写权限。
    # 占位符与 JS 变量名用不同字面量：用同一个字符串会被全局替换连变量名一起换掉。
    token = store.get_token(create=False) if _is_loopback(_client_host(request)) else ""
    if "%%FNLRC_TOKEN%%" not in html:
        # 前端改了写法却没同步这里 → 令牌永远注入不进去，必须立刻发现
        raise HTTPException(500, "index.html 缺少令牌占位符 %%FNLRC_TOKEN%%")
    return HTMLResponse(html.replace("%%FNLRC_TOKEN%%", token))


@app.get("/api/version")
def version():
    return {
        "version": __version__,
        "translate_backends": ["source", "remote", "none"],
    }


# ---------------------------------------------------------------- 配置
@app.get("/api/config")
def get_config():
    """**绝不回显 API key**。只告诉前端"配没配"以及要用哪个 base/model。"""
    cfg = store.load_config()
    key = store.effective_api_key(cfg)
    return {
        "translate": cfg.get("translate", "source"),
        "remote_base_url": cfg.get("remote_base_url", ""),
        "remote_model": cfg.get("remote_model", ""),
        "remote_timeout": cfg.get("remote_timeout", 60),
        "backup": cfg.get("backup", True),
        "keep_local": bool(cfg.get("keep_local", True)),
        "deadline_s": cfg.get("deadline_s", 25),
        "translate_deadline_s": cfg.get("translate_deadline_s", 180),
        "last_root": cfg.get("last_root", ""),
        # 只有这三个字段和 key 有关，且都不含 key 本身
        "remote_configured": bool(key),
        "remote_key_masked": P.mask_key(key),
        "remote_locked_by_env": store.api_key_locked(),
    }


@app.post("/api/config")
def set_config(request: Request, payload: dict = Body(...),
               x_fnlrc_token: str | None = Header(default=None)):
    require_token(request, x_fnlrc_token)
    patch = {k: v for k, v in payload.items() if k in store.CONFIG_KEYS}
    if patch.get("remote_api_key") == store.UNCHANGED:
        patch.pop("remote_api_key")
    if store.api_key_locked() and "remote_api_key" in patch:
        raise HTTPException(409, "API key 由环境变量 FNLRC_REMOTE_API_KEY 提供，不能在网页里改")
    # base_url 里的查询串可能藏着 ?api_key=，落盘前先剥掉
    if patch.get("remote_base_url"):
        patch["remote_base_url"] = P.redact_url(patch["remote_base_url"])
    store.save_config(patch)
    return get_config()


@app.post("/api/test-remote")
def test_remote(request: Request, payload: dict = Body(...),
                x_fnlrc_token: str | None = Header(default=None)):
    require_token(request, x_fnlrc_token)
    cfg = store.load_config()
    base = payload.get("remote_base_url") or cfg.get("remote_base_url", "")
    model = payload.get("remote_model") or cfg.get("remote_model", "")
    key = payload.get("remote_api_key")
    if not key or key == store.UNCHANGED:
        key = store.effective_api_key(cfg)
    ok, msg = P.remote_test(base, key, model)
    return {"ok": ok, "message": msg}


@app.get("/api/probe/ollama")
def probe_ollama_deprecated():
    """Ollama 本地翻译后端已移除。保留这条路由只为让旧前端拿到明确的 410 而不是 404。"""
    raise HTTPException(410, "本地模型（Ollama）后端已移除，请改用「源站译词」或「我的 API」")


# ---------------------------------------------------------------- 扫描 / 预览
def _scan(root: str, limit: int | None, only: str | None = None):
    if not root:
        raise HTTPException(400, "请先指定音乐库路径")
    if not os.path.isdir(root):
        raise HTTPException(400, f"打不开这个路径：{root}")
    # 先筛后截（library.select 把顺序定死了，别在这儿重写一遍）
    return lib.select(root, only=only, limit=limit)


@app.post("/api/scan")
def scan(payload: dict = Body(...)):
    root = payload.get("root", "")
    songs = _scan(root, payload.get("limit"))
    store.save_config({"last_root": root})
    return {"root": root, "summary": lib.summarize(songs)}


@app.post("/api/preview")
def preview(payload: dict = Body(...)):
    root = payload.get("root", "")
    cfg = _config_from(payload)
    songs = _scan(root, payload.get("limit"), payload.get("only"))
    rows = []
    dist: dict[str, int] = {}
    for s in songs:
        d = pl.decide(s, cfg)
        dist[d["action"]] = dist.get(d["action"], 0) + 1
        rows.append({"file": s.rel, "title": s.title, "artist": s.artist,
                     "action": d["action"], "reason": d["reason"],
                     "expected_lang": s.expected_lang,
                     "expected_lang_source": s.expected_lang_source,
                     "embedded_lyrics": bool(s.embedded_lyrics)})
    return {"root": root, "total": len(rows), "distribution": dist, "rows": rows}


def _config_from(payload: dict) -> pl.Config:
    """请求体 → Config。key 从**服务端**取，不接受浏览器传来的 key。"""
    cfg = store.load_config()
    return pl.Config(
        write=bool(payload.get("write")),
        dump_dir=payload.get("dump_dir"),
        force=bool(payload.get("force")),
        translate=payload.get("translate") or cfg.get("translate", "source"),
        remote_base_url=cfg.get("remote_base_url", ""),
        remote_api_key=store.effective_api_key(cfg),
        remote_model=cfg.get("remote_model", ""),
        remote_timeout=int(cfg.get("remote_timeout", 60) or 60),
        backup=bool(cfg.get("backup", True)),
        # 请求体里显式给了就用它，否则跟随服务端配置（默认开）
        keep_local=(bool(payload["keep_local"]) if "keep_local" in payload
                    else bool(cfg.get("keep_local", True))),
        limit=payload.get("limit"),
    )


# ---------------------------------------------------------------- 任务
@app.post("/api/run")
def run(request: Request, payload: dict = Body(...),
        x_fnlrc_token: str | None = Header(default=None)):
    require_token(request, x_fnlrc_token)
    root = payload.get("root", "")
    songs = _scan(root, payload.get("limit"), payload.get("only"))
    if not songs:
        raise HTTPException(400, "这个文件夹里没找到音频文件")
    cfg = _config_from(payload)
    job_id = uuid.uuid4().hex[:12]

    job = {"id": job_id, "root": root, "cfg": cfg, "total": len(songs),
           "started_at": time.time(), "done": False, "stopped": False,
           "stats": {}, "results": [], "events": queue.Queue(), "snapshot": {}}
    with _jobs_lock:
        JOBS[job_id] = job

    def worker():
        def on_event(ev):
            if ev.get("type") == "song":
                job["results"].append(ev["result"])
                job["snapshot"] = {"done": ev["index"], "total": ev["total"],
                                   "last": ev["result"]}
            elif ev.get("type") == "done":
                job["stats"] = ev["stats"]
            job["events"].put(ev)

        try:
            out = pl.run_batch(songs, cfg, on_event=on_event,
                               should_stop=lambda: job["stopped"])
            job["stats"] = out.get("stats") or job["stats"]
            store.save_report(job_id, {"job_id": job_id, "root": root,
                                       "started_at": job["started_at"],
                                       "elapsed_s": out.get("elapsed_s"),
                                       "stats": job["stats"], "results": out.get("results")})
        except Exception as e:
            job["events"].put({"type": "error", "message": P.mask_secret(f"{type(e).__name__}: {e}")})
        finally:
            job["done"] = True
            job["events"].put({"type": "__end__"})

    threading.Thread(target=worker, daemon=True, name=f"fnlrc-{job_id}").start()
    return {"job_id": job_id, "total": len(songs)}


@app.get("/api/jobs/{job_id}")
def job_snapshot(job_id: str):
    job = JOBS.get(job_id)
    if job:
        return {"job_id": job_id, "done": job["done"], "total": job["total"],
                "snapshot": job["snapshot"], "stats": job["stats"],
                "results": job["results"]}
    rep = store.load_report(job_id)
    if rep:
        return {"job_id": job_id, "done": True, "total": len(rep.get("results") or []),
                "snapshot": {}, "stats": rep.get("stats") or {},
                "results": rep.get("results") or []}
    raise HTTPException(404, "没有这个任务")


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str, token: str | None = Query(default=None)):
    """SSE 进度流。EventSource 不能带自定义头，所以令牌走查询串。"""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "没有这个任务（进程可能已重启，去看历史报告）")

    def gen():
        while True:
            try:
                ev = job["events"].get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                if job["done"]:
                    break
                continue
            if ev.get("type") == "__end__":
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/{job_id}/stop")
def job_stop(job_id: str, request: Request,
             x_fnlrc_token: str | None = Header(default=None)):
    require_token(request, x_fnlrc_token)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "没有这个任务")
    job["stopped"] = True
    return {"stopping": True}


# ---------------------------------------------------------------- 报告 / 回滚
@app.get("/api/report/{job_id}")
def report(job_id: str, status: str | None = None, format: str = "json"):
    job = JOBS.get(job_id)
    results = job["results"] if job else (store.load_report(job_id) or {}).get("results") or []
    if status:
        want = {s for s in status.split(",") if s}
        results = [r for r in results if r.get("status") in want]
    if format == "csv":
        buf = io.StringIO()
        cols = ["file", "title", "artist", "status", "source", "backend", "pairs",
                "matched", "score", "expected_lang", "lyric_lang", "note", "elapsed_s"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)
        return PlainTextResponse("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="fnlrc-{job_id}.csv"'})
    return {"job_id": job_id, "results": results,
            "stats": (job or {}).get("stats") or (store.load_report(job_id) or {}).get("stats") or {}}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": store.list_reports()}


@app.post("/api/rollback")
def rollback(request: Request, payload: dict = Body(...),
             x_fnlrc_token: str | None = Header(default=None)):
    """从 `.bak` 恢复。给一批文件路径，逐个把 .bak 换回去。"""
    require_token(request, x_fnlrc_token)
    files = payload.get("files") or []
    undone, skipped = [], []
    for f in files:
        bak = f + ".bak"
        if os.path.isfile(bak):
            try:
                os.replace(bak, f)
                undone.append(f)
            except Exception as e:
                skipped.append({"file": f, "why": P.mask_secret(str(e))})
        else:
            skipped.append({"file": f, "why": "没有 .bak"})
    return {"rolled_back": undone, "skipped": skipped}


# ---------------------------------------------------------------- 目录浏览
@app.get("/api/fs")
def browse(path: str = ""):
    """网页版选目录用。桌面版走 pywebview 的原生对话框。"""
    target = path or os.path.expanduser("~")
    if not os.path.isdir(target):
        raise HTTPException(400, f"打不开这个路径：{target}")
    dirs = []
    try:
        for name in sorted(os.listdir(target)):
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            if os.path.isdir(full):
                dirs.append({"name": name, "path": full})
    except PermissionError:
        raise HTTPException(403, "没有权限读取这个目录")
    parent = os.path.dirname(target.rstrip(os.sep)) or target
    audio = sum(1 for _ in lib.iter_audio(target)) if path else 0
    return {"path": target, "parent": parent, "dirs": dirs[:500], "audio_count": audio}


@app.get("/api/write-test")
def write_test(path: str = Query(...)):
    """目标目录能不能写。SMB / 只读挂载 / 容器权限都会在这里现出原形。

    探针文件名刻意不以点开头：部分 SMB 配置（veto files / hide dot files）
    会拒绝创建点文件，用点文件会把"可写"误判成"不可写"。
    """
    if not os.path.isdir(path):
        return {"ok": False, "message": "目录不存在或打不开"}
    probe = os.path.join(path, "fnlrc_write_test.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("fnlrc write test\n")
        os.remove(probe)
        return {"ok": True, "message": "可写"}
    except Exception as e:
        return {"ok": False, "message": P.mask_secret(f"{type(e).__name__}: {e}")}


# ---------------------------------------------------------------- 回填到飞牛
def _fnos_write_enabled() -> bool:
    """网页版能不能写飞牛的数据库。

    **默认关着**，要显式设 `FNLRC_ENABLE_FNOS_WRITE=1` 才开。理由：这个功能会写
    另一个应用的私有数据库，属于高权限操作。默认开着的话，任何拿到令牌的人
    都能往任意路径写 SQLite 文件 —— 那是比"写音乐库"更大的权限面。
    """
    return (os.environ.get("FNLRC_ENABLE_FNOS_WRITE") or "").strip() in ("1", "true", "yes")


@app.post("/api/fnos/preview")
def fnos_preview(payload: dict = Body(...)):
    """只读：探测结构，或者算一遍差异。**什么都不改。**"""
    from . import fnos

    app_data = (payload.get("app_data") or "").strip()
    appmeta = (payload.get("appmeta") or "").strip()
    music_root = (payload.get("music_root") or "").strip()
    if not (app_data or appmeta):
        return {"ok": False, "error": "先填飞牛的数据目录。注意是**两处**："
                                      "主库在 @appdata（系统盘），歌词分片在 @appmeta（存储池）"}

    if not music_root:
        rep = fnos.probe(app_data, appmeta)
        return {"ok": rep.get("verdict") == "ok", "stage": "probe",
                "verdict": rep.get("verdict"), "text": fnos.format_report(rep),
                "app_data": rep.get("app_data"), "appmeta": rep.get("appmeta"),
                "error": rep.get("error")}

    pl = fnos.plan(app_data, appmeta, music_root)
    if "error" in pl:
        return {"ok": False, "stage": "plan", "error": pl["error"]}
    return {
        "ok": True, "stage": "plan",
        "changed": len(pl["todo"]), "same": pl["same"], "no_lrc": pl["no_lrc"],
        "no_row": len(pl["no_row"]), "multi": len(pl["multi"]),
        # 其中**可以新建歌词行**的（排除"索引落不到分片"那种故障）
        "insertable": len([t for t in pl["no_row"] if not t.get("note")]),
        "total": pl["total_tracks"],
        # 键名跟着真机模型走：track_id / stored_guid（不是 guid），
        # 而且注明落在哪张正文表上 —— 早先这里写的是 `lyric`，真机上那张是索引表。
        "shard_table": pl["shard_table"],
        "samples": [{k: t[k] for k in ("file", "track_id", "stored_guid", "shard",
                                       "lyric_id", "old_len", "new_len")}
                    for t in pl["todo"][:8]],
        "text": fnos.format_plan(pl),
        "write_enabled": _fnos_write_enabled(),
    }


@app.post("/api/fnos/apply")
def fnos_apply(request: Request, payload: dict = Body(...),
               x_fnlrc_token: str | None = Header(default=None)):
    """真的写飞牛的数据库。要令牌 + 要显式开启 + 要核对差异数。"""
    require_token(request, x_fnlrc_token)
    from . import fnos

    if not _fnos_write_enabled():
        raise HTTPException(403, "服务端没有开启这个功能。启动容器时加 "
                                 "-e FNLRC_ENABLE_FNOS_WRITE=1 才行 —— 它会写飞牛的数据库，"
                                 "所以默认关着")

    app_data = (payload.get("app_data") or "").strip()
    appmeta = (payload.get("appmeta") or "").strip()
    music_root = (payload.get("music_root") or "").strip()
    expect = payload.get("expect_changed")
    allow_insert = bool(payload.get("allow_insert"))
    if not music_root:
        raise HTTPException(400, "音乐库路径要填")

    # 核对：你看到的差异数必须还是这个数，不然说明状态变了，让前端重新预览
    fresh = fnos.plan(app_data, appmeta, music_root)
    if "error" in fresh:
        raise HTTPException(400, fresh["error"])
    want = len(fresh["todo"])
    if allow_insert:
        # 新建只对"一行歌词都没有"的歌生效；带 note 的是故障，不算在内
        want += len([t for t in fresh["no_row"] if not t.get("note")])
    if expect is None or int(expect) != want:
        raise HTTPException(409, f"差异已经变了（现在是 {want} 首，"
                                 f"你看到的是 {expect}）—— 请重新预览一次再执行")

    out = fnos.apply_plan(app_data, appmeta, music_root, allow_insert=allow_insert)
    if "error" in out:
        raise HTTPException(400, out["error"])
    return {"changed": out["changed"], "inserted": out.get("inserted", 0),
            "backup_dir": out["backup_dir"],
            "insert_report": [{k: x.get(k) for k in ("file", "shard", "ok", "why")}
                              for x in out.get("insert_report", [])[:20]],
            "failed": [{k: x.get(k) for k in ("file", "why")} for x in out["failed"][:20]],
            "message": out.get("message", "")}


# ---------------------------------------------------------------- 静态资源
# 放在最后：显式的 "/" 路由先匹配（它需要在回环访问时注入令牌），
# 其余路径（/styles.css、/js/*.js）交给静态目录。
# 前端拆成多文件而不是单个 index.html：几件事的复杂度不同（设计 token / 场景渲染 /
# 面板 / 目录浏览），塞进一个文件只会都变难读。用原生 ES module，仍然零构建步骤。
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), check_dir=False), name="web")
