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


# 英文技术词：字母开头，允许 C++/C#/.NET/Node.js 这类符号
_EN_HOTWORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,30}")
# 中文热词：连续 2-12 个汉字（项目代号、岗位术语、公司名等）
_ZH_HOTWORD_RE = re.compile(r"[\u4e00-\u9fa5]{2,12}")
# 热词表总上限：Paraformer 把热词拼成一整串送入，过长反而拉低识别率
_MAX_HOTWORDS = 120
_MAX_ZH_HOTWORDS = 40

# 仅用于「简历/手动词全都为空」时的兜底，保持精简通用；
# 方向性强的大词表交给用户简历自动提取，避免干扰非技术岗识别。
_BASE_HOTWORDS: list[str] = [
    "Redis", "Kafka", "MySQL", "Docker", "Kubernetes", "Linux",
    "CTF", "QPS", "TPS", "SLA", "CI", "CD", "API", "SDK",
    "C++", "Java", "Python", "Golang", "Rust", "SQL",
    "STL", "RAII", "lambda", "atomic", "mutex", "thread pool",
    "微服务", "分布式", "高并发", "负载均衡", "消息队列",
]

# 提取热词时的文本来源（简历/JD/公司/附加背景）
_CONTEXT_KEYS = ("resumeContext", "jdContext", "targetCompany", "extraContext")

# 简历/JD 里高频出现但不构成「技术词」的中文词：进热词表只会干扰识别
_ZH_STOPWORDS: frozenset[str] = frozenset({
    "熟悉", "熟练", "精通", "了解", "掌握", "负责", "参与", "主导", "完成", "实现",
    "开发", "设计", "优化", "维护", "搭建", "支持", "使用", "基于", "具备", "拥有",
    "良好", "优秀", "丰富", "相关", "经验", "能力", "团队", "沟通", "协作", "学习",
    "本科", "硕士", "博士", "毕业", "专业", "大学", "公司", "岗位", "职位", "工作",
    "项目", "需求", "业务", "系统", "平台", "功能", "模块", "接口", "数据", "服务",
    "以及", "并且", "能够", "可以", "需要", "要求", "以下", "以上", "负责相关工作",
    "任职", "职责", "加分", "优先", "者优先", "年以上", "及其", "等等", "其他",
})


def _is_meaningful_zh(word: str) -> bool:
    """过滤无意义中文词：停用词、纯数字、以及短于 2 字的片段。"""
    if len(word) < 2 or word in _ZH_STOPWORDS:
        return False
    if word.isdigit():
        return False
    return True


# 简历/JD 里的动词与限定词：常作为「负责……」「熟悉……」的前后缀出现，
# 需从抽取结果中剥离，否则会得到「负责高并发系统优化」这种带噪声的候选词。
_ZH_AFFIXES: tuple[str, ...] = (
    "负责", "主导", "参与", "熟悉", "掌握", "精通", "了解", "完成",
    "使用", "基于", "具备", "拥有", "熟练", "擅长", "从事", "负责人",
    "工作", "系统", "平台", "项目", "优化", "设计", "开发", "维护",
    "搭建", "支持", "实现", "能力", "经验", "相关", "以及", "等",
)


def _strip_zh_affixes(word: str) -> str:
    """反复剥离前后缀动词/限定词，得到更像术语的中文片段。

    例：「负责高并发系统优化」→ 「高并发」；「掌握 ClickHouse 等」→ 剥离后为空则丢弃。
    """
    current = word
    changed = True
    while changed and current:
        changed = False
        for affix in _ZH_AFFIXES:
            if current.startswith(affix) and len(current) > len(affix):
                current = current[len(affix):]
                changed = True
            if current.endswith(affix) and len(current) > len(affix):
                current = current[: -len(affix)]
                changed = True
    return current


def _extract_zh_hotwords(text: str) -> list[str]:
    """从中文文本中提取候选术语：按连续汉字跑切分，再剥离动词前后缀。"""
    results: list[str] = []
    for run in _ZH_HOTWORD_RE.findall(text):
        stripped = _strip_zh_affixes(run)
        if _is_meaningful_zh(stripped):
            results.append(stripped)
    return results

# 单个热词长度上限：仅作防御，防止用户把整篇简历误粘进热词框。
# 注意：这不是「按字数切分」——超长条目直接丢弃，绝不切碎。
_MAX_HOTWORD_CHARS = 32

