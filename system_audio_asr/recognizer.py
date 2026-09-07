from __future__ import annotations

import os
import queue
import re
import threading
import time
import traceback
from collections.abc import Callable

import numpy as np

from .capture import WasapiLoopbackCapture
from .config import AppConfig
from .segmenter import AudioPacket, SpeechSegmenter, merge_stream_text
from .settings import load_settings


_HOTWORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,30}")
_MAX_HOTWORDS = 80

# 内置基础热词：C++ / Qt 技术栈（面试高频，简历为空时兜底）
_BASE_HOTWORDS: list[str] = [
    # C++ 核心
    "C++", "C++11", "C++14", "C++17", "C++20", "C++23",
    "STL", "template", "RAII", "smart pointer", "unique_ptr", "shared_ptr",
    "move semantics", "rvalue", "lambda", "constexpr", "decltype",
    "virtual", "vtable", "RTTI", "multiple inheritance", "diamond problem",
    "memory alignment", "cache line", "false sharing", "memory order",
    "atomic", "mutex", "condition variable", "thread pool", "coroutine",
    # Qt 核心
    "Qt", "Qt5", "Qt6", "QWidget", "QML", "QObject", "signal", "slot",
    "moc", "meta object", "property", "event loop", "QThread",
    "QTimer", "QMutex", "QWaitCondition", "QSemaphore",
    "QNetworkAccessManager", "QHttp", "QWebSocket",
    "QSqlDatabase", "QSqlQuery", "QTableView", "QStandardItemModel",
    "QOpenGLWidget", "QGraphicsView", "QQuickItem",
    "qmake", "CMake", "qrc", "ui file",
    # 常见搭配
    "signal slot mechanism", "event driven", "cross platform",
    "desktop application", "embedded", "real time",
]


def _extract_hotwords(settings: dict) -> list[str]:
    """从简历/JD/公司/附加背景里提取英文技术词作为热词表。"""
    parts = [
        str(settings.get("resumeContext") or ""),
        str(settings.get("jdContext") or ""),
        str(settings.get("targetCompany") or ""),
        str(settings.get("extraContext") or ""),
        str(settings.get("hotwordExtra") or ""),
    ]
    seen: set[str] = set()
    hotwords: list[str] = []
    for text in parts:
        for match in _HOTWORD_RE.findall(text):
            word = match.strip()
            if word and word not in seen:
                seen.add(word)
                hotwords.append(word)
                if len(hotwords) >= _MAX_HOTWORDS:
                    return hotwords
    return hotwords


def _get_hotwords(settings: dict) -> list[str]:
    """三层叠加热词：内置基础 + 简历提取 + 手动补充，去重后返回。"""
    # ① 内置基础热词（兜底）
    result: list[str] = list(_BASE_HOTWORDS)
    seen: set[str] = {w.lower() for w in result}

    # ② 简历自动提取（精准）
    for word in _extract_hotwords(settings):
        if word.lower() not in seen:
            result.append(word)
            seen.add(word.lower())

    return result


def choose_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


