"""手机投屏（扫码配对）：局域网直连查看电脑屏幕截图与实时字幕。

配对流程与参考实现一致：电脑端生成二维码（内容为
``http://<局域网IP>:<端口>/phone#sid=<会话>&t=<令牌>``），手机扫码后在浏览器
打开配对页并通过 ``/relay`` WebSocket 直连电脑；手机发送 trigger 指令，电脑
截屏后以 JPEG 二进制帧推回。令牌与会话保存在独立的 phone_share.json 中，
不与 config.json 混写（C# Overlay 会整体重写 config.json）。

另含手机↔电脑剪贴板同步：电脑端用 GetClipboardSequenceNumber 轮询剪贴板，
变化即推送到手机；手机发送的文本直接写入电脑剪贴板。
"""
from __future__ import annotations

import asyncio
import base64
import ctypes
import io
import json
import os
import secrets
import socket
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WasapiParaformerOverlay"
PHONE_CONFIG_PATH = APP_DIR / "phone_share.json"
CONFIG_PATH = APP_DIR / "config.json"

MAX_IMAGE_WIDTH = 1600
JPEG_QUALITY = 75
TRANSCRIPT_EVENT_TYPES = {"partial", "final", "status"}
MAX_CLIPBOARD_CHARS = 50000
CLIPBOARD_POLL_SECONDS = 0.8

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

VISION_KEY_PATH = APP_DIR / "vision.key"
VISION_ENTROPY = b"WasapiParaformerOverlay.Vision.v1"
SOLVE_PROMPT = (
    "请识别图中的题目或问题，直接给出简洁的答案与关键步骤。"
    "如果是代码题给出核心代码；如果是选择题先给选项字母再解释。"
    "不要复述题目，不要输出多余客套话。"
    "直接输出纯文本，不要使用 Markdown 标记（如 **加粗**、# 标题、代码块围栏）。"
)
SOLVE_STREAM_FLUSH_SECONDS = 0.15

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HGLOBAL
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HGLOBAL]
_user32.SetClipboardData.restype = wintypes.HGLOBAL
_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL

_store_lock = threading.Lock()


def clipboard_sequence() -> int:
    return int(_user32.GetClipboardSequenceNumber())


def get_clipboard_text() -> str:
    if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    if not _user32.OpenClipboard(None):
        return ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    payload = text.encode("utf-16-le") + b"\x00\x00"
    if not _user32.OpenClipboard(None):
        return False
    try:
        _user32.EmptyClipboard()
        handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not handle:
            return False
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            _kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(pointer, payload, len(payload))
        finally:
            _kernel32.GlobalUnlock(handle)
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        _user32.CloseClipboard()


def load_phone_config(path: Path | None = None) -> dict[str, Any]:
    target = path or PHONE_CONFIG_PATH
    with _store_lock:
        try:
            raw = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raw = {}
    config = {
        "enabled": bool(raw.get("enabled", False)),
        "sid": str(raw.get("sid") or ""),
        "token": str(raw.get("token") or ""),
    }
    if not config["sid"] or not config["token"]:
        config["sid"] = config["sid"] or secrets.token_hex(4)
        config["token"] = config["token"] or secrets.token_hex(16)
        save_phone_config(config, target)
    return config


