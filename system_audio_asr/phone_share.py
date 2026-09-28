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
# 一次解题最多挂几张截图。题干/约束/样例跨屏时用得上，但每张（1600px、q75）
# base64 后约 183 KB，全都会进模型输入，因此默认 3 张、上限 5 张。
DEFAULT_MAX_SOLVE_IMAGES = 3
MAX_SOLVE_IMAGES_LIMIT = 5
# 自动提交问题的可选间隔（秒）。手机上以档位下拉呈现，服务端用同一张表校验，
# 避免手机端传任意值（如 0.1 秒）把模型额度瞬间烧光。
AUTO_INTERVAL_LEVELS = (3, 5, 10, 15, 30, 60)
DEFAULT_AUTO_INTERVAL_SECONDS = 5
# 自动提交连续失败多少次后自停。模型未启用 / 网关不通这类问题不会自愈，
# 一直重试只会每几秒推一条同样的错误：聊天气泡上限 50 条，按 3 秒间隔算
# 约 2.5 分钟就把上限填满，把之前真正的解题回答与追问全挤掉。
AUTO_FAILURE_LIMIT = 3
TRANSCRIPT_EVENT_TYPES = {"partial", "final", "status"}
MAX_CLIPBOARD_CHARS = 50000
CLIPBOARD_POLL_SECONDS = 0.2
# 剪贴板被其他进程短暂占用（OpenClipboard 失败）时的重试节奏：
# 轮询间隔 25ms、最多 8 次，覆盖住浏览器/输入法锁定剪贴板的瞬时窗口。
CLIPBOARD_WRITE_ATTEMPTS = 8
CLIPBOARD_WRITE_RETRY_SECONDS = 0.025
# config.json 读取重试：C# Overlay 的 Save()（tmp + File.Replace）与网页设置页的
# 保存随时可能发生，手机在这两个进程之外读同一份文件。实测 Python 的 os.replace
# 与 .NET 的 File.ReadAllText 并发时，读失败 8 秒内出现 9 次（见
# settings.load_settings 的同款重试与 tests/test_config_resilience.py），
# 因此单次读失败不代表文件损坏，只有重试后仍失败才当「读不到」。
CONFIG_READ_ATTEMPTS = 4
CONFIG_READ_RETRY_SECONDS = 0.04

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

VISION_KEY_PATH = APP_DIR / "vision.key"
VISION_ENTROPY = b"WasapiParaformerOverlay.Vision.v1"
# 解题作答模式：core_code = 只给核心实现（历史默认）；acm = 完整可编译程序。
# 两者共用同一前缀与后缀，只有中间的「作答要求」块不同——这样 core_code
# 拼出来与历史上的 SOLVE_PROMPT 逐字一致（行为零变化）。
ANSWER_MODE_CORE_CODE = "core_code"
ANSWER_MODE_ACM = "acm"
ANSWER_MODES = (ANSWER_MODE_CORE_CODE, ANSWER_MODE_ACM)
DEFAULT_ANSWER_MODE = ANSWER_MODE_CORE_CODE

_SOLVE_PROMPT_PREFIX = "请识别图中的题目或问题，直接给出简洁的答案与关键步骤。"
# 代码必须用 ``` 围栏包裹：手机气泡与桌面字幕窗会据此渲染成独立的等宽代码块
# （此前禁止一切 Markdown，代码与正文混在一起、缩进丢失，抄代码容易漏行）。
# 加粗/标题仍然禁止——它们在两种渲染里都没有对应样式，只会留下多余符号。
_SOLVE_PROMPT_SUFFIX = (
    "不要复述题目，不要输出多余客套话。"
    "代码必须用 ``` 代码块围栏包裹（标明语言），正文不要使用其他 Markdown 标记"
    "（如 **加粗**、# 标题）。"
)

# core_code：只给核心代码实现（默认）。
_SOLVE_PROMPT_CORE_CODE_BODY = "如果是代码题给出核心代码；如果是选择题先给选项字母再解释。"

# acm：完整可编译程序——头文件、main、输入输出、样例走查。
# 语言默认 C++：解题链路不带简历（题干在截图里已完整），模型无从得知候选人技术栈，
# 不写明会随机给 Python，而笔试/ACM 场景通常要能直接提交的完整程序。
_SOLVE_PROMPT_ACM_BODY = (
    "如果是代码题，请给出完整可编译运行的程序：包含必要的头文件、完整的输入读取与"
    "结果输出，能直接提交到在线评测。默认使用 C++（含 #include、main 函数、cin/cout "
    "读写）；若题目明确要求其他语言则遵循题目。先用一两句话说明算法思路与复杂度，"
    "再给完整代码，最后用一个样例走查验证。"
    "如果是选择题先给选项字母再解释。"
)


def build_solve_prompt(mode: Any = DEFAULT_ANSWER_MODE) -> str:
    """按作答模式拼装内置解题提示词（用户自定义 solvePrompt 优先级更高）。"""
    body = _SOLVE_PROMPT_ACM_BODY if normalize_answer_mode(mode) == ANSWER_MODE_ACM else _SOLVE_PROMPT_CORE_CODE_BODY
    return _SOLVE_PROMPT_PREFIX + body + _SOLVE_PROMPT_SUFFIX


# 找 Bug 任务：与「解题」是两件事（一个给答案、一个查错），因此不走作答模式，
# 而是一个独立的内置提示词。允许代码围栏，便于两端渲染修复后的代码。
BUG_PROMPT = (
    "请检查图中代码或报错信息的问题。"
    "先用一两句话指出问题出在哪里、为什么错（引用关键的变量名或行）；"
    "如果有多个问题，按严重程度依次列出。"
    "然后给出修复后的代码，代码必须用 ``` 代码块围栏包裹（标明语言）。"
    "正文不要使用其他 Markdown 标记（如 **加粗**、# 标题）。"
    "不要复述代码全文，不要输出多余客套话。"
)

# 解题任务类型：solve = 解出题目（走作答模式）/ bug = 找 Bug（走 BUG_PROMPT）
SOLVE_TASK_SOLVE = "solve"
SOLVE_TASK_BUG = "bug"
SOLVE_TASKS = (SOLVE_TASK_SOLVE, SOLVE_TASK_BUG)


def normalize_solve_task(value: Any) -> str:
    """把任意输入规范到受支持的任务类型；无法识别时回退解题（行为零变化）。"""
    task = str(value or "").strip().lower()
    return task if task in SOLVE_TASKS else SOLVE_TASK_SOLVE


# 解题失败的统一前缀：失败消息都以此为开头，自动循环据此识别本轮结果。
# 用常量而不是各处写字面量：前缀一改，自动提交的降噪/自停会静默失效。
_SOLVE_FAILURE_PREFIX = "解题失败："


