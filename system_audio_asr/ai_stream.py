"""OpenAI 兼容 Chat Completions 的 SSE 流式调用（字幕 AI / 截图解题 / 手机追问共用）。

约定：回调收到的 text 始终是「截至当前的累计全文快照」，不是增量片段。
消费方（桌面 C# 气泡、手机页气泡、面试记录器）都以整体替换的方式渲染，
若推增量片段会只显示最后一小段、结束时才跳到全文，看起来像"没流式"。
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

# 快照节流：避免每个 token 都推一次（中转站可能按字符切分）。
STREAM_FLUSH_SECONDS = 0.15
STREAM_MIN_CHUNK_CHARS = 8

# 思考模式（thinking mode）。思考型模型默认先输出一段 reasoning_content 再给正文，
# 实测同一道题「关闭」能让首字延迟从约 11 秒降到 1 秒内，且正文反而更长
# （token 预算不再被思考占用）。三档取值：
#   "off"  —— 关闭思考（send thinking={type:disabled}），面试实时场景推荐
#   "auto" —— 不干预，由模型自行决定是否思考
# 注意：不要用 reasoning_effort 参数控制。实测该项在本机网关上适得其反：
# reasoning_effort="none" 会让模型把全部 token 用于思考、正文返回 0 字。
THINKING_OFF = "off"
THINKING_AUTO = "auto"
THINKING_MODES = (THINKING_OFF, THINKING_AUTO)
DEFAULT_THINKING_MODE = THINKING_AUTO


def normalize_thinking_mode(value: Any) -> str:
    """把任意输入规范到受支持的取值；无法识别时回退默认（不干预）。"""
    mode = str(value or "").strip().lower()
    return mode if mode in THINKING_MODES else DEFAULT_THINKING_MODE


def apply_thinking_mode(payload: dict[str, Any], mode: Any) -> dict[str, Any]:
    """按模式给请求体加上（或省略）思考控制字段。

    off 时显式发送 disabled；auto 时不发送任何相关字段，保持模型默认行为。
    """
    if normalize_thinking_mode(mode) == THINKING_OFF:
        payload["thinking"] = {"type": "disabled"}
    return payload


def is_thinking_unsupported(status: int, body: bytes) -> bool:
    """判断失败响应是否表示网关不认 thinking 字段。

    部分中转/上游会以 400/422 拒绝未知参数（报错里通常带 thinking 字样）。
    调用方据此自动降级重试，避免用户开了开关反而完全不可用。
    """
    if status not in (400, 422):
        return False
    text = body.decode("utf-8", errors="replace").lower()
    return "thinking" in text or "unknown" in text or "unsupported" in text or "invalid" in text


# 回答长度上限档位（max_tokens）。额度同时决定"回答能写多长"：给太多，模型会把
# 简单题展开成长文，更容易冒 Markdown 与代码示例；给太少，复杂题会被中途截断。
# 档位表与 C# OverlayConfig.MaxTokenLevels 各存一份（跨语言无法共享），改档位需同步。
MAX_TOKENS_LEVELS = (256, 512, 1024, 2048, 4096, 8192)
DEFAULT_MAX_TOKENS = 2048


def normalize_max_tokens(value: Any) -> int:
    """把任意输入规范到受支持的档位；无法识别时回退默认。

    区间内的值吸附到最近档（如 3000→2048、99999→8192）：两端 UI 都是固定档位
    下拉，吸附保证手改 config 后仍能显示出当前值，不会出现空选项后被清零。
    OverflowError 必须捕获：JSON 里的 1e400 会解析成 inf，int(inf) 抛的不是
    ValueError——漏掉它会让 load_settings 整体失败（设置页表现为全部配置丢失）。
    """
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MAX_TOKENS
    return min(MAX_TOKENS_LEVELS, key=lambda level: (abs(level - number), level))


def build_messages(prompt: str, question: str, history: list[dict] | None = None) -> list[dict]:
    """系统提示词 + 最近多轮历史 + 本次问题（与 _ask_ai_blocking 保持同一结构）。"""
    messages: list[dict] = [{"role": "system", "content": prompt}]
    for item in (history or [])[-12:]:
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    return messages


def stream_chat_completion(
    *,
    url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    on_snapshot: Callable[[str, bool], None],
    max_tokens: Any = DEFAULT_MAX_TOKENS,
    temperature: float = 0.3,
    timeout: float = 90.0,
    flush_seconds: float = STREAM_FLUSH_SECONDS,
    validate: bool = True,
    thinking_mode: Any = DEFAULT_THINKING_MODE,
) -> str:
    """流式调用并返回最终全文；失败抛异常，由调用方决定如何提示用户。

    validate=False 供调用方（如视觉解题）先用更具体的错误文案自行校验。
    thinking_mode 控制是否关闭思考；网关不认该字段时自动降级重试一次。
    """
    import httpx

    if validate:
        from .settings import validate_public_http_url

        validate_public_http_url(url)

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": normalize_max_tokens(max_tokens),
        "temperature": temperature,
    }
    apply_thinking_mode(payload, thinking_mode)

    accumulated: list[str] = []
    last_flush = time.monotonic()

    def snapshot(force: bool = False) -> None:
        nonlocal last_flush
        text = "".join(accumulated)
        if not text:
            return
        if not force and (time.monotonic() - last_flush) < flush_seconds:
            return
        last_flush = time.monotonic()
        on_snapshot(text, False)

    with httpx.Client(
        timeout=httpx.Timeout(timeout, read=timeout),
        # Cloudflare 站点（如部分中转 API）会按 UA 封禁默认的 python-httpx 签名
        headers={"User-Agent": "VoxRibbon/0.1"},
    ) as client:

        def run_once(request_payload: dict[str, Any]) -> None:
            """执行一次流式请求，把 delta 累积到 accumulated。"""
            with client.stream(
                "POST",
                url,
                json=request_payload,
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Accept": "text/event-stream",
                },
            ) as response:
                if response.status_code >= 400:
                    body = response.read()
                    # 网关不认 thinking 字段时自动降级：去掉该字段重试一次，
                    # 避免用户开启「关闭思考」后反而完全不可用。
                    if "thinking" in request_payload and is_thinking_unsupported(
                        response.status_code, body
                    ):
                        raise _ThinkingUnsupported(body)
                    raise RuntimeError(
                        f"HTTP {response.status_code}: {body.decode('utf-8', errors='replace')[:300]}"
                    )
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
                        accumulated.append(content)
                        snapshot()

        try:
            run_once(payload)
        except _ThinkingUnsupported:
            # 该网关不支持 thinking 字段：去掉后按默认（自动思考）重跑。
            fallback = {k: v for k, v in payload.items() if k != "thinking"}
            accumulated.clear()
            run_once(fallback)

    final_text = "".join(accumulated).strip()
    on_snapshot(final_text, True)
    return final_text


class _ThinkingUnsupported(Exception):
    """内部信号：网关拒绝 thinking 字段，调用方应去掉该字段重试。"""
