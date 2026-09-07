from __future__ import annotations

import ctypes
import http.client
import ipaddress
import json
import os
import re
import socket
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any


APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WasapiParaformerOverlay"
CONFIG_PATH = APP_DIR / "config.json"
KEY_PATH = APP_DIR / "deepseek.key"
ENTROPY = b"WasapiParaformerOverlay.DeepSeek.v1"

DEFAULTS: dict[str, Any] = {
    "left": None,
    "top": None,
    "width": 980.0,
    "height": 150.0,
    "fontSize": 36.0,
    "maxLines": 3,
    "opacity": 0.88,
    "fadeDelayMs": 1800,
    "fontFamily": "Microsoft YaHei UI",
    "textColor": "#FFFFFF",
    "frameMode": "hover",
    "frameColor": "#7DBEFF",
    "frameOpacity": 0.69,
    "locked": False,
    "screenName": "",
    "webSocketUrl": "ws://127.0.0.1:8765/ws",
    "asrLanguage": "zh",
    "liveTranslateEnabled": False,
    "aiEnabled": False,
    "aiModel": "deepseek-v4-flash",
    "aiMode": "auto",
    "aiSilenceSeconds": 0.6,
    "aiSystemPrompt": "",
    "aiOverridePrompt": "",
    "aiBuiltInPrompt": "",
    "aiBaseUrl": "https://api.deepseek.com",
    "hotwordEnabled": True,
    "hotwordExtra": "",
    "solvePrompt": "",
    "visionEnabled": False,
    "visionBaseUrl": "",
    "visionModel": "",
}


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect_secret(value: str) -> bytes:
    clear, clear_buffer = _blob(value.encode("utf-8"))
    entropy, entropy_buffer = _blob(ENTROPY)
    output = DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(clear), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(output)
    )
    _ = (clear_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def unprotect_secret(data: bytes) -> str:
    encrypted, encrypted_buffer = _blob(data)
    entropy, entropy_buffer = _blob(ENTROPY)
    output = DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(encrypted), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(output)
    )
    _ = (encrypted_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def load_api_key(path: Path = KEY_PATH) -> str:
    try:
        return unprotect_secret(path.read_bytes())
    except (OSError, UnicodeError):
        return ""


def save_api_key(value: str, path: Path = KEY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = protect_secret(value.strip())
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encrypted)
    os.replace(temporary, path)


def clear_api_key(path: Path = KEY_PATH) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def load_settings(path: Path = CONFIG_PATH, *, strict: bool = False) -> dict[str, Any]:
    if not path.exists():
        return normalize_settings(dict(DEFAULTS))
    last_error: Exception | None = None
    for attempt in range(4):
        result = dict(DEFAULTS)
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                result.update({key: value for key, value in raw.items() if key in DEFAULTS})
            return normalize_settings(result)
        except (OSError, ValueError, TypeError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.04)
    if strict:
        raise RuntimeError("配置文件正在更新，请稍后重试") from last_error
    return normalize_settings(dict(DEFAULTS))


def normalize_settings(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(DEFAULTS)
    result.update({key: item for key, item in value.items() if key in DEFAULTS})
    result["width"] = max(280.0, min(2200.0, float(result["width"])))
    result["height"] = max(72.0, min(800.0, float(result["height"])))
    result["fontSize"] = max(12.0, min(96.0, float(result["fontSize"])))
    result["maxLines"] = max(1, min(10, int(result["maxLines"])))
    result["opacity"] = max(0.45, min(0.98, float(result["opacity"])))
    result["frameOpacity"] = max(0.0, min(1.0, float(result["frameOpacity"])))
    result["aiSilenceSeconds"] = max(0.5, min(8.0, float(result["aiSilenceSeconds"])))
    result["aiEnabled"] = bool(result["aiEnabled"])
    result["liveTranslateEnabled"] = bool(result["liveTranslateEnabled"])
    result["locked"] = bool(result["locked"])
    if result["asrLanguage"] not in {"zh", "en"}:
        result["asrLanguage"] = "zh"
    if result["aiModel"] not in {"deepseek-v4-flash", "deepseek-v4-pro"} and not result["aiModel"]:
        result["aiModel"] = "deepseek-v4-flash"
    if result["aiMode"] not in {"auto", "summary", "qa", "explain", "translate"}:
        result["aiMode"] = "auto"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(result["textColor"])):
        result["textColor"] = "#FFFFFF"
    if result["frameMode"] not in {"hover", "always"}:
        result["frameMode"] = "hover"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(result["frameColor"])):
        result["frameColor"] = "#7DBEFF"
    for key in ("fontFamily", "screenName", "aiSystemPrompt", "aiOverridePrompt", "aiBuiltInPrompt",
                "aiBaseUrl",
                "hotwordExtra", "solvePrompt", "webSocketUrl",
                "visionBaseUrl", "visionModel"):
        result[key] = str(result[key] or DEFAULTS[key])
    result["visionEnabled"] = bool(result["visionEnabled"])
    result["hotwordEnabled"] = bool(result.get("hotwordEnabled", True))
    return result


