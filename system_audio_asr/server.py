from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from . import phone_share
from .capture import list_speakers
from .config import AppConfig
from .phone_share import ClipboardWatcher, PhoneRelay
from .recognizer import TranscriptionEngine
from .recorder import session_recorder
from .settings import (
    APP_DIR,
    builtin_prompts,
    load_api_key,
    load_settings,
    public_settings,
    test_deepseek,
    update_from_web,
    validate_public_http_url,
)
from .translation import LocalEnglishChineseTranslator

# C# Overlay 每次 AI 请求会把实际发送的系统提示词推回来（PromptFeed → /api/ai/prompt），
# 供设置页回显与手机端复用；桌面未触发过 AI 时回落到 effective_system_prompt() 计算。
last_ai_prompt: dict[str, str] = {"prompt": ""}


def effective_system_prompt() -> str:
    """当前实际生效的字幕 AI 系统提示词：优先用桌面端最近一次真实发送的；
    没有则按「完全自定义 > 自定义内置模板 > 系统默认」即时计算（含简历/JD 上下文）。"""
    pushed = last_ai_prompt["prompt"].strip()
    if pushed:
        return pushed
    from .settings import builtin_prompts

    prompts = builtin_prompts()
    if prompts.get("overridePrompt"):
        return prompts["overridePrompt"]
    return prompts["modes"]["auto"]


def _ask_ai_blocking(prompt: str, question: str, settings: dict, api_key: str) -> dict:
    """回答效果测试的阻塞调用：真实系统提示词 + 模拟问题，返回答案/实际模型/耗时。"""
    import json
    import time

    from .settings import request_public_http

    endpoint = settings["aiBaseUrl"].rstrip("/") + "/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("接口地址必须以 http:// 或 https:// 开头")
    body = json.dumps(
        {
            "model": settings["aiModel"],
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "转写文本：\n" + question},
            ],
            "stream": False,
            "max_tokens": 500,
            "temperature": 0.3,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    started = time.monotonic()
    status, payload = request_public_http(
        endpoint,
        body=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "VoxRibbon/0.1",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=90,
    )
    seconds = round(time.monotonic() - started, 1)
    if status >= 400:
        raise RuntimeError(f"AI HTTP {status}: {payload.decode('utf-8', errors='replace')[:300]}")
    result = json.loads(payload.decode("utf-8"))
    # 兼容中转站：reasoning 模型可能缺 content 字段
    choices = result.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    answer = (message.get("content") or "").strip()
    if not answer:
        raise RuntimeError("接口返回 200 但 message.content 为空（思考型模型可能耗尽 token）")
    return {
        "answer": answer,
        "model": str(result.get("model", "")),
        "seconds": seconds,
    }


