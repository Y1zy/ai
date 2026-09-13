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
    max_tokens: int = 500,
    temperature: float = 0.3,
    timeout: float = 90.0,
    flush_seconds: float = STREAM_FLUSH_SECONDS,
    validate: bool = True,
) -> str:
    """流式调用并返回最终全文；失败抛异常，由调用方决定如何提示用户。

    validate=False 供调用方（如视觉解题）先用更具体的错误文案自行校验。
    """
    import httpx

    if validate:
        from .settings import validate_public_http_url

        validate_public_http_url(url)

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

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
        with client.stream(
            "POST",
            url,
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
                    accumulated.append(content)
                    snapshot()

    final_text = "".join(accumulated).strip()
    on_snapshot(final_text, True)
    return final_text