def save_settings(value: dict[str, Any], path: Path = CONFIG_PATH) -> dict[str, Any]:
    normalized = normalize_settings(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return normalized


def public_settings() -> dict[str, Any]:
    return {
        "settings": load_settings(),
        "apiKeySet": bool(load_api_key()),
        "monitors": monitor_names(),
    }


# 与 C# OverlayApp.DeepSeekClient.PromptForMode 保持同源的内置提示词模板，
# 供设置页查看系统自带的提示词（后端只读拼装，不参与实际请求）。
_MODE_INSTRUCTIONS: dict[str, str] = {
    "auto": "若内容中包含明确问题，直接回答；否则用一句话总结或解释重点。回答简洁，不复述全文；转写可能有少量错误，请结合上下文理解。",
    "summary": "请用一句简洁中文总结这段语音的核心信息。",
    "qa": "请识别语音中的问题并直接给出简洁、准确的中文回答。若没有问题，说明未检测到明确问题。",
    "explain": "请用简洁中文解释这段语音涉及的概念或意图，不要复述全文。",
    "translate": "请将这段中文转写准确翻译为自然、简洁的英文，只输出译文。",
}
_PERSONA_WITH_CONTEXT = (
    "你是面试辅助助手。以下是候选人的真实背景资料，请基于这些经历来理解和回答问题，"
    "必要时直接引用候选人的项目/技能/公司经历，让回答更贴合候选人实际，而不是泛泛而谈。"
)
_PERSONA_PLAIN = "你是实时字幕助手。"
_NO_MARKDOWN_LINE = "直接输出纯文本，不要使用 Markdown 标记（如 **加粗**、# 标题、代码块围栏）。"


def overlay_context_block() -> str:
    """读取 C# Overlay 写入 config.json 的面试上下文（简历/JD/公司/附加背景）。"""
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")) if CONFIG_PATH.exists() else {}
    except (OSError, ValueError):
        raw = {}
    sections: list[str] = []
    for key, label in (
        ("resumeContext", "[Resume]"),
        ("jdContext", "[JD]"),
        ("targetCompany", "[Target Company]"),
        ("extraContext", "[Extra Context]"),
    ):
        value = str(raw.get(key) or "").strip()
        if value:
            sections.append(label + "\n" + value)
    return ("\n\n".join(sections) + "\n\n") if sections else ""


def builtin_prompts() -> dict[str, Any]:
    """系统内置提示词全集：字幕 AI 各处理模式模板 + 截图解题内置默认。"""
    settings = load_settings()
    context_block = overlay_context_block()
    persona = _PERSONA_WITH_CONTEXT if context_block else _PERSONA_PLAIN
    extra = str(settings.get("aiSystemPrompt") or "").strip()

    modes: dict[str, str] = {}
    for mode, instruction in _MODE_INSTRUCTIONS.items():
        prompt = (
            context_block
            + "这是连续的面试转写内容。请结合前几轮上下文理解当前消息，并先默默修正明显的识别错误。\n"
            + persona + "\n" + instruction
            + "\n" + _NO_MARKDOWN_LINE
        )
        if extra:
            prompt += "\n附加要求：" + extra
        modes[mode] = prompt

    override = str(settings.get("aiOverridePrompt") or "").strip()
    override_prompt = ""
    if override:
        override_prompt = context_block + override
        if extra:
            override_prompt += "\n附加要求：" + extra

    from .phone_share import SOLVE_PROMPT

    return {
        "contextBlock": context_block,
        "modes": modes,
        "overridePrompt": override_prompt or None,
        "solveDefault": SOLVE_PROMPT,
        "solveCustom": str(settings.get("solvePrompt") or "").strip() or None,
        "aiBuiltInPrompt": str(settings.get("aiBuiltInPrompt") or ""),
    }


def monitor_names() -> list[str]:
    monitors: list[tuple[bool, str]] = []

    class Rect(ctypes.Structure):
        _fields_ = [(name, wintypes.LONG) for name in ("left", "top", "right", "bottom")]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", Rect),
            ("rcWork", Rect),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(Rect), wintypes.LPARAM
    )

    def callback(monitor, _hdc, _rect, _data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            monitors.append((bool(info.dwFlags & 1), info.szDevice))
        return True

    try:
        callback_ref = callback_type(callback)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_ref, 0)
    except (AttributeError, OSError):
        pass
    return [name for _primary, name in sorted(monitors, key=lambda item: (not item[0], item[1]))]


