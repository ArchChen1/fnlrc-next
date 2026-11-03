"""持久层：配置、访问令牌、任务报告。

存储位置按运行形态决定：

    桌面 / CLI  →  %LOCALAPPDATA%\\fnlrc   （Windows）
                   ~/.local/share/fnlrc    （其它）
    容器        →  $FNLRC_DATA 或默认目录

**API key 只存在服务端**，浏览器永远拿不到它。取用的优先级是：

    环境变量 FNLRC_REMOTE_API_KEY  >  config.json 里的 remote_api_key

环境变量优先不是洁癖：容器部署时它是唯一合理的注入方式，而且一旦设了它，
设置页的 key 输入就变成只读 —— 避免"一份 key 两个真相"。
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

APP = "fnlrc"

# 允许写进 config.json 的键。白名单而不是黑名单：漏掉一个新字段只是不持久化，
# 漏掉一个敏感字段就是泄露。
CONFIG_KEYS = (
    "translate",
    "remote_base_url",
    "remote_model",
    "remote_api_key",
    "remote_timeout",
    "backup",
    "keep_local",
    "deadline_s",
    "translate_deadline_s",
    "last_root",
    # 首次使用引导（M1 前置）用：点过「完成」或「跳过」后置真，之后不再打扰
    "setup_done",
    "setup_version",
)
SECRET_KEYS = ("remote_api_key",)
# 引导口径的版本号。将来引导流程大改（新增步骤）时可抬高它，让老用户再走一次。
SETUP_VERSION = 1
# 设置页用这个占位符表示"不修改"，而不是把真 key 回传再收回来
UNCHANGED = "••••"


def data_dir() -> Path:
    env = os.environ.get("FNLRC_DATA")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / APP
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(xdg) / APP


def _ensure_dir() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_private(path: Path, text: str) -> None:
    """写文件并尽量收紧权限（POSIX 上 0600）。Windows 走 ACL，这里不做额外处理。"""
    _ensure_dir()
    path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


# ---------------------------------------------------------------- 配置
def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> dict:
    p = config_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: v for k, v in raw.items() if k in CONFIG_KEYS}


def config_error() -> bool:
    """config.json **存在但读不出来**（损坏）时为 True；文件不在不算。"""
    p = config_path()
    if not p.is_file():
        return False
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return True
    return False


def setup_needed() -> bool:
    """首次使用引导要不要走。

    判定「全新环境」的口径（缺一不可地按顺序判）：
    1. 显式标记过完成（``setup_done``）→ 不需要，永不打扰
    2. 配置文件**损坏** → 需要（多半是被手工改坏，重走一遍顺便自动修复）
    3. 从没扫描过任何库（无 ``last_root``）→ 需要（全新安装）
    4. 其余（有 last_root 等有效配置）→ 不需要
    """
    if load_config().get("setup_done"):
        return False
    if config_error():
        return True
    return not load_config().get("last_root")


def save_config(patch: dict) -> dict:
    """合并写回。值为 None 或占位符的敏感键表示"保持原值"。"""
    cfg = load_config()
    for k, v in (patch or {}).items():
        if k not in CONFIG_KEYS or v is None:
            continue
        if k in SECRET_KEYS and v == UNCHANGED:
            continue                     # 用户没改 key，别用占位符覆盖掉真的
        cfg[k] = v
    _write_private(config_path(), json.dumps(cfg, ensure_ascii=False, indent=1))
    return cfg


# ---------------------------------------------------------------- 访问令牌
def env_api_key() -> str:
    return (os.environ.get("FNLRC_REMOTE_API_KEY") or "").strip()


def effective_api_key(cfg: dict | None = None) -> str:
    """真正拿去发请求的 key。环境变量优先于配置文件。"""
    return env_api_key() or (cfg or load_config()).get("remote_api_key", "")


def api_key_locked() -> bool:
    """key 由环境变量提供 → 设置页该栏只读。"""
    return bool(env_api_key())


def token_path() -> Path:
    return data_dir() / "token"


def get_token(create: bool = True) -> str:
    """本机访问令牌。首次调用时生成并落盘。

    它的敏感度和 API key 同级 —— 只凭它就能触发写入与回滚，所以同样不写进日志、
    不随报告导出、不出现在 /api/config 的响应里。
    """
    p = token_path()
    if p.is_file():
        try:
            t = p.read_text(encoding="utf-8").strip()
            if t:
                return t
        except OSError:
            pass
    if not create:
        return ""
    t = secrets.token_urlsafe(24)
    _write_private(p, t)
    return t


def new_token() -> str:
    """重新生成令牌（旧的立即失效）。"""
    t = secrets.token_urlsafe(24)
    _write_private(token_path(), t)
    return t


# ---------------------------------------------------------------- 任务报告
def jobs_dir() -> Path:
    d = data_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_report(job_id: str, payload: dict) -> None:
    p = jobs_dir() / f"{job_id}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def load_report(job_id: str) -> dict | None:
    p = jobs_dir() / f"{job_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_reports(limit: int = 50) -> list[dict]:
    out = []
    for p in sorted(jobs_dir().glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({"job_id": p.stem, "root": d.get("root", ""),
                    "stats": d.get("stats") or {}, "started_at": d.get("started_at")})
    return out
