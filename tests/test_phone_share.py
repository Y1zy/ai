"""手机投屏（扫码配对）单元测试。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from system_audio_asr import phone_share


@pytest.fixture()
def phone_path(tmp_path: Path) -> Path:
    return tmp_path / "phone_share.json"


def test_store_roundtrip_and_defaults(phone_path: Path):
    config = phone_share.load_phone_config(phone_path)
    assert config["enabled"] is False
    assert len(config["sid"]) == 8
    assert len(config["token"]) == 32

    saved = phone_share.set_enabled(True, phone_path)
    assert saved["enabled"] is True
    reloaded = phone_share.load_phone_config(phone_path)
    assert reloaded["sid"] == config["sid"]
    assert reloaded["token"] == config["token"]


def test_regenerate_keeps_enabled(phone_path: Path):
    phone_share.set_enabled(True, phone_path)
    before = phone_share.load_phone_config(phone_path)
    after = phone_share.regenerate_phone_config(phone_path)
    assert after["sid"] != before["sid"]
    assert after["token"] != before["token"]
    assert after["enabled"] is True


def test_share_url_contains_sid_and_token(phone_path: Path):
    config = phone_share.load_phone_config(phone_path)
    url = phone_share.build_share_url(8765, config)
    assert url.startswith("http://")
    assert ":8765/phone#sid=" in url
    assert "t=" + config["token"] in url


def test_capture_screen_jpeg_returns_jpeg():
    data = phone_share.capture_screen_jpeg()
    assert isinstance(data, bytes) and len(data) > 1000
    assert data[:2] == b"\xff\xd8"


def test_qr_svg_generation():
    svg = phone_share.qr_svg_data_url("http://192.168.1.5:8765/phone#sid=ab&t=cd")
    assert svg is not None
    assert svg.startswith("data:image/svg+xml;base64,")


def test_clipboard_roundtrip():
    original = phone_share.get_clipboard_text()
    if not phone_share.set_clipboard_text("voxribbon-剪贴板测试-123"):
        pytest.skip("剪贴板被其他进程占用")
    try:
        assert phone_share.get_clipboard_text() == "voxribbon-剪贴板测试-123"
    finally:
        if original:
            phone_share.set_clipboard_text(original)


def test_clipboard_write_retries_when_busy(monkeypatch):
    """剪贴板被其他进程短暂占用（OpenClipboard 失败）时必须重试，而不是直接失败。"""
    attempts = []

    def flaky_once(text: str) -> bool:
        attempts.append(text)
        return len(attempts) >= 3  # 前两次模拟被占用

    monkeypatch.setattr(phone_share, "_set_clipboard_text_once", flaky_once)
    monkeypatch.setattr(phone_share.time, "sleep", lambda _seconds: None)
    assert phone_share.set_clipboard_text("重试后的文本") is True
    assert len(attempts) == 3


def test_clipboard_write_gives_up_after_limit(monkeypatch):
    """持续占用时最多重试固定次数后返回 False，不无限阻塞。"""
    attempts = []
    monkeypatch.setattr(
        phone_share, "_set_clipboard_text_once",
        lambda text: attempts.append(text) or False,
    )
    monkeypatch.setattr(phone_share.time, "sleep", lambda _seconds: None)
    assert phone_share.set_clipboard_text("写不进去") is False
    assert len(attempts) == phone_share.CLIPBOARD_WRITE_ATTEMPTS


def test_clipboard_poll_is_responsive():
    """轮询间隔必须明显小于旧的 0.8 秒，否则电脑→手机要等半秒以上。"""
    assert phone_share.CLIPBOARD_POLL_SECONDS <= 0.25


def test_clipboard_watcher_poll_and_echo_guard(monkeypatch):
    relay = phone_share.PhoneRelay()
    watcher = phone_share.ClipboardWatcher()
    watcher.relay = relay
    monkeypatch.setattr(phone_share, "clipboard_sequence", lambda: 1)
    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "第一段")
    assert watcher.poll_once() == "第一段"
    assert relay.latest_clipboard_text == "第一段"
    assert watcher.poll_once() is None  # 序列号未变化

    # 序列号变化 = 剪贴板确实被写入过（GetClipboardSequenceNumber 只在写入时自增）。
    # 即使用户重新复制的是同一段文字也应推送：早期按内容比对去重，
    # 会让「重新复制同一段话」被静默跳过，用户无法把内容再同步到手机。
    monkeypatch.setattr(phone_share, "clipboard_sequence", lambda: 2)
    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "第一段")
    assert watcher.poll_once() == "第一段"

    monkeypatch.setattr(phone_share, "clipboard_sequence", lambda: 3)
    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "第二段")
    monkeypatch.setattr(phone_share, "set_clipboard_text", lambda text: True)
    watcher.accept_from_phone("手机来的文本")
    assert watcher.poll_once() is None  # 手机推送引起的回环不再广播

    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "第二段")
    monkeypatch.setattr(phone_share, "set_clipboard_text", lambda text: True)
    watcher.accept_from_phone("第二段")
    monkeypatch.setattr(phone_share, "clipboard_sequence", lambda: 4)
    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "第三段")
    assert watcher.poll_once() == "第三段"


def test_clipboard_read_failure_does_not_consume_sequence(monkeypatch):
    """读取失败（剪贴板被其他进程占用）不能消费序列号。

    早期实现先记序列号再读内容，读取失败时序列号已被消费，
    该次复制会被永久丢弃——序列号不再变化，也就不会再重试。
    """
    relay = phone_share.PhoneRelay()
    watcher = phone_share.ClipboardWatcher()
    watcher.relay = relay
    watcher.latest_clipboard_text = "初始"
    monkeypatch.setattr(phone_share, "clipboard_sequence", lambda: 42)

    # 第一次：读取返回空（模拟 OpenClipboard 失败）
    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "")
    assert watcher.poll_once() is None
    assert watcher.latest_clipboard_text == "初始"

    # 第二次：同一序列号，这次能读到内容 → 必须成功推送
    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "用户复制的文本")
    assert watcher.poll_once() == "用户复制的文本"
    assert relay.latest_clipboard_text == "用户复制的文本"


def test_clipboard_phone_text_recopied_later_is_pushed(monkeypatch):
    """手机推来的文本之后在电脑上重新复制，应能再次推送（回环抑制只作用于紧接的一次）。"""
    relay = phone_share.PhoneRelay()
    watcher = phone_share.ClipboardWatcher()
    watcher.relay = relay
    monkeypatch.setattr(phone_share, "set_clipboard_text", lambda text: True)
    monkeypatch.setattr(phone_share, "clipboard_sequence", lambda: 1)
    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "手机文本")
    watcher.accept_from_phone("手机文本")

    # 紧接的那次（回环）应被抑制
    assert watcher.poll_once() is None, "回环应被抑制"
    # 之后序列号再变化（用户重新复制同一段文字）→ 应正常推送到手机
    monkeypatch.setattr(phone_share, "clipboard_sequence", lambda: 2)
    assert watcher.poll_once() == "手机文本", "重新复制同一段文字被永久跳过"


def test_broken_phone_config_falls_back_to_defaults(phone_path: Path):
    """phone_share.json 被写坏成数组/字符串/null 时不应抛异常。

    早期 raw.get 会抛 AttributeError，导致 /api/phone/* 返回 500、
    /relay 握手异常关闭（手机扫码后一直"未连接"且无明确报错）。
    """
    for content in ('[1,2,3]', '"str"', 'null', '123'):
        phone_path.write_text(content, encoding="utf-8")
        config = phone_share.load_phone_config(phone_path)
        assert config["enabled"] is False
        assert len(config["sid"]) == 8
        assert len(config["token"]) == 32


def test_vision_key_roundtrip(tmp_path: Path, monkeypatch):
    key_path = tmp_path / "vision.key"
    monkeypatch.setattr(phone_share, "VISION_KEY_PATH", key_path)
    phone_share.save_vision_key("vk-test-123", key_path)
    assert phone_share.load_vision_key(key_path) == "vk-test-123"
    phone_share.save_vision_key("", key_path)
    assert phone_share.load_vision_key(key_path) == ""


def test_vision_config_reads_shared_config(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "visionEnabled": True,
            "visionBaseUrl": "https://example.com/v1/",
            "visionModel": "test-vision",
            "resumeContext": "简历内容",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(phone_share, "CONFIG_PATH", config_path)
    vision = phone_share.load_vision_config()
    assert vision["enabled"] is True
    assert vision["baseUrl"] == "https://example.com/v1"  # 尾斜杠已去掉
    assert vision["model"] == "test-vision"
    assert vision["resume"] == "简历内容"


def test_solve_engine_streams_and_throttles(monkeypatch):
    engine = phone_share.SolveEngine()
    events: list[tuple[str, bool]] = []
    monkeypatch.setattr(phone_share, "load_vision_config", lambda: {
        "enabled": True,
        "baseUrl": "https://vision.example/v1",
        "model": "test-vision",
        "resume": "简历",
        "jd": "",
        "prompt": phone_share.SOLVE_PROMPT,
    })
    monkeypatch.setattr(phone_share, "load_vision_key", lambda: "vk-123")

    class FakeResponse:
        status_code = 200

        def __init__(self, lines):
            self._lines = lines

        def iter_lines(self):
            return iter(self._lines)

        def read(self):
            return b""

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
            payload = json
            assert payload["messages"][0]["content"][0]["type"] == "image_url"
            assert payload["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            assert payload["stream"] is True
            sse_lines = [
                'data: {"choices":[{"delta":{"content":"答案A"}}]}',
                'data: {"choices":[{"delta":{"content":"答案B"}}]}',
                "data: [DONE]",
            ]
            return FakeResponse(sse_lines)

    monkeypatch.setattr("httpx.Client", FakeClient)
    monkeypatch.setattr(
        "system_audio_asr.settings.validate_public_http_url", lambda url: url
    )  # 跳过 SSRF 校验：vision.example 无法解析 DNS
    monkeypatch.setattr(phone_share.time, "monotonic", lambda: 1e9)  # 时间冻结 → 每次都攒住，由 force 落盘
    engine.on_delta = lambda text, done: events.append((text, done))
    engine.solve(b"\xff\xd8fake")
    while engine.busy:
        time.sleep(0.01)
    joined = "".join(text for text, _ in events)
    assert "答案A" in joined and "答案B" in joined
    assert events[-1][1] is True  # 最后一条 done=True
    assert any("[Resume]" in text for text, _ in events) is False  # 简历在 user_text，不在 delta 里


def test_solve_engine_rejects_when_disabled(monkeypatch):
    engine = phone_share.SolveEngine()
    events: list[tuple[str, bool]] = []
    monkeypatch.setattr(phone_share, "load_vision_config", lambda: {
        "enabled": False, "baseUrl": "", "model": "", "resume": "", "jd": "",
        "prompt": phone_share.SOLVE_PROMPT,
    })
    engine.on_delta = lambda text, done: events.append((text, done))
    engine.solve(b"\xff\xd8fake")
    while engine.busy:
        time.sleep(0.01)
    assert events and events[-1][1] is True
    assert "未启用" in events[-1][0]


class TestRelayEndpoints:
    @pytest.fixture()
    def client(self, phone_path: Path, monkeypatch):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        pytest.importorskip("soundcard")
        from system_audio_asr.config import AppConfig
        from system_audio_asr.server import create_app

        monkeypatch.setattr(phone_share, "PHONE_CONFIG_PATH", phone_path)
        phone_share.set_enabled(True, phone_path)
        config = AppConfig(
            host="0.0.0.0",
            port=8765,
            speaker=None,
            capture_rate=48000,
            capture_block_ms=100,
            silence_db=-42.0,
            endpoint_silence_ms=900,
            preroll_ms=200,
            model="paraformer-zh-streaming",
            hub=None,
            device="cpu",
            language="zh",
        )
        # 不进入 lifespan（不启动 ASR 引擎），仅验证 HTTP/WS 路由
        # client 显式设为本机回环地址，require_local 才会放行
        return fastapi_testclient.TestClient(
            create_app(config), client=("127.0.0.1", 51000)
        )

    def test_relay_rejects_bad_token(self, client, phone_path: Path):
        config = phone_share.load_phone_config(phone_path)
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/relay?role=phone&sid=" + config["sid"] + "&t=wrong"
            ) as websocket:
                websocket.receive_json()

    def test_relay_trigger_pushes_jpeg(self, client, phone_path: Path, monkeypatch):
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"x" * 2048
        monkeypatch.setattr(phone_share, "capture_screen_jpeg", lambda: fake_jpeg)
        config = phone_share.load_phone_config(phone_path)
        with client.websocket_connect(
            "/relay?role=phone&sid=" + config["sid"] + "&t=" + config["token"]
        ) as websocket:
            hello = websocket.receive_json()
            assert hello["type"] == "hello"
            websocket.send_text(json.dumps({"type": "trigger"}))
            data = websocket.receive_bytes()
            assert data[:2] == b"\xff\xd8"

    def test_relay_rejects_when_disabled(self, client, phone_path: Path):
        phone_share.set_enabled(False, phone_path)
        config = phone_share.load_phone_config(phone_path)
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/relay?role=phone&sid=" + config["sid"] + "&t=" + config["token"]
            ) as websocket:
                websocket.receive_json()

    def test_phone_page_served(self, client):
        response = client.get("/phone")
        assert response.status_code == 200
        assert "手机投屏" in response.text

    def test_phone_status_public(self, client, phone_path: Path):
        response = client.get("/api/phone/status")
        assert response.status_code == 200
        assert response.json()["enabled"] is True

    def test_local_routes_blocked_for_lan(self, client, phone_path: Path, monkeypatch):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        from system_audio_asr.config import AppConfig
        from system_audio_asr.server import create_app

        monkeypatch.setattr(phone_share, "PHONE_CONFIG_PATH", phone_path)
        lan_client = fastapi_testclient.TestClient(
            create_app(AppConfig()), client=("192.168.1.50", 51000)
        )
        for path in ("/", "/health", "/devices", "/api/settings", "/api/phone/info"):
            assert lan_client.get(path).status_code == 403, path

    def test_ai_feed_accepts_loopback(self, client):
        response = client.post("/api/phone/ai", json={"text": "你好", "done": True})
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_relay_clipboard_message_writes_pc_clipboard(
        self, client, phone_path: Path, monkeypatch
    ):
        written = []
        monkeypatch.setattr(
            phone_share, "set_clipboard_text", lambda text: written.append(text) or True
        )
        config = phone_share.load_phone_config(phone_path)
        with client.websocket_connect(
            "/relay?role=phone&sid=" + config["sid"] + "&t=" + config["token"]
        ) as websocket:
            assert websocket.receive_json()["type"] == "hello"
            websocket.send_text(json.dumps({"type": "clipboard", "text": "hello pc"}))
            ack = websocket.receive_json()
            assert ack["type"] == "clipboard_ack"
            assert ack["ok"] is True
        assert "hello pc" in written

    def test_relay_solve_pushes_jpeg_and_ai_frames(
        self, client, phone_path: Path, monkeypatch
    ):
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"y" * 1024
        monkeypatch.setattr(phone_share, "capture_screen_jpeg", lambda: fake_jpeg)
        monkeypatch.setattr(phone_share, "load_vision_config", lambda: {
            "enabled": True,
            "baseUrl": "https://vision.example/v1",
            "model": "test-vision",
            "resume": "",
            "jd": "",
            "prompt": phone_share.SOLVE_PROMPT,
        })
        monkeypatch.setattr(phone_share, "load_vision_key", lambda: "vk-123")

        class FakeResponse:
            status_code = 200

            def iter_lines(self):
                return iter([
                    'data: {"choices":[{"delta":{"content":"解:选B"}}]}',
                    "data: [DONE]",
                ])

            def read(self):
                return b""

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
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        monkeypatch.setattr(
            "system_audio_asr.settings.validate_public_http_url", lambda url: url
        )  # 跳过 SSRF 校验：vision.example 无法解析 DNS

        config = phone_share.load_phone_config(phone_path)
        with client.websocket_connect(
            "/relay?role=phone&sid=" + config["sid"] + "&t=" + config["token"]
        ) as websocket:
            assert websocket.receive_json()["type"] == "hello"
            websocket.send_text(json.dumps({"type": "solve"}))
            first = websocket.receive_bytes()
            assert first[:2] == b"\xff\xd8"  # 截图帧先到
            frames = []
            for _ in range(4):
                message = websocket.receive_json()
                frames.append(message)
                if message.get("type") == "ai" and message.get("done"):
                    break
            ai_frames = [m for m in frames if m.get("type") == "ai" and m.get("source") == "solve"]
            assert ai_frames, frames
            assert ai_frames[-1]["done"] is True
            assert "选B" in ai_frames[-1]["text"]

    def test_phone_solve_endpoint(self, client, phone_path: Path, monkeypatch):
        monkeypatch.setattr(phone_share, "capture_screen_jpeg", lambda: b"\xff\xd8stub")
        monkeypatch.setattr(phone_share, "load_vision_config", lambda: {
            "enabled": False, "baseUrl": "", "model": "", "resume": "", "jd": "",
        })
        response = client.post("/api/phone/solve")
        assert response.status_code == 200
        assert response.json()["ok"] is True  # 未配置时也触发（引擎会把错误推给手机）

    def test_phone_info_loopback(self, client, phone_path: Path):
        response = client.get("/api/phone/info")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["shareUrl"].startswith("http://")
        assert data["port"] == 8765


class TestAutoCaptureLifecycle:
    """自动刷新截图的生命周期：没有手机时不应继续截屏。"""

    def test_capture_skipped_without_phones(self, monkeypatch):
        """没有手机连接时不截屏。

        早期实现先截屏再判断 _phones：手机断开后 auto_loop 仍会按间隔持续
        全屏截图，既浪费资源，又对隐私工具而言在无人消费时仍采集屏幕。
        """
        import asyncio

        relay = phone_share.PhoneRelay()
        calls = {"n": 0}

        def fake_capture():
            calls["n"] += 1
            return b"\xff\xd8fake"

        monkeypatch.setattr(phone_share, "capture_screen_jpeg", fake_capture)
        asyncio.run(relay._capture_and_push())
        assert calls["n"] == 0, "无手机连接时仍在截屏"

    def test_stop_auto_capture_clears_frame(self):
        relay = phone_share.PhoneRelay()
        relay._latest_jpeg = b"\xff\xd8old"
        before = relay._auto_generation
        relay.stop_auto_capture()
        assert relay._auto_generation == before + 1, "generation 未递增，auto_loop 不会退出"
        assert relay._latest_jpeg is None, "缓存帧未清空"

    def test_solve_busy_notifies_phone(self):
        """解题忙碌时要给手机一条 done 提示，否则气泡永远停在"正在解题"。"""
        relay = phone_share.PhoneRelay()
        relay.solve_engine._busy.acquire()
        events: list[tuple[str, bool]] = []
        original = relay._on_solve_delta
        relay._on_solve_delta = lambda text, done: events.append((text, done))
        try:
            assert relay.request_solve() is False
        finally:
            relay._on_solve_delta = original
            relay.solve_engine._busy.release()
        assert events and events[-1][1] is True, "忙碌时未通知手机"
        assert "进行中" in events[-1][0]

    def test_stale_solve_stream_dropped_after_new_session(self):
        """「开始新一场」后，上一场在途的解题快照不应污染新一场。"""
        relay = phone_share.PhoneRelay()
        sent: list[dict] = []
        relay.schedule_json = lambda payload: sent.append(payload)

        relay._solve_generation = relay._session_generation  # 当前场次的解题
        relay.begin_new_session()                             # 用户点了「开始新一场」
        sent.clear()

        relay._on_solve_delta("上一场的答案片段", False)
        assert sent == [], "旧场次的流式快照泄漏到新一场"

        # 末帧仍要放行，否则手机上那条气泡会永远停在流式状态
        relay._on_solve_delta("上一场的完整答案", True)
        assert len(sent) == 1 and sent[0]["done"] is True


def test_phone_ask_forwards_max_tokens(monkeypatch):
    """手机追问应把「回答长度」档位传给流式调用（与字幕 AI 同一档位）。"""
    import asyncio

    from system_audio_asr import ai_stream
    from system_audio_asr import server
    from system_audio_asr import settings as settings_module

    relay = phone_share.PhoneRelay()
    relay.schedule_json = lambda payload: None
    captured: dict = {}

    def fake_stream(**kwargs):
        captured.update(kwargs)
        return "回答"

    monkeypatch.setattr(ai_stream, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(
        settings_module, "load_settings",
        lambda path=None, strict=False: {
            **settings_module.DEFAULTS,
            "aiMaxTokens": 512, "aiModel": "m", "aiBaseUrl": "https://api.example/v1",
        },
    )
    monkeypatch.setattr(settings_module, "load_api_key", lambda path=None: "test-key")
    monkeypatch.setattr(server, "effective_system_prompt", lambda: "系统提示词")

    asyncio.run(relay._handle_ask("测一道题"))
    assert captured.get("max_tokens") == 512, "手机追问未把档位传给请求"


def test_phone_ask_defaults_max_tokens_when_key_missing(monkeypatch):
    """旧配置没有 aiMaxTokens 键时，追问链路的实际请求应发出默认 2048。

    这里走真实的 stream_chat_completion（只拦 httpx），验证 relay → ai_stream
    的完整兜底链路，而不是只验证 relay 传了 None。
    """
    import asyncio

    from system_audio_asr import server
    from system_audio_asr import settings as settings_module

    relay = phone_share.PhoneRelay()
    relay.schedule_json = lambda payload: None
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def iter_lines(self):
            return iter(['data: {"choices":[{"delta":{"content":"回答"}}]}', "data: [DONE]"])

        def read(self):
            return b""

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
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)
    monkeypatch.setattr("system_audio_asr.settings.validate_public_http_url", lambda url: url)

    payload = {k: v for k, v in settings_module.DEFAULTS.items() if k != "aiMaxTokens"}
    monkeypatch.setattr(settings_module, "load_settings", lambda path=None, strict=False: payload)
    monkeypatch.setattr(settings_module, "load_api_key", lambda path=None: "test-key")
    monkeypatch.setattr(server, "effective_system_prompt", lambda: "系统提示词")

    asyncio.run(relay._handle_ask("测一道题"))
    assert captured.get("payload"), "未发出请求"
    assert captured["payload"]["max_tokens"] == 2048, "缺键时未回退默认档位"


def test_phone_status_ok_when_config_broken(tmp_path: Path, monkeypatch):
    """phone_share.json 被写坏时 /api/phone/status 不应 500。"""
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    pytest.importorskip("soundcard")
    from system_audio_asr.config import AppConfig
    from system_audio_asr.server import create_app

    broken = tmp_path / "phone_share.json"
    broken.write_text("[1,2,3]", encoding="utf-8")
    monkeypatch.setattr(phone_share, "PHONE_CONFIG_PATH", broken)
    client = fastapi_testclient.TestClient(
        create_app(AppConfig()), client=("127.0.0.1", 51000)
    )
    assert client.get("/api/phone/status").status_code == 200