def update_from_web(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_settings(strict=True)
    supplied = payload.get("settings", payload)
    if isinstance(supplied, dict):
        current.update({key: value for key, value in supplied.items() if key in DEFAULTS})
    api_key = payload.get("apiKey")
    if isinstance(api_key, str) and api_key.strip():
        save_api_key(api_key)
    if payload.get("clearApiKey") is True:
        clear_api_key()
    vision_key = payload.get("visionApiKey")
    if isinstance(vision_key, str) and vision_key.strip():
        from .phone_share import save_vision_key

        save_vision_key(vision_key)
    if payload.get("clearVisionApiKey") is True:
        from .phone_share import save_vision_key

        save_vision_key("")
    return save_settings(current)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS 连接：证书/SNI 按原域名校验，但 TCP 直连已校验的公网 IP（防 DNS rebinding）。"""

    def __init__(self, ip: str, hostname: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._tunnel_host:
            self._tunnel()
        server_hostname = self.host
        if server_hostname.startswith("[") and server_hostname.endswith("]"):
            server_hostname = server_hostname[1:-1]
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


def _resolve_public_ips(hostname: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, port)
    except socket.gaierror as exc:
        raise ValueError(f"接口地址无法解析：{hostname}") from exc
    ips: list[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("接口地址不允许指向本机或内网/保留地址")
        if info[4][0] not in ips:
            ips.append(info[4][0])
    if not ips:
        raise ValueError("接口地址无法解析：" + hostname)
    return ips


def request_public_http(
    url: str,
    *,
    method: str = "POST",
    body: bytes | None = None,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, bytes]:
    """SSRF 安全请求：校验 URL 为公网 http(s) 并直连已校验 IP，返回 (状态码, 响应体)。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仅允许 http/https 接口地址")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("接口地址缺少主机名")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    target_ip = _resolve_public_ips(hostname, port)[0]
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        connection = _PinnedHTTPSConnection(target_ip, hostname, port, timeout=timeout)
    else:
        connection = http.client.HTTPConnection(target_ip, port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        status = response.status
    except (OSError, http.client.HTTPException) as exc:
        raise RuntimeError(f"接口请求失败：{exc}") from exc
    finally:
        connection.close()
    return status, payload


def validate_public_http_url(url: str) -> str:
    """SSRF 防护：服务端只请求公网 http(s) 地址，拒绝本机/内网/保留地址。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仅允许 http/https 接口地址")
    host = parsed.hostname
    if not host:
        raise ValueError("接口地址缺少主机名")
    _resolve_public_ips(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    return url


def test_deepseek() -> dict[str, Any]:
    settings = load_settings()
    api_key = load_api_key()
    if not api_key:
        raise ValueError("请先保存 DeepSeek API Key")
    endpoint = settings["aiBaseUrl"].rstrip("/") + "/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("接口地址必须以 http:// 或 https:// 开头")
    body = json.dumps(
        {
            "model": settings["aiModel"],
            "messages": [
                {"role": "system", "content": "你是连接测试助手。"},
                {"role": "user", "content": "请只回复：连接成功"},
            ],
            "stream": False,
            "max_tokens": 100,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    status, payload = request_public_http(
        endpoint,
        body=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "VoxRibbon/0.1",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=35,
    )
    if status >= 400:
        raise RuntimeError(f"AI HTTP {status}: {payload.decode('utf-8', errors='replace')[:300]}")
    result = json.loads(payload.decode("utf-8"))
    # 兼容中转站：reasoning 模型可能只返回 reasoning_content 而缺 content 字段
    choices = result.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    content = (message.get("content") or "").strip()
    if not content:
        raise RuntimeError(
            "接口返回 200 但 message.content 为空（思考型模型可能耗尽了 token，请换模型或调大 max_tokens）"
        )
    return {"ok": True, "message": content}
