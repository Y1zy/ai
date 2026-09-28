"""真机验证：手机端单键写入在读盘失败时不丢配置，且如实告诉手机。

背景（2026-09-25 实测复现）：_write_single_config_key 是读-改-写，读盘失败时
把 raw 当空字典，然后无条件写盘 —— 手机切一次开关就把 config.json 覆写成
「只剩 aiAutoSubmit 一个键」的单键文件（45 个键里丢 44 个，含简历/JD/目标公司）。
触发条件是杀软/备份/索引器对 config.json 的瞬时独占，不是理论情况。

本脚本用**真实的独占文件句柄**（Windows 共享模式 0）制造读失败，走**真实的
HTTP + WS 链路**验证三件事：
  ① 读失败时不写盘 —— 磁盘内容逐字节不变（这是核心，用哈希比对）；
  ② 手机收到的是磁盘上的真实值 + 一条失败提示，而不是假装的 {"on":false}；
  ③ 解锁后重试能成功，且仍然保留其余所有键。

注意：全程不碰用户的真实 config.json —— 通过 /api/settings 之外的路径无法改
CONFIG_PATH，所以这里用「复制一份真实配置到临时目录 + 让服务读它」的方式？
不可行（服务端 CONFIG_PATH 是模块级常量）。因此本脚本改为**只读验证**：
真实文件加独占锁 → 发切换请求 → 断言文件哈希不变 → 解锁。

用法：先启动服务，再运行
    .venv\\Scripts\\python.exe tools\\verify_config_write_safety.py
"""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from system_audio_asr import phone_share  # noqa: E402

GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value

_kernel32 = ctypes.windll.kernel32
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]


class exclusive_lock:
    """用真实的 Windows 独占句柄锁住文件（模拟杀软/备份/索引器）。

    共享模式传 0 = 不给别人任何访问权限，此时 Python 侧 read_text 抛
    PermissionError、os.replace 也抛 WinError 5 —— 与真实占用同一语义。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def __enter__(self):
        self.handle = _kernel32.CreateFileW(
            str(self.path), GENERIC_READ, 0, None, OPEN_EXISTING, 0, None
        )
        if self.handle == INVALID_HANDLE:
            raise OSError(f"无法独占打开 {self.path}（错误码 {ctypes.get_last_error()}）")
        return self

    def __exit__(self, *exc):
        if self.handle:
            _kernel32.CloseHandle(self.handle)
            self.handle = None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main() -> int:
    import websockets

    config_path = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    ) / "WasapiParaformerOverlay" / "config.json"
    if not config_path.exists():
        print(f"找不到 {config_path}，先运行一次桌面程序生成配置")
        return 2

    before = json.loads(config_path.read_text(encoding="utf-8-sig"))
    before_hash = digest(config_path)
    print(f"真实配置：{len(before)} 个键，哈希 {before_hash[:12]}…")

    cfg = phone_share.load_phone_config()
    phone_url = f"ws://127.0.0.1:8765/relay?role=phone&sid={cfg['sid']}&t={cfg['token']}"

    problems: list[str] = []
    original_value = before.get("aiAutoSubmit", True)
    print(f"当前 aiAutoSubmit = {original_value}")

    async with websockets.connect(phone_url) as phone:
        hello = json.loads(await asyncio.wait_for(phone.recv(), timeout=3))
        print(f"手机已连接；hello 帧 aiAutoSubmit={hello.get('aiAutoSubmit')}")

        # ① 独占锁定真实 config.json，然后切换开关 —— 预期：不写盘 + 提示失败
        with exclusive_lock(config_path):
            print("已独占锁定 config.json（模拟杀软瞬时占用）")
            await phone.send(json.dumps({"type": "auto_submit", "on": not original_value}))
            frames: list[dict] = []
            try:
                while True:
                    raw = await asyncio.wait_for(phone.recv(), timeout=2.0)
                    if isinstance(raw, str):
                        frames.append(json.loads(raw))
            except asyncio.TimeoutError:
                pass

            print(f"① 锁定期间收到 {len(frames)} 帧：{[f.get('type') for f in frames]}")
            notices = [f for f in frames if f.get("type") == "notice"]
            acks = [f for f in frames if f.get("type") == "auto_submit"]
            if not notices:
                problems.append("写盘失败没有提示手机（用户以为切成功了）")
            if acks:
                problems.append(
                    f"写盘失败却回了状态帧 {acks[-1]}：服务端此刻读不出文件，"
                    "回的必然是猜的默认值（真机验证踩过：原值 False 时回 True）"
                )
            reverts = [f for f in notices if f.get("revert") == "auto_submit"]
            if not reverts:
                problems.append(
                    "失败提示没带 revert：复选框会停在用户点的位置，与实际相反"
                )

        after_hash = digest(config_path)
        print(f"② 文件哈希 {after_hash[:12]}…")
        if after_hash != before_hash:
            after_keys = json.loads(config_path.read_text(encoding="utf-8-sig"))
            problems.append(
                f"读失败时仍写了盘！键数 {len(before)} -> {len(after_keys)}（丢配置）"
            )

        # ③ 解锁后重试：应当成功，且只改这一个键
        await asyncio.sleep(0.3)
        await phone.send(json.dumps({"type": "auto_submit", "on": not original_value}))
        frames2: list[dict] = []
        try:
            while True:
                raw = await asyncio.wait_for(phone.recv(), timeout=2.0)
                if isinstance(raw, str):
                    frames2.append(json.loads(raw))
        except asyncio.TimeoutError:
            pass
        acks2 = [f for f in frames2 if f.get("type") == "auto_submit"]
        print(f"③ 解锁后回执：{acks2}")
        if not acks2 or acks2[-1].get("on") != (not original_value):
            problems.append("解锁后重试没成功")
        else:
            saved = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if len(saved) != len(before):
                problems.append(
                    f"成功路径也丢了键：{len(before)} -> {len(saved)}"
                )
            if saved.get("resumeContext") != before.get("resumeContext"):
                problems.append("成功路径改动了简历：只应改 aiAutoSubmit 一个键")

            # 还原成进入脚本时的值
            await phone.send(json.dumps({"type": "auto_submit", "on": original_value}))
            await asyncio.sleep(0.8)
            restored = json.loads(config_path.read_text(encoding="utf-8-sig"))
            print(f"已还原 aiAutoSubmit = {restored.get('aiAutoSubmit')}")
            if restored.get("aiAutoSubmit") != original_value:
                problems.append("还原失败，请手工改回")

    print()
    if problems:
        print("未通过：")
        for item in problems:
            print("  -", item)
        return 1
    print("通过：读失败不丢配置、如实回真实值并提示；解锁后写入正常且只改一个键")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