class TranscriptionEngine:
    def __init__(self, config: AppConfig, publish: Callable[[dict], None]) -> None:
        self.config = config
        self.publish = publish
        self._stop = threading.Event()
        self._packets: queue.Queue[AudioPacket] = queue.Queue(maxsize=64)
        self._worker: threading.Thread | None = None
        self._capture_thread: threading.Thread | None = None
        self._capture: WasapiLoopbackCapture | None = None
        self._segment_id = 0
        self._last_level = 0.0

    def start(self) -> None:
        self._worker = threading.Thread(target=self._run, name="paraformer-worker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._capture:
            self._capture.stop()
        if self._capture_thread:
            self._capture_thread.join(timeout=2)
        if self._worker:
            self._worker.join(timeout=5)

    def _run(self) -> None:
        try:
            if self.config.language == "en":
                self._run_english()
                return
            os.environ.setdefault("MODELSCOPE_DOWNLOAD_PARALLEL_WORKERS", "1")
            os.environ.setdefault("MODELSCOPE_DOWNLOAD_PART_SIZE_MB", "64")
            if self.config.hub == "hf":
                os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
                os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            from funasr import AutoModel

            device = choose_device(self.config.device)
            self.publish(
                {"type": "status", "state": "loading_model", "model": self.config.model, "device": device}
            )
            model_options = {"model": self.config.model, "device": device}
            if self.config.hub:
                model_options["hub"] = self.config.hub
            model = AutoModel(**model_options)
            self.publish({"type": "status", "state": "model_ready", "device": device})

            segmenter = SpeechSegmenter(
                self.config.target_rate,
                self.config.model_stride_samples,
                self.config.silence_db,
                self.config.endpoint_silence_ms,
                self.config.preroll_ms,
            )
            self._capture = WasapiLoopbackCapture(
                self.config.speaker,
                self.config.capture_rate,
                self.config.target_rate,
                self.config.capture_block_ms,
                lambda audio: self._on_audio(segmenter, audio),
                self.publish,
                self._on_capture_error,
            )
            self._capture_thread = threading.Thread(
                target=self._capture.run, name="wasapi-loopback", daemon=True
            )
            self._capture_thread.start()

            cache: dict = {}
            utterance = ""
            hotwords: list[str] = []
            hotword_failed = False
            last_hotword_refresh = 0.0
            while not self._stop.is_set():
                try:
                    packet = self._packets.get(timeout=0.2)
                except queue.Empty:
                    if self._capture_thread and not self._capture_thread.is_alive():
                        break
                    continue

                now = time.monotonic()
                if now - last_hotword_refresh > 15.0:
                    last_hotword_refresh = now
                    try:
                        settings = load_settings()
                        if settings.get("hotwordEnabled", True):
                            hotwords = _get_hotwords(settings)
                        else:
                            hotwords = []
                    except Exception:
                        pass

                generate_kwargs: dict = dict(
                    input=packet.samples,
                    cache=cache,
                    is_final=packet.is_final,
                    chunk_size=list(self.config.chunk_size),
                    encoder_chunk_look_back=self.config.encoder_look_back,
                    decoder_chunk_look_back=self.config.decoder_look_back,
                )
                if hotwords and not hotword_failed:
                    generate_kwargs["hotword"] = " ".join(hotwords)

                try:
                    result = model.generate(**generate_kwargs)
                except TypeError:
                    # 模型不支持 hotword 参数时自动降级，后续不再尝试
                    if hotwords and not hotword_failed:
                        hotword_failed = True
                        hotwords = []
                        self.publish({"type": "status", "state": "hotword_disabled"})
                    result = model.generate(
                        input=packet.samples,
                        cache=cache,
                        is_final=packet.is_final,
                        chunk_size=list(self.config.chunk_size),
                        encoder_chunk_look_back=self.config.encoder_look_back,
                        decoder_chunk_look_back=self.config.decoder_look_back,
                    )
                except Exception as exc:
                    self.publish({"type": "error", "where": "recognizer", "message": f"generate 失败: {exc}"})
                    continue

                incoming = "".join(
                    str(item.get("text", "")) for item in (result or []) if isinstance(item, dict)
                )
                utterance = merge_stream_text(utterance, incoming)
                if utterance:
                    self.publish(
                        {
                            "type": "final" if packet.is_final else "partial",
                            "segment_id": self._segment_id,
                            "text": utterance,
                        }
                    )
                if packet.is_final:
                    cache = {}
                    utterance = ""
                    self._segment_id += 1
        except BaseException as exc:
            traceback.print_exc()
            self.publish(
                {"type": "error", "where": "recognizer", "message": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            if self._capture:
                self._capture.stop()
            self.publish({"type": "status", "state": "stopped"})

    def _run_english(self) -> None:
        os.environ.setdefault("MODELSCOPE_DOWNLOAD_PARALLEL_WORKERS", "4")
        from faster_whisper import WhisperModel
        from modelscope import snapshot_download

        requested = choose_device(self.config.device)
        device = "cuda" if requested.startswith("cuda") else "cpu"
        device_index = int(requested.split(":", 1)[1]) if ":" in requested else 0
        compute_type = "float16" if device == "cuda" else "int8"
        model_name = "pengzhendong/faster-whisper-tiny.en"
        self.publish(
            {
                "type": "status",
                "state": "loading_model",
                "model": model_name,
                "device": requested,
                "language": "en",
            }
        )
        configured_dir = os.environ.get("VOXRIBBON_ENGLISH_MODEL_DIR", "").strip()
        app_root = os.environ.get(
            "LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local")
        )
        persistent_dir = os.path.join(
            app_root, "VoxRibbon", "models", "faster-whisper-tiny.en"
        )
        if configured_dir and os.path.isfile(os.path.join(configured_dir, "model.bin")):
            model_dir = configured_dir
        elif os.path.isfile(os.path.join(persistent_dir, "model.bin")):
            model_dir = persistent_dir
        else:
            model_dir = snapshot_download(model_name, local_dir=persistent_dir)
        model = WhisperModel(
            model_dir,
            device=device,
            device_index=device_index,
            compute_type=compute_type,
        )
        self.publish(
            {"type": "status", "state": "model_ready", "device": requested, "language": "en"}
        )

        segmenter = SpeechSegmenter(
            self.config.target_rate,
            self.config.model_stride_samples,
            self.config.silence_db,
            self.config.endpoint_silence_ms,
            self.config.preroll_ms,
        )
        self._capture = WasapiLoopbackCapture(
            self.config.speaker,
            self.config.capture_rate,
            self.config.target_rate,
            self.config.capture_block_ms,
            lambda audio: self._on_audio(segmenter, audio),
            self.publish,
            self._on_capture_error,
        )
        self._capture_thread = threading.Thread(
            target=self._capture.run, name="wasapi-loopback", daemon=True
        )
        self._capture_thread.start()

        utterance = np.empty(0, dtype=np.float32)
        last_text = ""
        while not self._stop.is_set():
            try:
                packet = self._packets.get(timeout=0.2)
            except queue.Empty:
                if self._capture_thread and not self._capture_thread.is_alive():
                    break
                continue
            utterance = np.concatenate((utterance, packet.samples))
            # Whisper accepts at most 30 seconds. Keep the newest context for unusually long turns.
            if utterance.size > self.config.target_rate * 30:
                utterance = utterance[-self.config.target_rate * 30 :]
            if utterance.size < self.config.target_rate and not packet.is_final:
                continue
            segments, _info = model.transcribe(
                utterance,
                language="en",
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
            if text and (text != last_text or packet.is_final):
                self.publish(
                    {
                        "type": "final" if packet.is_final else "partial",
                        "segment_id": self._segment_id,
                        "text": text,
                        "language": "en",
                    }
                )
                last_text = text
            if packet.is_final:
                utterance = np.empty(0, dtype=np.float32)
                last_text = ""
                self._segment_id += 1

    def _on_audio(self, segmenter: SpeechSegmenter, audio: np.ndarray) -> None:
        level, packets = segmenter.feed(audio)
        now = time.monotonic()
        if now - self._last_level >= 0.1:
            self._last_level = now
            self.publish({"type": "audio_level", "dbfs": round(level, 1), "active": segmenter.active})
        for packet in packets:
            try:
                self._packets.put(packet, timeout=0.5)
            except queue.Full:
                self.publish(
                    {"type": "error", "where": "audio_queue", "message": "识别跟不上音频，已丢弃一块音频"}
                )

    def _on_capture_error(self, exc: BaseException) -> None:
        self.publish({"type": "error", "where": "wasapi", "message": f"{type(exc).__name__}: {exc}"})
