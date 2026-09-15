"""ai_stream：三条链路（字幕 AI / 截图解题 / 手机追问）共用的 SSE 流式解析。

关键契约：on_snapshot 收到的一律是「累计全文快照」，不是增量片段。
桌面 C# 气泡、手机页气泡、面试记录器都按整体替换渲染，推增量会显示错乱。
"""
from __future__ import annotations

import pytest

from system_audio_asr import ai_stream


def _fake_stream(monkeypatch, lines, status_code: int = 200):
    """把 httpx.Client.stream 替换为固定 SSE 行的假实现，并跳过 SSRF 域名解析。"""
    captured: dict = {}

    class FakeResponse:
        def __init__(self):
            self.status_code = status_code

        def iter_lines(self):
            return iter(lines)

        def read(self):
            return b'{"error":"boom"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, json=None, headers=None):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)
    # api.example 无法解析 DNS；校验本身由 test_settings 单独覆盖
    monkeypatch.setattr(
        "system_audio_asr.settings.validate_public_http_url", lambda url: url
    )
    return captured


def test_snapshots_are_cumulative_not_deltas(monkeypatch):
    """每个快照都必须是"到目前为止的全文"，可直接整体替换渲染。"""
    _fake_stream(monkeypatch, [
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: {"choices":[{"delta":{"content":"呀"}}]}',
        "data: [DONE]",
    ])
    seen: list[tuple[str, bool]] = []
    final = ai_stream.stream_chat_completion(
        url="https://api.example/v1/chat/completions",
        api_key="k",
        model="m",
        messages=[{"role": "user", "content": "q"}],
        on_snapshot=lambda t, d: seen.append((t, d)),
        flush_seconds=0,  # 每次都推，便于断言
    )
    texts = [t for t, _ in seen]
    assert final == "你好呀"
    assert texts == ["你", "你好", "你好呀", "你好呀"], "快照不是累计全文"
    # 每个快照都是最终全文的前缀 —— 这才是"可直接替换渲染"的真正含义
    assert all("你好呀".startswith(t) for t in texts)
    # 末条重复文本是刻意的：手机端靠 done=True 关闭气泡，否则下一条回答会串进来
    assert seen[-1] == ("你好呀", True)
    assert all(d is False for _, d in seen[:-1])


def test_throttle_keeps_partial_and_final_snapshot(monkeypatch):
    """节流只减少推送次数，不改变"快照"语义；最终必有一次全文。"""
    _fake_stream(monkeypatch, [
        'data: {"choices":[{"delta":{"content":"A"}}]}',
        'data: {"choices":[{"delta":{"content":"B"}}]}',
        'data: {"choices":[{"delta":{"content":"C"}}]}',
        "data: [DONE]",
    ])
    seen: list[tuple[str, bool]] = []
    # flush_seconds 极大 → 中间不发，只在结束时发一次
    final = ai_stream.stream_chat_completion(
        url="https://api.example/v1/chat/completions",
        api_key="k",
        model="m",
        messages=[{"role": "user", "content": "q"}],
        on_snapshot=lambda t, d: seen.append((t, d)),
        flush_seconds=1e9,
    )
    assert final == "ABC"
    assert len(seen) == 1 and seen[0] == ("ABC", True), "节流不应漏掉最终全文"


def test_ignores_non_data_lines_and_empty_choices(monkeypatch):
    _fake_stream(monkeypatch, [
        ": keep-alive",
        "",
        'data: {"choices":[]}',
        'data: not-json',
        'data: {"choices":[{"delta":{"content":"X"}}]}',
        "data: [DONE]",
        "data: ",
    ])
    seen: list[str] = []
    final = ai_stream.stream_chat_completion(
        url="https://api.example/v1/chat/completions",
        api_key="k",
        model="m",
        messages=[{"role": "user", "content": "q"}],
        on_snapshot=lambda t, d: seen.append(t),
        flush_seconds=0,
    )
    assert final == "X"