# 手动补充热词的分隔符：换行、中英文逗号、顿号。
# 与参考实现（商业版仅用 , ， \n）保持一致；额外支持顿号，因为中文用户习惯用「、」列举。
# 不引入分号/竖线/制表符：规则越少越不容易误解。
_HOTWORD_SPLIT_RE = re.compile(r"[\r\n,，、]+")


def _split_manual_hotwords(text: str) -> list[str]:
    """切分手动补充热词：只按用户显式写下的分隔符切，不做任何语义猜测。

    中文没有天然词边界，任何「按字数切分」都会把「高并发缓存穿透」这类完整术语
    切坏，并把碎片混进热词表污染识别结果。因此严格遵守：
    用户写了分隔符才切，中文词组完整保留。

    超长条目（> _MAX_HOTWORD_CHARS）视为误粘贴，直接丢弃而非切分。
    """
    if not text.strip():
        return []
    parts: list[str] = []
    for chunk in _HOTWORD_SPLIT_RE.split(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        # 中文片段完整保留；英文/数字片段按空格再分（英文本身有空格，语义明确）
        if re.search(r"[\u4e00-\u9fa5]", chunk):
            parts.append(chunk)
        else:
            parts.extend(piece for piece in chunk.split() if piece)
    # 防御：超长条目丢弃（绝不切碎）
    return [item for item in parts if item and len(item) <= _MAX_HOTWORD_CHARS]

def _extract_hotwords(settings: dict) -> list[str]:
    """从简历/JD/公司/附加背景中提取中英文技术词作为热词表。"""
    seen: set[str] = set()
    hotwords: list[str] = []
    zh_count = 0
    for key in _CONTEXT_KEYS:
        text = str(settings.get(key) or "")
        if not text:
            continue
        for match in _EN_HOTWORD_RE.findall(text):
            word = match.strip()
            lowered = word.lower()
            if word and lowered not in seen and len(hotwords) < _MAX_HOTWORDS:
                seen.add(lowered)
                hotwords.append(word)
        for word in _extract_zh_hotwords(text):
            if word not in seen and zh_count < _MAX_ZH_HOTWORDS:
                seen.add(word)
                hotwords.append(word)
                zh_count += 1
    return hotwords


def _get_hotwords(settings: dict) -> list[str]:
    """三层叠加热词：手动补充（最精准，优先） + 简历提取 + 内置兜底，去重后截断。"""
    result: list[str] = []
    seen: set[str] = set()
    zh_total = 0

    def push(word: str) -> bool:
        """返回是否成功加入；中文受 _MAX_ZH_HOTWORDS 单独约束。"""
        nonlocal zh_total
        word = word.strip()
        if not word:
            return False
        is_zh = bool(re.search(r"[\u4e00-\u9fa5]", word))
        key = word if is_zh else word.lower()
        if key in seen:
            return False
        if len(result) >= _MAX_HOTWORDS:
            return False
        if is_zh:
            if zh_total >= _MAX_ZH_HOTWORDS:
                return False
            zh_total += 1
        seen.add(key)
        result.append(word)
        return True

    # ① 手动补充：用户显式指定，优先级最高，中英文都支持
    for word in _split_manual_hotwords(str(settings.get("hotwordExtra") or "")):
        push(word)

    # ② 简历/JD 自动提取
    for word in _extract_hotwords(settings):
        push(word)

    # ③ 内置兜底：仅当上面两层都没有产出时才使用，避免方向词干扰
    if not result:
        result.extend(_BASE_HOTWORDS)

    return result[: _MAX_HOTWORDS]

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
        self._paused = threading.Event()

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
        if self._gate_paused():
            return
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

    # ---------------------------------------------------------------- 暂停采集
    # 用 Event 门控而非停线程：暂停瞬间生效、恢复无重启开销（模型常驻内存），
    # 也避开 SoundCard/COM 在动态重建采集线程上的初始化坑。
    def pause(self) -> dict:
        self._paused.set()
        self.publish({"type": "status", "state": "paused"})
        return {"paused": True}

    def resume(self) -> dict:
        self._paused.clear()
        self.publish({"type": "status", "state": "capturing"})
        return {"paused": False}

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def _gate_paused(self) -> bool:
        """暂停门控：暂停期间丢弃音频块并广播静止电平，保证不采集任何声音。"""
        if not self._paused.is_set():
            return False
        now = time.monotonic()
        if now - self._last_level >= 0.1:
            self._last_level = now
            self.publish({"type": "audio_level", "dbfs": -120.0, "active": False})
        return True