class EventHub:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue[dict] | None = None
        self.clients: set[WebSocket] = set()
        self.listeners: list = []
        self._lock = threading.Lock()
        self._sequence = 0
        self.latest_status: dict | None = None
        self.latest_error: dict | None = None

    def bind(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=256)

    def publish(self, event: dict) -> None:
        with self._lock:
            self._sequence += 1
            payload = {
                **event,
                "seq": self._sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if event.get("type") == "status":
                self.latest_status = payload
            elif event.get("type") == "error":
                self.latest_error = payload
        if self.loop and self.queue:
            self.loop.call_soon_threadsafe(self._enqueue, payload)

    def _enqueue(self, payload: dict) -> None:
        assert self.queue is not None
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(payload)
        for listener in tuple(self.listeners):
            try:
                listener(payload)
            except Exception:
                pass

    async def dispatch(self) -> None:
        assert self.queue is not None
        while True:
            payload = await self.queue.get()
            message = json.dumps(payload, ensure_ascii=False)
            dead: list[WebSocket] = []
            for client in tuple(self.clients):
                try:
                    await client.send_text(message)
                except Exception:
                    dead.append(client)
            for client in dead:
                self.clients.discard(client)


def create_app(config: AppConfig) -> FastAPI:
    config.validate()
    hub = EventHub()
    runtime_config = config
    engine = TranscriptionEngine(runtime_config, hub.publish)
    engine_restart_lock = asyncio.Lock()
    translator = LocalEnglishChineseTranslator()
    phone_relay = PhoneRelay()
    clipboard_watcher = ClipboardWatcher()
    clipboard_watcher.relay = phone_relay
    phone_relay.clipboard_handler = clipboard_watcher.accept_from_phone
    phone_relay.desktop_publisher = hub.publish
    hub.listeners.append(phone_relay.publish_event)
    hub.listeners.append(session_recorder.on_event)
    records_root = APP_DIR / "records"

    async def restart_engine(language: str) -> None:
        nonlocal engine, runtime_config
        async with engine_restart_lock:
            if engine.config.language == language:
                return
            hub.publish({"type": "status", "state": "switching_language", "language": language})
            await asyncio.to_thread(engine.stop)
            runtime_config = replace(runtime_config, language=language)
            engine = TranscriptionEngine(runtime_config, hub.publish)
            engine.start()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        hub.bind()
        phone_relay.bind_loop()
        clipboard_watcher.start()
        dispatcher = asyncio.create_task(hub.dispatch())
        engine.start()
        try:
            yield
        finally:
            engine.stop()
            clipboard_watcher.stop()
            dispatcher.cancel()
            try:
                await dispatcher
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="WASAPI Paraformer WebSocket", version="0.1.0", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> str:
        require_local(request)
        return (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")

    def require_local(request: Request) -> None:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1"}:
            raise HTTPException(status_code=403, detail="设置接口只允许本机访问")

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> str:
        require_local(request)
        return (Path(__file__).parent / "web" / "settings.html").read_text(encoding="utf-8")

    @app.get("/api/settings")
    async def get_settings(request: Request) -> dict:
        require_local(request)
        return await asyncio.to_thread(public_settings)

    @app.post("/api/settings")
    async def put_settings(request: Request, payload: dict) -> dict:
        require_local(request)
        saved = await asyncio.to_thread(update_from_web, payload)
        restarted = saved.get("asrLanguage", "zh") != engine.config.language
        if restarted:
            await restart_engine(saved.get("asrLanguage", "zh"))
        if saved.get("liveTranslateEnabled"):
            asyncio.create_task(asyncio.to_thread(translator.warmup))
        return {
            "ok": True,
            "settings": saved,
            "apiKeySet": public_settings()["apiKeySet"],
            "asrRestarted": restarted,
        }

    @app.get("/api/translate/status")
    async def translation_status(request: Request) -> dict:
        require_local(request)
        return translator.status()

    @app.post("/api/translate/warmup")
    async def translation_warmup(request: Request) -> dict:
        require_local(request)
        try:
            return await asyncio.to_thread(translator.warmup)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/translate")
    async def translate_english(request: Request, payload: dict) -> dict:
        require_local(request)
        text = str(payload.get("text", "")).strip()
        if not text:
            return {"translation": "", **translator.status()}
        if len(text) > 4000:
            raise HTTPException(status_code=400, detail="翻译文本不能超过 4000 个字符")
        try:
            return await asyncio.to_thread(translator.translate, text)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/settings/test-deepseek")
    async def test_deepseek_api(request: Request) -> dict:
        require_local(request)
        try:
            return await asyncio.to_thread(test_deepseek)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/health")
    async def health(request: Request) -> dict:
        require_local(request)
        return {
            "ok": hub.latest_error is None,
            "status": hub.latest_status,
            "error": hub.latest_error,
            "clients": len(hub.clients),
            "language": engine.config.language,
        }

    @app.get("/devices")
    async def devices(request: Request) -> dict:
        require_local(request)
        return {"speakers": list_speakers()}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        host = websocket.client.host if websocket.client else ""
        if host not in {"127.0.0.1", "::1"}:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        await websocket.send_json(
            {"type": "hello", "protocol": 1, "pcm": {"sample_rate": 16000, "channels": 1}}
        )
        if hub.latest_status:
            await websocket.send_json(hub.latest_status)
        if hub.latest_error:
            await websocket.send_json(hub.latest_error)
        hub.clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(websocket)

    @app.get("/phone", response_class=HTMLResponse)
    async def phone_page() -> str:
        return (Path(__file__).parent / "web" / "phone.html").read_text(encoding="utf-8")

    @app.get("/api/phone/status")
    async def phone_status() -> dict:
        return {"enabled": phone_share.load_phone_config()["enabled"]}

    @app.websocket("/relay")
    async def relay_endpoint(
        websocket: WebSocket, role: str = "", sid: str = "", t: str = ""
    ) -> None:
        if role != "phone":
            await websocket.close(code=4403)
            return
        await phone_relay.handle(websocket, sid, t)

    @app.get("/api/phone/info")
    async def phone_info(request: Request) -> dict:
        require_local(request)
        phone_config = phone_share.load_phone_config()
        share_url = phone_share.build_share_url(config.port, phone_config)
        return {
            "enabled": phone_config["enabled"],
            "shareUrl": share_url,
            "lanIp": phone_share.lan_ipv4(),
            "port": config.port,
            "qr": phone_share.qr_svg_data_url(share_url),
            "listeningLan": config.host == "0.0.0.0",
        }

    @app.post("/api/phone/toggle")
    async def phone_toggle(request: Request, payload: dict) -> dict:
        require_local(request)
        saved = phone_share.set_enabled(bool(payload.get("enabled")))
        return {"ok": True, "enabled": saved["enabled"]}

    @app.post("/api/phone/regenerate")
    async def phone_regenerate(request: Request) -> dict:
        require_local(request)
        phone_share.regenerate_phone_config()
        return {"ok": True}

    @app.post("/api/phone/ai")
    async def phone_ai(request: Request, payload: dict) -> dict:
        require_local(request)
        text = str(payload.get("text", ""))
        if len(text) > 20000:
            text = text[:20000]
        session_recorder.add_ai(text, bool(payload.get("done")))
        phone_relay.schedule_json(
            {"type": "ai", "text": text, "done": bool(payload.get("done"))}
        )
        return {"ok": True}

    @app.post("/api/ai/prompt")
    async def save_ai_prompt(request: Request, payload: dict) -> dict:
        require_local(request)
        prompt = str(payload.get("prompt", ""))
        if len(prompt) > 20000:
            prompt = prompt[:20000]
        last_ai_prompt["prompt"] = prompt
        return {"ok": True}

    @app.get("/api/ai/prompt")
    async def get_ai_prompt(request: Request) -> dict:
        require_local(request)
        return {"prompt": last_ai_prompt["prompt"] or None}

    @app.get("/api/prompts/builtin")
    async def get_builtin_prompts(request: Request) -> dict:
        """系统内置提示词：字幕 AI 各模式模板 + 截图解题默认（设置页展示用）。"""
        require_local(request)
        return await asyncio.to_thread(builtin_prompts)

    @app.post("/api/ai/ask")
    async def ai_ask(request: Request, payload: dict) -> dict:
        """回答效果测试：用 C# 推送的真实系统提示词 + 用户问题走一次完整调用。"""
        require_local(request)
        question = str(payload.get("question", "")).strip()
        if not question:
            raise HTTPException(status_code=400, detail="请先输入测试问题")
        if len(question) > 4000:
            question = question[:4000]
        prompt = effective_system_prompt()
        settings = await asyncio.to_thread(load_settings)
        api_key = await asyncio.to_thread(load_api_key)
        if not api_key:
            raise HTTPException(status_code=400, detail="请先保存 AI 接口 API Key")
        try:
            result = await asyncio.to_thread(_ask_ai_blocking, prompt, question, settings, api_key)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

    @app.get("/api/records/stats")
    async def records_stats(request: Request) -> dict:
        require_local(request)
        return {"root": str(records_root), **session_recorder.stats()}

    @app.post("/api/records/save")
    async def records_save(request: Request) -> dict:
        require_local(request)
        if not session_recorder.stats()["has_content"]:
            raise HTTPException(status_code=400, detail="本场还没有可保存的记录")
        return await asyncio.to_thread(session_recorder.save_to, records_root)

    @app.post("/api/records/open")
    async def records_open(request: Request) -> dict:
        require_local(request)
        records_root.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(os.startfile, str(records_root))  # type: ignore[attr-defined]
        return {"ok": True}

    @app.post("/api/records/clear")
    async def records_clear(request: Request) -> dict:
        require_local(request)
        session_recorder.clear()
        return {"ok": True}

    @app.post("/api/phone/solve")
    async def phone_solve(request: Request) -> dict:
        require_local(request)
        triggered = await asyncio.to_thread(phone_relay.request_solve)
        if not triggered and phone_relay.solve_engine.busy:
            return {"ok": False, "detail": "上一个解题请求还在进行中"}
        return {"ok": True}

    @app.post("/api/vision/key")
    async def save_vision_key_endpoint(request: Request, payload: dict) -> dict:
        require_local(request)
        api_key = str(payload.get("apiKey", ""))
        await asyncio.to_thread(phone_share.save_vision_key, api_key)
        return {"ok": True, "apiKeySet": bool(phone_share.load_vision_key())}

    @app.get("/api/vision/status")
    async def vision_status(request: Request) -> dict:
        require_local(request)
        vision = phone_share.load_vision_config()
        return {
            "enabled": vision["enabled"],
            "baseUrlSet": bool(vision["baseUrl"]),
            "modelSet": bool(vision["model"]),
            "apiKeySet": bool(phone_share.load_vision_key()),
        }

    return app
