"""远端翻译端点（DeepSeek）的请求契约。

这里盯的是**发给对方的东西**，不是翻译质量：

- 「测试连接」要能给出可区分的原因（错 key / 余额 / 模型名 / 限流）
- **思考模式必须显式关掉**：DeepSeek 官方文档写明 `thinking.type` 默认是 `enabled`
  且默认强度 `high`。翻译这种任务上思考链纯属负担 —— 慢、贵，而且会吃掉
  `max_tokens` 的预算，正文被截断后 `_parse_numbered` 只拿到一部分，
  过不了 90% 门槛，**整首降级为单语**。
  官方文档还写明 `temperature` 在思考模式下不生效，所以顺手发的 0.1 也是空设。
- 但那是 DeepSeek 专有字段，换成 one-api / vLLM / LM Studio 时严格端点会 400，
  所以要能退让。

发出去的请求全部打桩，不联网。
"""
from __future__ import annotations

import pytest

from fnlrc import providers as P


# ---------------------------------------------------------------- 夹具
class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.text = text

    def json(self):
        return self._payload


def _ok(content="1|你好"):
    return _Resp({"choices": [{"message": {"content": content}}]})


@pytest.fixture
def sent(monkeypatch):
    """把 requests.post 换掉，记下每次发出去的 (url, json)。"""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        calls.append({"url": url, "json": json, "headers": headers})
        return _ok()

    monkeypatch.setattr(P.requests, "post", fake_post)
    return calls


# ---------------------------------------------------------------- 端点识别
@pytest.mark.parametrize("url,want", [
    ("https://api.deepseek.com", True),
    ("https://api.deepseek.com/v1", True),
    ("https://api.deepseek.com/anthropic", True),
    # 只看 **主机名**：路径里带 deepseek 的自建代理识别不出来，
    # 那就走"不发专有字段"的保守路子（功能不受影响，只是关不掉思考模式）
    ("https://proxy.example.com/deepseek", False),
    ("https://api.openai.com/v1", False),
    ("http://127.0.0.1:8000/v1", False),
    ("", False),
    ("not a url", False),
])
def test_认得哪些是DeepSeek端点(url, want):
    assert P.is_deepseek(url) is want


# ---------------------------------------------------------------- 请求体
def test_给DeepSeek发请求会显式关掉思考模式(sent):
    P.remote_translate(["a", "b"], "https://api.deepseek.com", "sk-x", "deepseek-flash")

    assert sent, "应当发出了请求"
    body = sent[0]["json"]
    assert body["thinking"] == {"type": "disabled"}, "思考模式没关掉"
    assert body["model"] == "deepseek-flash"
    assert body["stream"] is False
    assert "max_tokens" in body and body["max_tokens"] > 0
    # 关掉思考后 temperature 才真的生效，所以留着它是有意义的
    assert body["temperature"] == 0.1


def test_给别的端点不发DeepSeek专有字段(sent):
    """one-api / vLLM / LM Studio 这类严格校验的端点会因未知字段 400。"""
    P.remote_translate(["a"], "https://api.openai.com/v1", "sk-x", "gpt-x")

    assert sent
    assert "thinking" not in sent[0]["json"]
    assert "reasoning_effort" not in sent[0]["json"]


def test_端点拒绝扩展字段时会去掉重试(monkeypatch):
    """万一对面 400 了，去掉专有字段再发一次，而不是直接失败。"""
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        seen.append(json)
        if "thinking" in json:
            return _Resp({"error": {"message": "unknown field 'thinking'"}}, status=400)
        return _ok()

    monkeypatch.setattr(P.requests, "post", fake_post)
    got, err = P.remote_translate(["a"], "https://api.deepseek.com", "sk-x", "deepseek-flash")

    assert len(seen) == 2, "应当去掉扩展字段重试一次"
    assert "thinking" in seen[0] and "thinking" not in seen[1]
    assert got == {1: "你好"}, "退让之后应当拿到译文"
    assert err is None


def test_退让后仍然失败就如实报错(monkeypatch):
    """错 key 这类致命错误不该被重试掩盖。"""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        calls.append(json)
        return _Resp({"error": {"message": "Authentication Fails"}}, status=401)

    monkeypatch.setattr(P.requests, "post", fake_post)
    got, err = P.remote_translate(["a"], "https://api.deepseek.com", "sk-bad", "deepseek-flash")

    assert got == {}
    # 报错会被人话化（见 REMOTE_ERR），不是裸的状态码
    assert err and ("401" in err or "API key" in err)


def test_错误信息里的key被打码(monkeypatch):
    """异常/报错说明会进报告的 note，绝不能漏 key。"""
    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        return _Resp({"error": {"message": "bad key sk-abcdef123456 rejected"}}, status=401)

    monkeypatch.setattr(P.requests, "post", fake_post)
    _got, err = P.remote_translate(["a"], "https://api.deepseek.com", "sk-abcdef123456", "m")
    assert "sk-abcdef123456" not in (err or "")
    assert "sk-***" in (err or "")


# ---------------------------------------------------------------- 测试连接
def test_测试连接成功时说明用的哪个模型(sent):
    ok, msg = P.remote_test("https://api.deepseek.com", "sk-x", "deepseek-flash")
    assert ok and "deepseek-flash" in msg


def test_测试连接给模型留够输出预算(sent):
    """万一对面关不掉思考模式，64 个 token 会被思考吃光、正文空 → 误报"连接有问题"。"""
    P.remote_test("https://api.deepseek.com", "sk-x", "deepseek-flash")
    assert sent[0]["json"]["max_tokens"] >= 256


def test_模型名不对时给得出人话(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        return _Resp({"error": {"message": "Model Not Exist"}}, status=400)

    monkeypatch.setattr(P.requests, "post", fake_post)
    ok, msg = P.remote_test("https://api.deepseek.com", "sk-x", "deepseek-chat")
    assert not ok
    assert "400" in msg or "模型" in msg