def test_http_error_raises_with_body(monkeypatch):
    _fake_stream(monkeypatch, [], status_code=401)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        ai_stream.stream_chat_completion(
            url="https://api.example/v1/chat/completions",
            api_key="bad",
            model="m",
            messages=[{"role": "user", "content": "q"}],
            on_snapshot=lambda t, d: None,
        )


def test_empty_response_yields_empty_and_done(monkeypatch):
    """模型只回 reasoning 不给 content 时，必须仍发出一次 done，避免手机一直转圈。"""
    _fake_stream(monkeypatch, ["data: [DONE]"])
    seen: list[tuple[str, bool]] = []
    final = ai_stream.stream_chat_completion(
        url="https://api.example/v1/chat/completions",
        api_key="k",
        model="m",
        messages=[{"role": "user", "content": "q"}],
        on_snapshot=lambda t, d: seen.append((t, d)),
    )
    assert final == ""
    assert seen == [("", True)]


def test_build_messages_orders_system_history_question():
    messages = ai_stream.build_messages(
        "系统提示",
        "本次问题",
        history=[
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "tool", "content": "忽略我"},
            {"role": "user", "content": "   "},
        ],
    )
    assert messages[0] == {"role": "system", "content": "系统提示"}
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1] == {"role": "user", "content": "本次问题"}


def test_build_messages_caps_history_at_twelve():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}条"}
        for i in range(30)
    ]
    messages = ai_stream.build_messages("s", "q", history)
    assert len(messages) == 1 + 12 + 1


# ---------------------------------------------------------------- 思考模式
# 思考型模型默认先输出 reasoning_content 再给正文，首字延迟可达 10 秒以上。
# 关闭思考可把首字降到 1 秒内，且正文更完整（token 不再被思考占用）。


def test_apply_thinking_mode_off_sends_disabled() -> None:
    payload: dict = {"model": "m"}
    ai_stream.apply_thinking_mode(payload, "off")
    assert payload["thinking"] == {"type": "disabled"}


def test_apply_thinking_mode_auto_sends_nothing() -> None:
    """auto 表示不干预，不能发送任何相关字段（保持模型默认行为）。"""
    payload: dict = {"model": "m"}
    ai_stream.apply_thinking_mode(payload, "auto")
    assert "thinking" not in payload
    assert "reasoning_effort" not in payload, "不得使用 reasoning_effort：实测会让正文返回空"


@pytest.mark.parametrize("value", ["forced", "", None, "AUTO", "unknown"])
def test_normalize_thinking_mode_falls_back_to_auto(value) -> None:
    assert ai_stream.normalize_thinking_mode(value) == "auto"


def test_normalize_thinking_mode_accepts_off() -> None:
    assert ai_stream.normalize_thinking_mode("off") == "off"
    assert ai_stream.normalize_thinking_mode("OFF") == "off"


def test_thinking_mode_is_sent_in_stream_payload(monkeypatch) -> None:
    captured = _fake_stream(monkeypatch, [
        'data: {"choices":[{"delta":{"content":"X"}}]}',
        "data: [DONE]",
    ])
    ai_stream.stream_chat_completion(
        url="https://api.example/v1/chat/completions",
        api_key="k", model="m",
        messages=[{"role": "user", "content": "q"}],
        on_snapshot=lambda t, d: None,
        thinking_mode="off",
    )
    assert captured["payload"]["thinking"] == {"type": "disabled"}


def test_thinking_mode_auto_omits_field(monkeypatch) -> None:
    captured = _fake_stream(monkeypatch, [
        'data: {"choices":[{"delta":{"content":"X"}}]}',
        "data: [DONE]",
    ])
    ai_stream.stream_chat_completion(
        url="https://api.example/v1/chat/completions",
        api_key="k", model="m",
        messages=[{"role": "user", "content": "q"}],
        on_snapshot=lambda t, d: None,
        thinking_mode="auto",
    )
    assert "thinking" not in captured["payload"]


def test_is_thinking_unsupported_detects_rejection() -> None:
    assert ai_stream.is_thinking_unsupported(400, b'{"error":"unknown field thinking"}')
    assert ai_stream.is_thinking_unsupported(422, b"unsupported parameter: thinking")
    assert not ai_stream.is_thinking_unsupported(400, b'{"error":"model not found"}')
    assert not ai_stream.is_thinking_unsupported(500, b"thinking blew up"), "5xx 不是参数问题"