def build_solve_prompt_for_task(mode: Any, task: Any = SOLVE_TASK_SOLVE) -> str:
    """按任务类型给出内置提示词：找 Bug 用专用提示词，解题用作答模式。"""
    if normalize_solve_task(task) == SOLVE_TASK_BUG:
        return BUG_PROMPT
    return build_solve_prompt(mode)


def normalize_answer_mode(value: Any) -> str:
    """把任意输入规范到受支持的作答模式；无法识别时回退默认（行为零变化）。"""
    mode = str(value or "").strip().lower()
    return mode if mode in ANSWER_MODES else DEFAULT_ANSWER_MODE


# 默认（core_code）内置提示词：与历史文本逐字一致。保留该名字，
# 设置页「解题提示词」的默认值展示与历史引用都指向它。
SOLVE_PROMPT = build_solve_prompt(DEFAULT_ANSWER_MODE)
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


def _set_clipboard_text_once(text: str) -> bool:
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


def set_clipboard_text(text: str) -> bool:
    """写入剪贴板；被其他进程短暂占用时重试。

    OpenClipboard 是独占的：浏览器、输入法等会随机短暂持有剪贴板，实测约 10%
    的写入会立刻失败。这里按 25ms 间隔重试若干次，把它压到可忽略。
    """
    for attempt in range(CLIPBOARD_WRITE_ATTEMPTS):
        try:
            if _set_clipboard_text_once(text):
                return True
        except Exception:
            pass
        if attempt < CLIPBOARD_WRITE_ATTEMPTS - 1:
            time.sleep(CLIPBOARD_WRITE_RETRY_SECONDS)
    return False


def load_phone_config(path: Path | None = None) -> dict[str, Any]:
    target = path or PHONE_CONFIG_PATH
    with _store_lock:
        try:
            raw = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raw = {}
    # 文件可能被外部写坏成数组/字符串/null；非 dict 一律按空配置处理，
    # 否则 raw.get 抛 AttributeError，会让 /api/phone/* 返回 500、
    # 且 /relay 握手异常关闭（手机扫码后一直"未连接"且无明确报错）。
    if not isinstance(raw, dict):
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
    except Exception:
        # 读取失败（文件缺失/损坏/DPAPI 解密异常）一律视为未配置。
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
    """读取解题链路的配置（只读，不写盘，读不出来时一律走默认值）。

    走 _read_config_raw 而不是自己读：单次读失败（撞上 C# Overlay 或网页设置页
    的「tmp + 原子替换」窗口）会让这几个档位静默回默认值，而它喂给手机配对、
    hello 帧与解题链路 —— 表现是手机重连后模式显示错，用户看不出原因。
    这里只读不写，所以「读不出来」按空配置处理是安全的（与写路径的取舍相反）。
    """
    raw = _read_config_raw()
    if raw is None:
        raw = {}
    from .ai_stream import THINKING_MODES, normalize_max_tokens, normalize_thinking_mode

    # 思考模式："" = 跟随字幕 AI；其余取值（off/auto/medium/high）在解题独立生效。
    # 解题是独立链路（算法题/笔试题为主），与实时字幕的取舍不同，
    # 故留独立开关而不是硬绑 aiThinkingMode。
    # 白名单必须用共享常量而不是字面量集合：写死 {"off","auto"} 会让新增的
    # medium/high 静默退化成「跟随字幕 AI」，界面上看不出任何异常。
    thinking = str(raw.get("visionThinkingMode") or "").strip().lower()
    if thinking not in THINKING_MODES:
        thinking = normalize_thinking_mode(raw.get("aiThinkingMode"))

    return {
        "enabled": bool(raw.get("visionEnabled", False)),
        "baseUrl": str(raw.get("visionBaseUrl", "")).rstrip("/"),
        "model": str(raw.get("visionModel", "")),
        # 简历/JD/知识库不在此处返回：解题不需要面试上下文（题干在截图里完整）。
        # 作答模式决定内置提示词（core_code 只给核心实现 / acm 给完整可编译程序）；
        # 用户自定义 solvePrompt 优先级最高，一旦填写就与模式无关。
        "answerMode": normalize_answer_mode(raw.get("visionAnswerMode")),
        "prompt": (
            str(raw.get("solvePrompt", "")).strip()
            or build_solve_prompt(raw.get("visionAnswerMode"))
        ),
        "thinkingMode": thinking,
        # 回答长度独立档位：算法题核心代码较长，被截断就没法抄。
        "maxTokens": normalize_max_tokens(raw.get("visionMaxTokens")),
        # 一次解题最多挂几张截图（题干跨屏时用；越多 token 越多）。
        "maxImages": normalize_max_solve_images(raw.get("visionMaxImages")),
    }


def normalize_max_solve_images(value: Any) -> int:
    """把一次解题的截图张数规范到受支持范围：1..5，非法值回退默认 3。

    必须捕获 OverflowError：JSON 里的 1e400 / Infinity 解析成 inf 后 int() 会抛，
    而它不是 ValueError —— 漏掉会让 load_vision_config 失败，进而影响手机配对、
    hello 帧与解题链路（与 settings.normalize_record_image_cap 同一坑）。
    """
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MAX_SOLVE_IMAGES
    return max(1, min(MAX_SOLVE_IMAGES_LIMIT, number))


def normalize_auto_interval_ms(value: Any) -> float:
    """把任意输入规范到受支持的间隔档位，返回**毫秒**。

    单位陷阱（2026-09-23 实测）：这里返回的是毫秒，而 asyncio.sleep() 收的是
    秒，调用处必须 ÷1000 —— 曾经直接把返回值喂给 asyncio.sleep，于是「每 5 秒」
    实际睡 5000 秒（83 分钟），勾选后只在第一轮立即截屏一次，之后再无动静。
    状态字段 `_auto_interval_ms` 与 hello 帧都按毫秒走，所以换算只应发生在
    sleep 调用处，不要改成返回秒。

    非法值与越界值都吸附到最近档位：自动提交会真实消耗模型额度，
    不能让手机端传极小值（如 0.1 秒）把额度瞬间烧光。
    """
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        seconds = float(DEFAULT_AUTO_INTERVAL_SECONDS)
    snapped = min(AUTO_INTERVAL_LEVELS, key=lambda level: (abs(level - seconds), level))
    return snapped * 1000.0