def save_phone_config(config: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or PHONE_CONFIG_PATH
    payload = {
        "enabled": bool(config.get("enabled", False)),
        "sid": str(config.get("sid") or secrets.token_hex(4)),
        "token": str(config.get("token") or secrets.token_hex(16)),
    }
    with _store_lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    return payload


def set_enabled(value: bool, path: Path | None = None) -> dict[str, Any]:
    config = load_phone_config(path)
    config["enabled"] = bool(value)
    return save_phone_config(config, path)


def regenerate_phone_config(path: Path | None = None) -> dict[str, Any]:
    config = load_phone_config(path)
    config["sid"] = secrets.token_hex(4)
    config["token"] = secrets.token_hex(16)
    return save_phone_config(config, path)


def lan_ipv4() -> str:
    """探测本机局域网 IPv4（UDP connect 不发包，只读路由源地址）。"""
    for probe in ("223.5.5.5", "8.8.8.8"):
        try:
            probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe_socket.connect((probe, 53))
                return probe_socket.getsockname()[0]
            finally:
                probe_socket.close()
        except OSError:
            continue
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def build_share_url(port: int, config: dict[str, Any]) -> str:
    return "http://{0}:{1}/phone#sid={2}&t={3}".format(
        lan_ipv4(), port, config["sid"], config["token"])


def capture_screen_jpeg() -> bytes:
    from PIL import Image, ImageGrab

    image = ImageGrab.grab(all_screens=True)
    if image is None:
        raise RuntimeError("屏幕采集失败")
    if image.mode != "RGB":
        image = image.convert("RGB")
    if image.width > MAX_IMAGE_WIDTH:
        height = max(1, round(image.height * MAX_IMAGE_WIDTH / image.width))
        image = image.resize((MAX_IMAGE_WIDTH, height))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def qr_svg_data_url(url: str) -> str | None:
    try:
        import segno
    except ImportError:
        return None
    buffer = io.BytesIO()
    segno.make(url, error="m").save(buffer, kind="svg", scale=4, border=2, dark="#0b1220")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return "data:image/svg+xml;base64," + encoded


def _dpapi_protect(value: str, entropy: bytes) -> bytes:
    import ctypes

    from ctypes import wintypes as wt

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wt.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    def blob(data: bytes) -> tuple[DataBlob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(data, len(data))
        return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer

    clear, clear_buffer = blob(value.encode("utf-8"))
    entropy_blob, entropy_buffer = blob(entropy)
    output = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(clear), None, ctypes.byref(entropy_blob), None, None, 0, ctypes.byref(output)
    )
    _ = (clear_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _dpapi_unprotect(data: bytes, entropy: bytes) -> str:
    import ctypes

    from ctypes import wintypes as wt

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wt.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    def blob(source: bytes) -> tuple[DataBlob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(source, len(source))
        return DataBlob(len(source), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer

    encrypted, encrypted_buffer = blob(data)
    entropy_blob, entropy_buffer = blob(entropy)
    output = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(encrypted),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(output),
    )
    _ = (encrypted_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output.pbData)


def load_vision_key(path: Path | None = None) -> str:
    target = path or VISION_KEY_PATH
    try:
        return _dpapi_unprotect(target.read_bytes(), VISION_ENTROPY)
    except (OSError, ValueError, Exception):
        return ""


def save_vision_key(value: str, path: Path | None = None) -> None:
    target = path or VISION_KEY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    if not value.strip():
        if target.exists():
            target.unlink()
        return
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(_dpapi_protect(value.strip(), VISION_ENTROPY))
    os.replace(temporary, target)


def load_vision_config() -> dict[str, Any]:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")) if CONFIG_PATH.exists() else {}
    except (OSError, ValueError):
        raw = {}
    return {
        "enabled": bool(raw.get("visionEnabled", False)),
        "baseUrl": str(raw.get("visionBaseUrl", "")).rstrip("/"),
        "model": str(raw.get("visionModel", "")),
        "resume": str(raw.get("resumeContext", "")),
        "jd": str(raw.get("jdContext", "")),
        # 允许用户在设置里自定义解题提示词；为空回落内置默认。
        "prompt": str(raw.get("solvePrompt", "")).strip() or SOLVE_PROMPT,
    }


class SolveEngine:
    """OpenAI 兼容视觉模型截图解题：流式 SSE，delta 经节流回调推送。"""

    def __init__(self) -> None:
        self._busy = threading.Lock()
        self.on_delta: Any = None  # callable(text, done: bool)

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def solve(self, jpeg: bytes) -> None:
        """后台线程执行；结果与错误都通过 on_delta 回调（text/done）通知。"""
        if not self._busy.acquire(blocking=False):
            self._emit("上一个解题请求还在进行中，请稍候", True)
            return

        def runner() -> None:
            try:
                self._run_stream(jpeg)
            except Exception as exc:
                self._emit(f"解题失败：{exc}", True)
            finally:
                self._busy.release()

        threading.Thread(target=runner, name="solve-engine", daemon=True).start()

    def _emit(self, text: str, done: bool) -> None:
        callback = self.on_delta
        if callback is not None:
            try:
                callback(text, done)
            except Exception:
                pass

    def _run_stream(self, jpeg: bytes) -> None:
        import httpx

        vision = load_vision_config()
        api_key = load_vision_key()
        if not vision["enabled"]:
            raise RuntimeError("视觉模型未启用（设置页勾选启用）")
        if not vision["baseUrl"] or not vision["model"]:
            raise RuntimeError("视觉模型 BaseURL / 模型名未配置")
        if not api_key:
            raise RuntimeError("视觉模型 API Key 未配置")
        if not vision["baseUrl"].startswith(("http://", "https://")):
            raise RuntimeError("视觉模型 BaseURL 必须以 http:// 或 https:// 开头")
        from .settings import validate_public_http_url

        try:
            validate_public_http_url(vision["baseUrl"] + "/chat/completions")
        except ValueError as exc:
            raise RuntimeError(f"视觉模型 {exc}") from exc

        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        context_parts = []
        if vision["resume"]:
            context_parts.append("[Resume]\n" + vision["resume"][:4000])
        if vision["jd"]:
            context_parts.append("[JD]\n" + vision["jd"][:2000])
        context_block = ("\n\n".join(context_parts) + "\n\n") if context_parts else ""
        user_text = context_block + vision["prompt"]

        payload = {
            "model": vision["model"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
            "stream": True,
            "max_tokens": 1500,
            "temperature": 0.2,
        }

        accumulated: list[str] = []
        pending: list[str] = []
        pending_lock = threading.Lock()
        last_flush = time.monotonic()

        def flush(force: bool = False) -> None:
            nonlocal last_flush
            with pending_lock:
                if not pending:
                    return
                elapsed = time.monotonic() - last_flush
                if not force and elapsed < SOLVE_STREAM_FLUSH_SECONDS and len(pending) < 8:
                    return
                chunk = "".join(pending)
                pending.clear()
            last_flush = time.monotonic()
            accumulated.append(chunk)
            self._emit(chunk, False)

        with httpx.Client(
            timeout=httpx.Timeout(90.0, read=90.0),
            # Cloudflare 站点（如部分中转 API）会按 UA 封禁默认的 python-httpx 签名
            headers={"User-Agent": "VoxRibbon/0.1"},
        ) as client:
            with client.stream(
                "POST",
                vision["baseUrl"] + "/chat/completions",
                json=payload,
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Accept": "text/event-stream",
                },
            ) as response:
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"HTTP {response.status_code}: {body[:300]}")
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        with pending_lock:
                            pending.append(content)
                        flush()

        flush(force=True)
        final_text = "".join(accumulated).strip()
        self._emit(final_text if final_text else "（模型未返回内容）", True)


class ClipboardWatcher:
    """轮询电脑剪贴板，变化即推送给已连接的手机。"""

    def __init__(self) -> None:
        self.relay: PhoneRelay | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_sequence = 0
        self._last_text = ""
        self._last_pushed_from_phone = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="clipboard-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def accept_from_phone(self, text: str) -> bool:
        """手机推送的文本写入电脑剪贴板；记录来源防止回环广播。"""
        self._last_pushed_from_phone = text
        ok = False
        try:
            ok = set_clipboard_text(text)
        except Exception:
            ok = False
        if ok:
            self._last_sequence = clipboard_sequence()
            self._last_text = text
            if self.relay is not None:
                self.relay.latest_clipboard_text = text
        return ok

    def poll_once(self) -> str | None:
        sequence = clipboard_sequence()
        if sequence == self._last_sequence:
            return None
        self._last_sequence = sequence
        try:
            text = get_clipboard_text()
        except Exception:
            return None
        if not text or text == self._last_text or text == self._last_pushed_from_phone:
            return None
        self._last_text = text
        if len(text) > MAX_CLIPBOARD_CHARS:
            text = text[:MAX_CLIPBOARD_CHARS]
        if self.relay is not None:
            self.relay.latest_clipboard_text = text
            self.relay.schedule_json({"type": "clipboard", "text": text, "from": "desktop"})
        return text

    def _run(self) -> None:
        try:
            self._last_sequence = clipboard_sequence()
        except Exception:
            pass
        while not self._stop.wait(CLIPBOARD_POLL_SECONDS):
            try:
                self.poll_once()
            except Exception:
                pass


class PhoneRelay:
    """管理手机端 WebSocket：JPEG 二进制推送、触发指令、字幕事件转发。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._phones: set[Any] = set()
        self._latest_jpeg: bytes | None = None
        self._auto_generation = 0
        self.clipboard_handler: Any = None
        self.latest_clipboard_text = ""
        self.solve_engine = SolveEngine()
        self.solve_engine.on_delta = self._on_solve_delta
        self.desktop_publisher: Any = None  # callable(payload: dict) 桌面字幕窗分发

    def _on_solve_delta(self, text: str, done: bool) -> None:
        self.schedule_json({"type": "ai", "text": text, "done": done, "source": "solve"})
        publisher = self.desktop_publisher
        if publisher is not None:
            try:
                publisher({"type": "solve_answer", "text": text, "done": done})
            except Exception:
                pass

    def request_solve(self) -> bool:
        """触发一次截图解题；返回 False 表示引擎未启用或正在进行。"""
        if self.solve_engine.busy:
            self._on_solve_delta("上一个解题请求还在进行中，请稍候", True)
            return False
        try:
            jpeg = capture_screen_jpeg()
        except Exception:
            self._on_solve_delta("电脑端屏幕采集失败", True)
            return False
        from .recorder import session_recorder

        session_recorder.add_solve_image(jpeg)
        self._latest_jpeg = jpeg
        self._drain_bytes_threadsafe(jpeg)
        self.solve_engine.solve(jpeg)
        return True

    def _drain_bytes_threadsafe(self, data: bytes) -> None:
        if not self._phones:
            return
        loop = self._running_loop()
        if loop is None:
            return
        loop.call_soon_threadsafe(self._drain_bytes, data)

    def bind_loop(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def handle(self, websocket: Any, sid: str, token: str) -> None:
        config = load_phone_config()
        if (
            not config["enabled"]
            or not sid
            or sid != config["sid"]
            or not token
            or token != config["token"]
        ):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        self._phones.add(websocket)
        try:
            await websocket.send_json({"type": "hello", "sid": sid})
            latest = self._latest_jpeg
            if latest:
                await websocket.send_bytes(latest)
            if self.latest_clipboard_text:
                await websocket.send_json(
                    {"type": "clipboard", "text": self.latest_clipboard_text, "from": "desktop"}
                )
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                text = message.get("text")
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                kind = str(payload.get("type", ""))
                if kind == "trigger":
                    asyncio.ensure_future(self._capture_and_push())
                elif kind == "solve":
                    asyncio.ensure_future(self._handle_solve())
                elif kind == "ask":
                    phone_text = str(payload.get("text", ""))[:4000]
                    if phone_text:
                        asyncio.ensure_future(self._handle_ask(phone_text))
                elif kind == "get_prompt":
                    asyncio.ensure_future(self._handle_get_prompt())
                elif kind == "auto":
                    self._set_auto(bool(payload.get("on")), payload.get("interval"))
                elif kind == "clipboard":
                    phone_text = str(payload.get("text", ""))[:MAX_CLIPBOARD_CHARS]
                    if phone_text:
                        asyncio.ensure_future(self._handle_clipboard(phone_text))
        except Exception:
            pass
        finally:
            self._phones.discard(websocket)

    async def _handle_solve(self) -> None:
        await asyncio.to_thread(self.request_solve)

    async def _handle_ask(self, question: str) -> None:
        """手机文字提问：走与字幕 AI 相同的真实管线（提示词+简历/JD 上下文）。"""
        from .server import _ask_ai_blocking, effective_system_prompt
        from .settings import load_settings, load_api_key

        settings = await asyncio.to_thread(load_settings)
        api_key = await asyncio.to_thread(load_api_key)
        if not api_key:
            self.schedule_json({"type": "ai", "text": "电脑端尚未配置 AI 接口 API Key", "done": True, "source": "ask"})
            return
        prompt = effective_system_prompt()
        if not prompt:
            self.schedule_json({"type": "ai", "text": "系统提示词为空，请检查设置页", "done": True, "source": "ask"})
            return

        def run():
            return _ask_ai_blocking(prompt, question, settings, api_key)

        try:
            result = await asyncio.to_thread(run)
            self.schedule_json({"type": "ai", "text": result["answer"], "done": True, "source": "ask"})
        except Exception as exc:
            self.schedule_json({"type": "ai", "text": f"请求失败：{exc}", "done": True, "source": "ask"})

    async def _handle_get_prompt(self) -> None:
        from .server import effective_system_prompt

        prompt = effective_system_prompt()
        if prompt:
            self.schedule_json({"type": "prompt", "prompt": prompt})

    async def _handle_clipboard(self, text: str) -> None:
        handler = self.clipboard_handler
        ok = False
        if handler is not None:
            try:
                ok = bool(await asyncio.to_thread(handler, text))
            except Exception:
                ok = False
        self.schedule_json({"type": "clipboard_ack", "ok": ok})

    def publish_event(self, event: dict) -> None:
        """EventHub 事件转发（字幕 partial/final 与状态）。"""
        if str(event.get("type", "")) not in TRANSCRIPT_EVENT_TYPES:
            return
        self.schedule_json(event)

    def schedule_json(self, payload: dict) -> None:
        if not self._phones:
            return
        message = json.dumps(payload, ensure_ascii=False)
        loop = self._running_loop()
        if loop is None:
            return
        loop.call_soon_threadsafe(self._drain_json, message)

    def _running_loop(self) -> asyncio.AbstractEventLoop | None:
        if self._loop is not None:
            return self._loop
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _drain_json(self, message: str) -> None:
        for websocket in tuple(self._phones):
            asyncio.ensure_future(self._safe_send_text(websocket, message))

    def _drain_bytes(self, data: bytes) -> None:
        for websocket in tuple(self._phones):
            asyncio.ensure_future(self._safe_send_bytes(websocket, data))

    async def _safe_send_text(self, websocket: Any, message: str) -> None:
        try:
            await websocket.send_text(message)
        except Exception:
            self._phones.discard(websocket)

    async def _safe_send_bytes(self, websocket: Any, data: bytes) -> None:
        try:
            await websocket.send_bytes(data)
        except Exception:
            self._phones.discard(websocket)

    def _set_auto(self, on: bool, interval_ms: Any) -> None:
        self._auto_generation += 1
        if not on or self._running_loop() is None:
            return
        try:
            interval = float(interval_ms)
        except (TypeError, ValueError):
            interval = 3000.0
        interval = max(1000.0, min(10000.0, interval)) / 1000.0
        generation = self._auto_generation

        async def auto_loop() -> None:
            while generation == self._auto_generation:
                await self._capture_and_push()
                await asyncio.sleep(interval)

        asyncio.ensure_future(auto_loop())

    async def _capture_and_push(self) -> None:
        try:
            jpeg = await asyncio.to_thread(capture_screen_jpeg)
        except Exception:
            self.schedule_json({"type": "error", "message": "电脑端屏幕采集失败"})
            return
        if not jpeg:
            return
        self._latest_jpeg = jpeg
        if self._phones:
            loop = self._running_loop()
            if loop is not None:
                loop.call_soon_threadsafe(self._drain_bytes, jpeg)