def test_stream_downgrades_when_gateway_rejects_thinking(monkeypatch) -> None:
    """网关不认 thinking 字段时应自动去掉该字段重试，而不是直接失败。

    否则用户开启「关闭思考」后可能反而完全不可用。
    """
    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, status_code, lines=None):
            self.status_code = status_code
            self._lines = lines or []

        def iter_lines(self):
            return iter(self._lines)

        def read(self):
            return b'{"error":"unknown parameter thinking"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, json=None, headers=None):
            calls.append(dict(json))
            if len(calls) == 1:
                return FakeResponse(400)
            return FakeResponse(200, ['data: {"choices":[{"delta":{"content":"重试成功"}}]}', "data: [DONE]"])

    monkeypatch.setattr("httpx.Client", FakeClient)
    monkeypatch.setattr("system_audio_asr.settings.validate_public_http_url", lambda url: url)

    seen: list[str] = []
    final = ai_stream.stream_chat_completion(
        url="https://api.example/v1/chat/completions",
        api_key="k", model="m",
        messages=[{"role": "user", "content": "q"}],
        on_snapshot=lambda t, d: seen.append(t),
        thinking_mode="off",
    )
    assert len(calls) == 2, "未触发降级重试"
    assert calls[0].get("thinking") == {"type": "disabled"}
    assert "thinking" not in calls[1], "重试请求仍带 thinking 字段"
    assert final == "重试成功"


# ---------------------------------------------------------------- 回答长度档位
# max_tokens 档位控制单次回答的篇幅；非法值回退、区间值吸附到最近档，
# 保证两端固定档位下拉永远能显示出当前值。


@pytest.mark.parametrize("value, expected", [
    (256, 256),
    (512, 512),
    (1024, 1024),
    (2048, 2048),
    (4096, 4096),
    (8192, 8192),
    ("1024", 1024),        # 网页表单可能以字符串提交
    (1024.0, 1024),
    (3000, 2048),          # 吸附到最近档（往小）
    (1500, 1024),          # 吸附到最近档（居中偏小）
    (5000, 4096),          # 吸附到最近档（往大）
    (99999, 8192),         # 超出上限吸附到最大档
    (1e15, 8192),          # 大浮点（与 C# Load 的 double 路径对拍用）
    (1, 256),              # 低于下限吸附到最小档
    (None, 2048),          # 缺键 / 垃圾值回退默认
    ("", 2048),
    ("forced", 2048),
    (float("inf"), 2048),  # json 的 1e400 会解析成 inf；int(inf) 抛 OverflowError
    (float("-inf"), 2048),
    (float("nan"), 2048),
    ("1e400", 2048),
])
def test_normalize_max_tokens(value, expected) -> None:
    assert ai_stream.normalize_max_tokens(value) == expected


def test_max_tokens_is_sent_in_stream_payload(monkeypatch) -> None:
    captured = _fake_stream(monkeypatch, [
        'data: {"choices":[{"delta":{"content":"X"}}]}',
        "data: [DONE]",
    ])
    ai_stream.stream_chat_completion(
        url="https://api.example/v1/chat/completions",
        api_key="k", model="m",
        messages=[{"role": "user", "content": "q"}],
        on_snapshot=lambda t, d: None,
        max_tokens=512,
    )
    assert captured["payload"]["max_tokens"] == 512


def test_max_tokens_defaults_to_2048_when_omitted(monkeypatch) -> None:
    """不传 max_tokens 时保持历史默认 2048，避免调用方漏改导致额度变化。"""
    captured = _fake_stream(monkeypatch, [
        'data: {"choices":[{"delta":{"content":"X"}}]}',
        "data: [DONE]",
    ])
    ai_stream.stream_chat_completion(
        url="https://api.example/v1/chat/completions",
        api_key="k", model="m",
        messages=[{"role": "user", "content": "q"}],
        on_snapshot=lambda t, d: None,
    )
    assert captured["payload"]["max_tokens"] == 2048