def _read_config_raw() -> dict[str, Any] | None:
    """读取整份 config.json；返回 None = 「读不出来」，与「空配置」是两回事。

    三种结果对应三种不同的事实，调用方必须分开对待：
      · {}   文件不存在（全新安装）—— 确实没有配置可保留，允许写盘；
      · dict 读到了内容；
      · None 文件存在但读不出来（被独占占用 / 半个文件 / 顶层不是对象）——
             磁盘上有什么我们一无所知，此时任何写盘都可能抹掉用户资料。

    重试的理由见 CONFIG_READ_ATTEMPTS：单次失败多半只是撞上了 C# Overlay 或
    网页设置页「tmp + 原子替换」的窗口（实测 8 秒内 9 次），重试三次基本都能读到。
    最坏情况阻塞约 120ms，且只在文件真的读不出来时发生。

    注意不要用 CONFIG_PATH.exists() 先判断：Path.exists() 内部吞掉 OSError
    返回 False，文件被独占锁定时会得到「不存在」这个错误结论，于是又回到
    「按空配置处理」的老路。这里以 read_text 的 FileNotFoundError 为准。
    """
    for attempt in range(CONFIG_READ_ATTEMPTS):
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            # 必须在 OSError 之前捕获：它正是「文件不存在」这一确定的结论。
            return {}
        except (OSError, ValueError):
            if attempt < CONFIG_READ_ATTEMPTS - 1:
                time.sleep(CONFIG_READ_RETRY_SECONDS)
            continue
        # 顶层不是对象（被别的程序写成了数组/字符串）：内容已不是配置，
        # 读路径按空处理，写路径则必须拒绝 —— 见上面的第 3 种情况。
        return raw if isinstance(raw, dict) else None
    return None


def _read_config_value(key: str) -> Any:
    """只读 config.json 的单个键；文件缺失/损坏/读不出时返回 None（不抛异常）。

    读路径把「读不出来」也当空处理：这里只需要各字段的默认值，不写盘所以
    没有丢数据的风险；区分读写两种语义的是 _read_config_raw。
    """
    raw = _read_config_raw()
    return raw.get(key) if isinstance(raw, dict) else None


def _write_single_config_key(key: str, normalized: Any) -> Any:
    """只改写 config.json 的一个键（手机端切换类操作的唯一写入口）。

    手机在局域网，调不到 /api/settings（require_local 只放行回环地址），所以
    切换必须走 relay。为避免手机端越权改配置，这里刻意只接受键名与已白名单
    规范化的值，其余字段一律不碰、原样保留。

    返回 None = 写入失败（读不出原文件或写盘失败），调用方**必须**显式处理：
    这是读-改-写，读不到原文件时若照写，整份 config.json 会被覆写成
    「只剩这一个键」的单键文件（2026-09-25 实测：杀软瞬时锁定 config.json 时，
    切一次开关就把 resumeContext/jdContext/targetCompany 等 44 个键全部清空）。

    写失败（磁盘满 / 文件被锁）同样返回 None 而不是抛异常：用户资料此时完好，
    失败必须能被调用方如实告知手机，而不是变成一次静默的空操作。
    """
    raw = _read_config_raw()
    if raw is None:
        return None
    raw[key] = normalized
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, CONFIG_PATH)
    except OSError:
        return None
    return normalized


def set_vision_answer_mode(mode: Any) -> str | None:
    """手机端切换作答模式：**只写 visionAnswerMode 这一个键**。

    返回 None = 写入失败（原因见 _write_single_config_key），调用方需提示手机。
    """
    return _write_single_config_key("visionAnswerMode", normalize_answer_mode(mode))


def normalize_vision_thinking_mode(value: Any) -> str:
    """解题思考模式的独立取值："" = 跟随字幕 AI，或 off/auto/medium/high。

    与字幕 AI 的区别是多了 "" 这一档（跟随）。非法值回退 ""（跟随），
    且白名单来自 ai_stream.THINKING_MODES，新增档位时无需在这里改字面量。
    """
    from .ai_stream import THINKING_MODES

    mode = str(value or "").strip().lower()
    return mode if mode in THINKING_MODES else ""


def set_vision_thinking_mode(mode: Any) -> str | None:
    """手机端切换解题思考模式：**只写 visionThinkingMode 这一个键**。

    返回 None = 写入失败（原因见 _write_single_config_key），调用方需提示手机。
    """
    return _write_single_config_key(
        "visionThinkingMode", normalize_vision_thinking_mode(mode)
    )


def load_auto_submit() -> bool:
    """字幕 AI 是否静音后自动提交（config.json 的 aiAutoSubmit）。

    默认 True = 历史行为。读不到时也回 True，避免配置损坏导致「突然不自动问了」
    而用户不知道原因。
    """
    value = _read_config_value("aiAutoSubmit")
    return True if value is None else bool(value)


def set_auto_submit(on: Any) -> bool | None:
    """手机端切换「自动提交」：只写 aiAutoSubmit 这一个键。

    桌面端每秒重载一次 config（configTimer → ReloadConfigIfChanged），
    因此手机改完约 1 秒内生效，无需重启。

    返回 None = 写入失败，**不是 False**。这里不能写 bool(...)：bool(None) 是
    False，调用方会把一次写盘失败当成「已成功切到手动」广播给手机与桌面，
    而磁盘上什么都没变 —— 界面显示的状态与真实行为相反。
    """
    result = _write_single_config_key("aiAutoSubmit", bool(on))
    return None if result is None else bool(result)


