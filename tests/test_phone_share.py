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


def test_clipboard_watcher_poll_and_echo_guard(monkeypatch):
    relay = phone_share.PhoneRelay()
    watcher = phone_share.ClipboardWatcher()
    watcher.relay = relay
    monkeypatch.setattr(phone_share, "clipboard_sequence", lambda: 1)
    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "第一段")
    assert watcher.poll_once() == "第一段"
    assert relay.latest_clipboard_text == "第一段"
    assert watcher.poll_once() is None  # 序列号未变化

    monkeypatch.setattr(phone_share, "clipboard_sequence", lambda: 2)
    monkeypatch.setattr(phone_share, "get_clipboard_text", lambda: "第一段")
    assert watcher.poll_once() is None  # 内容未变不重复推送

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
