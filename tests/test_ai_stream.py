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