class SolveEngine:
    """OpenAI 兼容视觉模型截图解题：流式 SSE，delta 经节流回调推送。"""

    def __init__(self) -> None:
        self._busy = threading.Lock()
        self.on_delta: Any = None  # callable(text, done: bool)

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def solve(self, images: bytes | list[bytes], task: Any = SOLVE_TASK_SOLVE) -> None:
        """后台线程执行；结果与错误都通过 on_delta 回调（text/done）通知。

        支持一次提交多张截图：算法题的题干、约束、样例常分散在多屏，
        单张截图会漏掉条件，导致答案按错误的题意给出。
        为兼容既有调用（桌面热键），也接受单张 bytes。

        task：solve = 解出题目（走作答模式）；bug = 找 Bug（走 BUG_PROMPT）。
        """
        if not self._busy.acquire(blocking=False):
            self._emit("上一个解题请求还在进行中，请稍候", True)
            return

        batch = [images] if isinstance(images, (bytes, bytearray)) else list(images)
        batch = [image for image in batch if image]
        if not batch:
            self._busy.release()
            self._emit("没有可提交的截图", True)
            return
        normalized_task = normalize_solve_task(task)

        def runner() -> None:
            try:
                self._run_stream(batch, normalized_task)
            except Exception as exc:
                self._emit(f"{_SOLVE_FAILURE_PREFIX}{exc}", True)
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

    def _run_stream(self, images: list[bytes], task: str = SOLVE_TASK_SOLVE) -> None:
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

        # 多张截图按提交顺序拼接（同一道题的不同部分）。图片之间不插入文字说明，
        # 由提示词统一交代「多张属于同一道题」，避免打断模型对图序的理解。
        image_parts = [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")},
            }
            for image in images
        ]
        # 解题是独立链路：题干在截图里已完整，不带简历/JD/知识库等面试上下文
        # （算法题/笔试题用不上，白占 token 与首字延迟）。
        # 找 Bug 是另一件事（查错而非求解），用它自己的内置提示词；
        # 自定义 solvePrompt 只作用于解题，不覆盖找 Bug（否则自定义解题模板
        # 会让「找 Bug」按钮去做解题，用户无从察觉）。
        user_text = vision["prompt"] if task == SOLVE_TASK_SOLVE else BUG_PROMPT
        if len(image_parts) > 1:
            subject = "这道题的不同部分" if task == SOLVE_TASK_SOLVE else "同一段代码/报错的不同部分"
            user_text = (
                f"以下 {len(image_parts)} 张截图是{subject}（按顺序给出），"
                "请把它们合起来理解，不要当成多个独立问题。\n\n" + user_text
            )

        messages = [
            {
                "role": "user",
                "content": image_parts + [{"type": "text", "text": user_text}],
            }
        ]

        from .ai_stream import stream_chat_completion

        # max_tokens 用解题独立档位（默认 2048）：部分网关把思考 token 也计入上限，
        # 1500 在长题目下可能返回 200 但 content 为空；算法题核心代码较长，
        # 需要更多时在设置页单独调大（不影响字幕 AI）。
        final_text = stream_chat_completion(
            url=vision["baseUrl"] + "/chat/completions",
            api_key=api_key,
            model=vision["model"],
            messages=messages,
            on_snapshot=self._emit,
            max_tokens=vision.get("maxTokens"),
            temperature=0.2,
            flush_seconds=SOLVE_STREAM_FLUSH_SECONDS,
            validate=False,  # 上面已用更具体的文案校验过
            thinking_mode=vision.get("thinkingMode"),
        )
        if not final_text:
            self._emit("（模型未返回内容）", True)


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
        """手机推送的文本写入电脑剪贴板；抑制由此产生的回环广播。

        写入前先打回环标记（poll_once 在别的线程轮询，写剪贴板与更新序列号
        之间有极短窗口，其间它可能读到本次写入的内容）；写入成功后把序列号
        推进到当前值并清掉标记——此后序列号再变化就一定是用户的新复制。
        序列号必须在写入**之后**取：若取写入前的值，poll_once 会因"序列号未变"
        提前返回，清不掉标记，之后用户真正重新复制时会被误判成回环而漏推。
        """
        self._last_pushed_from_phone = text
        ok = False
        try:
            ok = set_clipboard_text(text)
        except Exception:
            ok = False
        if ok:
            try:
                self._last_sequence = clipboard_sequence()
            except Exception:
                pass
            self._last_pushed_from_phone = ""
            if self.relay is not None:
                self.relay.latest_clipboard_text = text
        else:
            self._last_pushed_from_phone = ""
        return ok

    def poll_once(self) -> str | None:
        sequence = clipboard_sequence()
        if sequence == self._last_sequence:
            return None
        try:
            text = get_clipboard_text()
        except Exception:
            return None
        # 读取失败（剪贴板被其他进程占用时 get_clipboard_text 返回空）不能消费
        # 序列号，否则这次复制会被永久丢弃——序列号不再变化，也就不会再重试。
        if not text:
            return None
        self._last_sequence = sequence
        # 用「序列号是否变化」判断是否有新复制，而不是比对文本内容：
        # 用户重新复制同一段文字时内容与上次相同，按内容比对会漏推。
        # 只保留回环抑制——手机推来的文本会在电脑剪贴板里再出现一次。
        # 该抑制只作用于「紧接着的那次」：命中一次后即复位，之后用户
        # 主动重新复制同一段文字仍能正常推送。
        if text == self._last_pushed_from_phone:
            self._last_pushed_from_phone = ""
            return None
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
        # 自动提交（定时截屏 + 自动解题）的当前状态：勾选状态只活在服务端循环里，
        # 手机刷新页面后要能通过 hello 帧恢复，故单独记录。
        self._auto_solve = False
        self._auto_interval_ms = DEFAULT_AUTO_INTERVAL_SECONDS * 1000.0
        # 自动提交的降噪状态：连续失败计数 + 上一条已提示的失败文案。
        self._auto_failures = 0
        self._auto_last_failure = ""
        # 最近一次提交给解题引擎的请求是否来自自动循环（request_solve 里赋值）。
        # 自动提交的成败要等模型返回（几秒后）才知道，届时 _auto_solve 可能已经
        # 被停掉，靠它分不清「这轮失败是自动发的还是用户手点的」——手点失败必须
        # 原文回气泡封口，自动失败必须走降噪，故按提交时的来源单独记一份。
        self._auto_submitted = False
        # 待解截图缓冲：题干跨多屏时先「加一图」攒起来，再一次性提交（多图一题）。
        # 单独加锁：截屏走线程池、WebSocket 消息走事件循环，两个线程都会改它。
        self._pending_solve_images: list[bytes] = []
        self._pending_lock = threading.Lock()
        # 场次编号：开始新一场时自增，用于丢弃旧场次在途的解题流；
        # _solve_generation 记录当前正在跑的解题属于哪一场。
        self._session_generation = 0
        self._solve_generation = 0
        self.clipboard_handler: Any = None
        self.latest_clipboard_text = ""
        self.solve_engine = SolveEngine()
        self.solve_engine.on_delta = self._on_solve_delta
        self.desktop_publisher: Any = None  # callable(payload: dict) 桌面字幕窗分发
        # Debug 快照供应方（server.create_app 注入）：返回白名单字段的字典。
        # 用回调而非直接引用 server，避免 phone_share ↔ server 循环导入。
        self.debug_snapshot_provider: Any = None  # callable() -> dict
        self._debug_subscribed = False
        # 手机端追问上下文：最近的问答对（user/assistant 交替，上限 12 条）。
        self.phone_chat_history: list[dict] = []
        self._chat_lock = threading.Lock()
        # 持有后台 task 的强引用，避免事件循环仅弱引用导致高负载下被 GC 回收。
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro: Any) -> None:
        """在运行中的事件循环里创建后台 task，并保留强引用直到完成。"""
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError:
            return  # 事件循环已关闭：丢弃协程，避免告警
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _push_solve_message(self, text: str, done: bool) -> None:
        """把一条解题消息推给手机与桌面字幕窗（不含任何自动循环的统计逻辑）。

        单独拆出来是因为 _note_auto_failure 也要推消息：若它回头调
        _on_solve_delta，就会在「自动模式下的失败」分支里再统计一次，
        一次失败被算两次，3 次上限会提前触发。
        """
        self.schedule_json({"type": "ai", "text": text, "done": done, "source": "solve"})
        publisher = self.desktop_publisher
        if publisher is not None:
            try:
                publisher({"type": "solve_answer", "text": text, "done": done})
            except Exception:
                pass

    def _on_solve_delta(self, text: str, done: bool) -> None:
        # 「开始新一场」之后，上一场在途的解题流可能还在推快照。
        # 丢弃属于旧场次的增量，避免它污染新一场的记录与手机聊天流；
        # 但最后一条 done 仍要放行，否则旧气泡永远停在流式状态。
        if self._solve_generation != self._session_generation and not done:
            return
        if done and self._auto_submitted:
            # 自动模式的本轮成败只有在这里（真正的结束点）才知道：
            # solve_engine.solve() 只是起了个后台线程，模型类失败（未启用/无 key/
            # 网关 400）要几秒后才经这条 done 帧回来。曾经把统计写在 auto_loop 的
            # 提交点（if submitted），结果这类失败永远统计不到；抓屏失败则每轮推
            # 一条同样的气泡却一次都不计数，连续失败自停形同虚设。
            # 用 _auto_submitted（提交时的来源）而不是 _auto_solve（此刻的开关）：
            # 在途的一轮失败时用户可能刚把开关关掉，靠开关会漏掉这次失败。
            self._auto_submitted = False
            if text.startswith(_SOLVE_FAILURE_PREFIX):
                # 只认失败前缀，避免把正常回答里出现的「失败」二字误判成失败。
                reason = text[len(_SOLVE_FAILURE_PREFIX):].strip()
                self._note_auto_failure(reason)
                # 原文不再单独推：失败已由 _note_auto_failure 以「自动提交失败：…」
                # 的降噪文案推出（自动模式没有用户点出的待封口气泡），
                # 再推一次原文只会让同一次失败出现两个气泡。
                return
            self._note_auto_success()
        self._push_solve_message(text, done)

    def begin_new_session(self) -> None:
        """标记新一场开始：停自动截图、丢弃缓存帧，并让旧场次的在途流失效。"""
        self._session_generation += 1
        self.stop_auto_capture()
        # 上一场攒下的待解截图不能带进新一场：否则新一场的第一次提交会混入旧题。
        self.clear_pending_solve_images()

    def _debug_payload(self) -> dict:
        """诊断快照（白名单字段集合）。

        手机在局域网、调不到 /api/* 的管理接口（那些要求回环地址），所以诊断
        信息只能经 relay 下发。这里只放排障必需且不敏感的事实：
        绝不包含 API Key、完整 config、含凭据的 baseUrl 或用户资料。
        """
        data: dict = {
            "type": "debug",
            "sentAt": time.time(),
            "phones": len(self._phones),
            "session": self._session_generation,
            "solveBusy": self.solve_engine.busy,
            "images": len(self._latest_jpeg) if self._latest_jpeg else 0,
            "clipboardChars": len(self.latest_clipboard_text or ""),
        }
        provider = self.debug_snapshot_provider
        if provider is not None:
            try:
                extra = provider()
                if isinstance(extra, dict):
                    data.update(extra)
            except Exception:
                data["snapshotError"] = True
        return data

    def _push_debug_if_subscribed(self) -> None:
        """仅供内部定时调用：订阅开启时下发一次快照。"""
        if self._debug_subscribed:
            try:
                self.schedule_json(self._debug_payload())
            except Exception:
                pass

    def request_solve(self, task: Any = SOLVE_TASK_SOLVE, quiet_busy: bool = False) -> bool:
        """触发一次截图解题；返回 False 表示引擎未启用或正在进行。

        待解缓冲非空时提交缓冲里的全部截图（多图一题），否则现场截一张立即提交
        （桌面热键 Ctrl+Shift+C 与手机直接点「截题+回答」都走这条，行为与历史一致）。

        task：solve = 解出题目；bug = 找 Bug（同一套截图与流式通道，换提示词）。

        quiet_busy=True 表示本次调用来自自动循环（间隔到了）：此时本次提交的
        结果交给 _on_solve_delta 在 done 帧上统计，用于降噪与连续失败自停。

        busy 时主动推一条 done 提示给手机：否则手机点了「截题+回答」后
        只会看到自己插入的"正在截屏解题…"气泡永远不封口，以为卡住了。
        quiet_busy=True（自动提交）时不推该提示：自动模式下用户没点任何按钮，
        每隔几秒就弹一条"请求还在进行中"只是噪音，且解题本来就可能比间隔慢。
        """
        # 忙碌路径分两种，必须区别对待，否则会把在途那一轮的成败记错：
        #  · 自动循环本轮没提交（间隔比解题快）：「还在进行中」不是失败，
        #    跳过即可，也不能动在途那一轮的来源标记；
        #  · 用户手点而引擎正忙：这是手动请求的失败，必须让用户看到并封口气泡。
        #    若此时仍有自动轮在途，它的统计只能让位（该帧没有别的地方可标记），
        #    最坏结果是漏记一次失败，比「把自动轮误判成成功」轻。
        if self.solve_engine.busy:
            if not quiet_busy:
                self._auto_submitted = False
                self._on_solve_delta("上一个解题请求还在进行中，请稍候", True)
            return False
        # 走到这里说明本轮真的要提交了：先清掉上一轮的来源标记，
        # 避免它泄漏到本轮（手动提交 + 上一轮自动标记 = 把手动结果算进自动统计）。
        self._auto_submitted = False
        from .recorder import session_recorder

        with self._pending_lock:
            pending = list(self._pending_solve_images)
            had_pending = bool(pending)
            self._pending_solve_images.clear()
        # 只在确实清掉了待解图时才广播：常态（直接截一张）不发多余消息，
        # 避免给手机加噪音、也保持「先来截图帧」的既有顺序。
        if had_pending:
            self._push_pending_state(0)
        if pending:
            images = pending
        else:
            try:
                images = [capture_screen_jpeg()]
            except Exception:
                # 带上统一前缀，自动提交才能识别成本轮失败（否则会误判成成功、
                # 永远不触发降噪与自停）。
                if quiet_busy:
                    self._auto_submitted = True
                self._on_solve_delta(f"{_SOLVE_FAILURE_PREFIX}电脑端屏幕采集失败", True)
                return False
        # 多张图共用同一个批次号，落盘时才能整批归到这一次解题上。
        session_recorder.add_solve_images(images)
        self._latest_jpeg = images[-1]
        self._drain_bytes_threadsafe(images[-1])
        # 记录本次解题属于哪一场，供 _on_solve_delta 判断增量是否已过期
        self._solve_generation = self._session_generation
        # 标记本次提交来自自动循环，供 _on_solve_delta 在本轮结束时统计成败。
        # 必须在 solve() 之前置位：模型可能返回得极快，晚置位会漏掉那一帧。
        if quiet_busy:
            self._auto_submitted = True
        self.solve_engine.solve(images, task)
        return True

    def add_pending_solve_image(self) -> int:
        """把当前屏幕追加到待解缓冲，返回缓冲张数；0 表示已满或采集失败。

        题干跨多屏时用：连点几次「加一图」把各部分都截进来，再点「截题+回答」
        一次性提交。上限由 visionMaxImages 配置决定。
        """
        limit = max(1, int(load_vision_config().get("maxImages") or DEFAULT_MAX_SOLVE_IMAGES))
        try:
            jpeg = capture_screen_jpeg()
        except Exception:
            return -1
        with self._pending_lock:
            if len(self._pending_solve_images) >= limit:
                return 0
            self._pending_solve_images.append(jpeg)
            count = len(self._pending_solve_images)
        self._push_pending_state(count)
        # 手机端同时看到最新一张，便于确认截到的是不是想要的那屏
        self._latest_jpeg = jpeg
        self._drain_bytes_threadsafe(jpeg)
        return count

    def clear_pending_solve_images(self) -> None:
        with self._pending_lock:
            self._pending_solve_images.clear()
        self._push_pending_state(0)

    def _push_pending_state(self, count: int) -> None:
        """待解张数变化时广播给所有手机。"""
        limit = max(1, int(load_vision_config().get("maxImages") or DEFAULT_MAX_SOLVE_IMAGES))
        try:
            self.schedule_json({"type": "solve_pending", "count": count, "limit": limit})
        except Exception:
            pass

    def _drain_bytes_threadsafe(self, data: bytes) -> None:
        if not self._phones:
            return
        self._call_soon_threadsafe(self._drain_bytes, data)

    def bind_loop(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def handle(self, websocket: Any, sid: str, token: str) -> None:
        config = load_phone_config()
        expected_sid = str(config.get("sid") or "")
        expected_token = str(config.get("token") or "")
        # 常量时间比较；用 UTF-8 字节避免 compare_digest 对非 ASCII 字符串抛 TypeError。
        sid_ok = bool(sid) and secrets.compare_digest(sid.encode("utf-8"), expected_sid.encode("utf-8"))
        token_ok = bool(token) and secrets.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8"))
        if not config["enabled"] or not sid_ok or not token_ok:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        # 确保已知当前事件循环：lifespan 之外的调用路径（如测试/嵌入）也能正常推送。
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        self._phones.add(websocket)
        try:
            await websocket.send_json({
                "type": "hello",
                "sid": sid,
                # 当前作答模式：手机端据此高亮切换按钮（core_code / acm）。
                "visionMode": normalize_answer_mode(
                    _read_config_value("visionAnswerMode")
                ),
                # 当前解题思考模式："" = 跟随字幕 AI / off / auto。
                "visionThinking": normalize_vision_thinking_mode(
                    _read_config_value("visionThinkingMode")
                ),
                # 待解截图张数与上限：重连后按钮上的计数要对得上。
                "solvePending": len(self._pending_solve_images),
                "solveImageLimit": load_vision_config().get("maxImages")
                or DEFAULT_MAX_SOLVE_IMAGES,
                # 自动提交的当前状态：重连后复选框与间隔下拉要显示服务端的实际值
                # （勾选状态只存在于服务端循环里，刷新页面不会自己恢复）。
                "autoSolve": self._auto_solve,
                "autoIntervalSec": int(self._auto_interval_ms / 1000),
                # 字幕 AI 的自动/手动模式（桌面 config.json 的 aiAutoSubmit）：
                # 刷新页面后开关要显示实际值，否则界面与真实行为相反。
                "aiAutoSubmit": load_auto_submit(),
            })
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
                    self._spawn(self._capture_and_push())
                elif kind == "solve":
                    self._spawn(self._handle_solve(SOLVE_TASK_SOLVE))
                elif kind == "solve_bug":
                    # 找 Bug：与解题同一套截图/流式通道，只是换内置提示词。
                    self._spawn(self._handle_solve(SOLVE_TASK_BUG))
                elif kind == "solve_add":
                    # 追加一张待解截图（题干跨屏时连点几次，再一次性提交）。
                    self._spawn(self._handle_solve_add())
                elif kind == "solve_clear":
                    self.clear_pending_solve_images()
                elif kind == "ask":
                    phone_text = str(payload.get("text", ""))[:4000]
                    if phone_text:
                        self._spawn(self._handle_ask(phone_text))
                elif kind == "clear_chat":
                    with self._chat_lock:
                        self.phone_chat_history.clear()
                    self.schedule_json({"type": "chat_cleared"})
                elif kind == "auto":
                    # solve=true 时为「自动提交问题」（定时截屏并自动解题），
                    # 否则是原「自动刷新截图」（只推送画面）。
                    self._set_auto(
                        bool(payload.get("on")),
                        payload.get("interval"),
                        solve=bool(payload.get("solve")),
                    )
                elif kind == "clipboard":
                    phone_text = str(payload.get("text", ""))[:MAX_CLIPBOARD_CHARS]
                    if phone_text:
                        self._spawn(self._handle_clipboard(phone_text))
                elif kind == "vision_mode":
                    # 手机端切写作答模式：只写 visionAnswerMode 一个键，白名单校验后
                    # 向所有手机广播当前值（多台手机时保持一致）。
                    applied = set_vision_answer_mode(payload.get("mode"))
                    if applied is None:
                        self._notify_config_write_failed()
                    else:
                        self.schedule_json({"type": "vision_mode", "mode": applied})
                elif kind == "vision_thinking":
                    # 手机端切换解题思考模式：同样只写 visionThinkingMode 一个键。
                    # 三态 "" = 跟随字幕 AI / off / auto。
                    applied = set_vision_thinking_mode(payload.get("mode"))
                    if applied is None:
                        self._notify_config_write_failed()
                    else:
                        self.schedule_json({"type": "vision_thinking", "mode": applied})
                elif kind == "ask_now":
                    # 手机端「问 AI」按钮：把「立即提交当前字幕」的意图转给桌面。
                    # 字幕文本只存在于 C# 进程（服务端只做识别与转发、不保存转写），
                    # 所以这里不能自己发请求，只能经 hub 广播给 C# 由它执行。
                    self._request_manual_ai_submit()
                elif kind == "auto_submit":
                    # 手机端切换「自动提交」：与桌面共用 config.json 的 aiAutoSubmit，
                    # 桌面每秒重载一次配置，约 1 秒生效。
                    #
                    # applied 为 None 时绝不能回一帧 on:false：那等于告诉手机与桌面
                    # 「已成功切到手动」，而磁盘根本没动，界面显示与实际行为相反。
                    applied = set_auto_submit(payload.get("on"))
                    if applied is None:
                        # 复选框是浏览器原生翻过去的（乐观更新），写盘失败必须让它
                        # 退回原值，否则手机显示的状态与实际相反。回退基准交给手机
                        # 自己（它知道自己最后一次确认过的值）—— 服务端此刻读不到
                        # 文件，回任何值都是猜的（真机验证：原值 False 时回退默认
                        # True，手机显示「已开启」，磁盘上却是关的）。
                        self._notify_config_write_failed(revert="auto_submit")
                    else:
                        self.schedule_json({"type": "auto_submit", "on": applied})
                elif kind == "ping":
                    # 手机端测 RTT：原样回带时间戳，不做任何计算。
                    self.schedule_json({"type": "pong", "t": payload.get("t")})
                elif kind == "debug":
                    # 只允许开关订阅，不接受任何写入参数（手机不能改配置）。
                    self._debug_subscribed = bool(payload.get("on"))
                    if self._debug_subscribed:
                        self.schedule_json(self._debug_payload())
        except Exception:
            pass
        finally:
            self._phones.discard(websocket)
            # 最后一台手机断开时停掉自动截图：否则 auto_loop 会继续空转截屏。
            if not self._phones:
                self.stop_auto_capture()

    async def _handle_solve(self, task: str = SOLVE_TASK_SOLVE) -> None:
        await asyncio.to_thread(self.request_solve, task)

    def _notify_config_write_failed(self, revert: str = "") -> None:
        """配置写盘失败时如实回一条提示（手机端 case "notice" 已存在）。

        必须让用户知道「这次切换没有生效」：静默失败会让手机上的开关停在
        用户刚点的位置，而电脑端行为没变 —— 用户以为切了，实际没切。
        写盘失败通常是 config.json 被杀软/备份/索引器瞬时占用，稍后重试即可，
        所以文案要给出可执行的下一步，而不是只说失败。

        revert：需要手机把自己那个「乐观更新」的控件退回原值的，填控件对应的键名。
        服务端**不能**在这里回一个「磁盘真实值」——写失败正是因为读不出这个文件，
        任何取值都是猜的（真机验证踩到：原值是 False，回退默认值 True，
        手机显示「已开启自动提交」而磁盘上是关的）。唯一可靠的参照是手机侧
        最后一次从服务端确认过的值，所以由手机自己退回去。
        """
        payload = {
            "type": "notice",
            "message": "配置写入失败（config.json 被占用？），请稍后重试",
        }
        if revert:
            payload["revert"] = revert
        self.schedule_json(payload)

    def _desktop_online(self) -> bool:
        """桌面（C# Overlay）是否连着。判断依据是 hub 的 WS 客户端数。

        经 debug_snapshot_provider 间接取（server.create_app 注入），
        与手机诊断面板用的是同一个来源，不额外引入依赖。
        """
        provider = self.debug_snapshot_provider
        if provider is None:
            return True  # 取不到就当作在线：宁可多转一次，也不要误报「电脑不在线」
        try:
            snapshot = provider()
            if isinstance(snapshot, dict) and "wsClients" in snapshot:
                return int(snapshot["wsClients"]) > 0
        except Exception:
            pass
        return True

    def _request_manual_ai_submit(self) -> None:
        """手机端「问 AI」→ 经 hub 广播给桌面，由 C# 用当前字幕发起请求。

        用 desktop_publisher（= hub.publish，广播给所有 WS 客户端、含 C#）
        而不是 schedule_json（只发手机）：这条消息的消费者是桌面，不是手机。

        桌面没连上时必须如实回一条提示：字幕文本只存在于 C# 进程，
        没有桌面这条消息必然无人处理 —— 若手机照样显示「已请求电脑提交」，
        用户会反复点按钮并以为功能坏了（点一次提示一次，比静默无反应更清楚）。
        """
        if not self._desktop_online():
            self.schedule_json({
                "type": "manual_ai_unavailable",
                "message": "电脑端字幕程序未运行（或已断开），无法提交字幕",
            })
            return
        publisher = self.desktop_publisher
        if publisher is None:
            self.schedule_json({
                "type": "manual_ai_unavailable",
                "message": "电脑端字幕程序未就绪，无法提交字幕",
            })
            return
        try:
            publisher({"type": "ask_now"})
        except Exception:
            pass

    async def _handle_solve_add(self) -> None:
        """追加一张待解截图；失败/已满时回一条提示，避免手机端无声无息。"""
        added = await asyncio.to_thread(self.add_pending_solve_image)
        if added == -1:
            self.schedule_json(
                {"type": "ai", "text": "电脑端屏幕采集失败", "done": True, "source": "solve"}
            )
        elif added == 0:
            limit = load_vision_config().get("maxImages") or DEFAULT_MAX_SOLVE_IMAGES
            self.schedule_json(
                {
                    "type": "ai",
                    "text": f"已达上限（{limit} 张），请先点「截题+回答」提交，或点清空重来",
                    "done": True,
                    "source": "solve",
                }
            )

    async def _handle_ask(self, question: str) -> None:
        """手机文字提问：系统提示词 + 最近对话历史（追问）+ 本次问题走真实 AI 管线。"""
        from .server import effective_system_prompt
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
        with self._chat_lock:
            history = list(self.phone_chat_history)

        from .ai_stream import build_messages, stream_chat_completion

        endpoint = settings["aiBaseUrl"].rstrip("/") + "/chat/completions"
        messages = build_messages(prompt, question, history)

        def run():
            # 流式：每个快照即时推给手机，避免等全文返回才显示（长回答体感差别很大）。
            return stream_chat_completion(
                url=endpoint,
                api_key=api_key,
                model=settings["aiModel"],
                messages=messages,
                on_snapshot=lambda text, done: self.schedule_json(
                    {"type": "ai", "text": text, "done": done, "source": "ask"}
                ),
                # 回答长度上限跟随设置（与字幕 AI 同一档位）；缺键由
                # normalize_max_tokens 回退默认，不会因 None 报错。
                max_tokens=settings.get("aiMaxTokens"),
                thinking_mode=settings.get("aiThinkingMode"),
            )

        try:
            answer = await asyncio.to_thread(run)
            if not answer:
                answer = "（模型未返回内容）"
                self.schedule_json({"type": "ai", "text": answer, "done": True, "source": "ask"})
            with self._chat_lock:
                self.phone_chat_history.append({"role": "user", "content": question})
                self.phone_chat_history.append({"role": "assistant", "content": answer})
                while len(self.phone_chat_history) > 12:
                    self.phone_chat_history.pop(0)
        except Exception as exc:
            self.schedule_json({"type": "ai", "text": f"请求失败：{exc}", "done": True, "source": "ask"})

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
        self._call_soon_threadsafe(self._drain_json, message)

    def _running_loop(self) -> asyncio.AbstractEventLoop | None:
        if self._loop is not None:
            return self._loop
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _call_soon_threadsafe(self, callback: Any, *args: Any) -> None:
        """从引擎线程投递回调；事件循环关闭阶段容忍 RuntimeError（服务正在退出）。"""
        loop = self._running_loop()
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            pass

    def _drain_json(self, message: str) -> None:
        for websocket in tuple(self._phones):
            self._spawn(self._safe_send_text(websocket, message))

    def _drain_bytes(self, data: bytes) -> None:
        for websocket in tuple(self._phones):
            self._spawn(self._safe_send_bytes(websocket, data))

    async def _safe_send_text(self, websocket: Any, message: str) -> None:
        try:
            await websocket.send_text(message)
        except Exception:
            self._phones.discard(websocket)
            if not self._phones:
                self.stop_auto_capture()

    async def _safe_send_bytes(self, websocket: Any, data: bytes) -> None:
        try:
            await websocket.send_bytes(data)
        except Exception:
            self._phones.discard(websocket)
            if not self._phones:
                self.stop_auto_capture()

    def _note_auto_success(self) -> None:
        """自动提交成功一次：清零失败计数（偶发失败不该累积成自停）。"""
        self._auto_failures = 0
        self._auto_last_failure = ""

    def _note_auto_failure(self, reason: str) -> None:
        """自动提交失败一次：降噪提示，连续失败到上限就自停。

        降噪规则：同一失败文案只提示第一次；原因变化时再提示一次
        （否则用户看不到新原因）。连续失败达 AUTO_FAILURE_LIMIT 时停止循环，
        并推一条停止通知 —— 这类问题不会自愈，一直重试只会刷屏。
        """
        reason = str(reason or "").strip() or "未知错误"
        self._auto_failures += 1
        first_time = reason != self._auto_last_failure
        self._auto_last_failure = reason
        if first_time:
            self._push_solve_message(f"自动提交失败：{reason}", True)
        if self._auto_failures >= AUTO_FAILURE_LIMIT and self._auto_solve:
            # 只在仍在运行时停一次：已经停掉后若还收到失败（在途的一轮），
            # 不再重复喊「已停止」，否则又变成新的刷屏源。
            self._auto_generation += 1  # 让 auto_loop 退出
            self._auto_solve = False
            self._push_solve_message(
                f"自动提交已停止（连续 {self._auto_failures} 次失败）：{reason}",
                True,
            )
            self._push_auto_state()

    def _push_auto_state(self) -> None:
        """把自动提交的当前状态推给手机：停止后要让界面上的勾选一起复位，
        否则界面显示仍在自动提交、实际循环已经退出。"""
        try:
            self.schedule_json({
                "type": "auto_state",
                "on": self._auto_solve,
                "intervalSec": int(self._auto_interval_ms / 1000),
            })
        except Exception:
            pass

    def _set_auto(self, on: bool, interval_ms: Any, solve: bool = False) -> None:
        """开关自动循环。

        solve=False：只定时截屏推送到手机（原「自动刷新截图」）。
        solve=True ：定时截屏并**自动提交给 AI 解题**（「自动提交问题」）。
                    引擎忙碌时跳过本轮而不是排队——解题常要十几秒到几十秒，
                    排队会让积压的请求在面试结束后还在跑。
        """
        self._auto_generation += 1
        interval = normalize_auto_interval_ms(interval_ms)
        self._auto_solve = bool(solve)
        self._auto_interval_ms = interval
        # 每次开启都从零开始计数：自停时计数停在 AUTO_FAILURE_LIMIT，
        # 不清零的话用户重新勾选后第一次失败就立刻又停（3+1 ≥ 3），
        # 看起来像开关坏了、再也不敢用。
        self._auto_failures = 0
        self._auto_last_failure = ""
        if not on or self._running_loop() is None:
            self._auto_solve = False
            return
        generation = self._auto_generation

        async def auto_loop() -> None:
            while generation == self._auto_generation:
                if solve:
                    # 这里只负责「提交」：本轮成败要等模型返回（几秒到几十秒后）
                    # 由 _on_solve_delta 在 done 帧上统计。此处读不到结果——solve()
                    # 只起了后台线程，抓屏失败也会立刻 return False 而绕过统计，
                    # 曾经因此让抓屏失败每轮推一条同样的气泡、却永远不自停。
                    await asyncio.to_thread(self.request_solve, SOLVE_TASK_SOLVE, True)
                else:
                    await self._capture_and_push()
                # 单位换算必须在这里：interval 是毫秒（状态字段/hello 帧都用毫秒），
                # asyncio.sleep 收的是秒。漏掉 /1000 会让「每 5 秒」睡 5000 秒。
                await asyncio.sleep(interval / 1000.0)

        self._spawn(auto_loop())

    async def _capture_and_push(self) -> None:
        # 先判断有没有手机再截屏：手机断开后 auto_loop 可能还没退出，
        # 若无条件截屏就会在没有消费者的情况下持续采集屏幕（隐私 + 资源）。
        if not self._phones:
            return
        try:
            jpeg = await asyncio.to_thread(capture_screen_jpeg)
        except Exception:
            self.schedule_json({"type": "error", "message": "电脑端屏幕采集失败"})
            return
        if not jpeg:
            return
        self._latest_jpeg = jpeg
        if self._phones:
            self._call_soon_threadsafe(self._drain_bytes, jpeg)

    def stop_auto_capture(self) -> None:
        """停止自动刷新/自动提交并丢弃缓存帧（手机断开、开始新一场时调用）。

        仅递增 generation 让 auto_loop 自然退出，不在此处打断正在进行的截屏。
        """
        self._auto_generation += 1
        self._auto_solve = False
        self._latest_jpeg = None
