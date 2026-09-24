"""真机验证「手机 → relay → 桌面」的 ask_now 转发与 auto_submit 开关读写。

背景：字幕文本只存在于 C# 进程，所以手机点「问 AI」时必须把它转发给桌面。
本脚本用一个「冒充 C#」的 WS 客户端连到 /ws，再从手机 relay 发 ask_now，
断言那条广播确实到达了桌面侧 —— 这是纯服务端可自动验证的部分。
（悬浮窗按钮的鼠标命中需要真实鼠标，另行人工确认。）

用法：先启动服务，再运行
    .venv\Scripts\python.exe tools\verify_ask_now.py
"""
from __future__ import annotations

import asyncio
import json
import sys

from system_audio_asr import phone_share

DESKTOP_TIMEOUT = 5.0


async def main() -> int:
    import websockets

    cfg = phone_share.load_phone_config()
    phone_url = (
        f"ws://127.0.0.1:8765/relay?role=phone&sid={cfg['sid']}&t={cfg['token']}"
    )
    desktop_url = "ws://127.0.0.1:8765/ws"

    problems: list[str] = []
    received: list[dict] = []

    # 冒充桌面：连 /ws 并记录收到的广播（C# 就是这样收到 ask_now 的）
    async with websockets.connect(desktop_url) as desktop:
        await asyncio.wait_for(desktop.recv(), timeout=3)  # hello

        async def collect() -> None:
            try:
                while True:
                    raw = await asyncio.wait_for(desktop.recv(), timeout=DESKTOP_TIMEOUT)
                    if isinstance(raw, str):
                        received.append(json.loads(raw))
            except asyncio.TimeoutError:
                return

        collector = asyncio.create_task(collect())

        async with websockets.connect(phone_url) as phone:
            hello = json.loads(await asyncio.wait_for(phone.recv(), timeout=3))
            print(f"手机已连接；hello 帧 aiAutoSubmit={hello.get('aiAutoSubmit')}")
            if "aiAutoSubmit" not in hello:
                problems.append("hello 帧未带 aiAutoSubmit：刷新后开关会显示错误状态")

            # ① 手机发 ask_now → 桌面应收到
            await phone.send(json.dumps({"type": "ask_now"}))
            await asyncio.sleep(1.2)
            hits = [m for m in received if m.get("type") == "ask_now"]
            print(f"① 桌面收到 ask_now：{len(hits)} 条")
            if not hits:
                problems.append("ask_now 没有转发到桌面（按钮会没反应）")

            # ② 手机切 auto_submit=false → 该键要落盘，且回执帧要回到手机
            #    （回执走 schedule_json，消费者是手机；只看桌面侧会漏掉这条断言）
            await phone.send(json.dumps({"type": "auto_submit", "on": False}))
            phone_frames: list[dict] = []
            try:
                while True:
                    raw = await asyncio.wait_for(phone.recv(), timeout=1.5)
                    if isinstance(raw, str):
                        phone_frames.append(json.loads(raw))
            except asyncio.TimeoutError:
                pass
            acks = [m for m in phone_frames if m.get("type") == "auto_submit"]
            print(f"② 手机收到的 auto_submit 回执：{len(acks)} 条 {acks}")
            if not acks:
                problems.append("手机切开关后没收到回执：界面状态可能与实际不符")
            elif acks[-1].get("on") is not False:
                problems.append(f"回执内容不对: {acks[-1]}")
            current = phone_share.load_auto_submit()
            print(f"   config.json 里 aiAutoSubmit = {current}")
            if current is not False:
                problems.append("切换 auto_submit=False 没有落盘")

            # ③ 切回去
            await phone.send(json.dumps({"type": "auto_submit", "on": True}))
            await asyncio.sleep(1.0)
            restored = phone_share.load_auto_submit()
            print(f"③ 切回后 aiAutoSubmit = {restored}")
            if restored is not True:
                problems.append("切回 auto_submit=True 失败")

        collector.cancel()

    print()
    if problems:
        print("未通过：")
        for item in problems:
            print("  -", item)
        return 1
    print("通过：ask_now 转发链路可用；auto_submit 开关能落盘并回填 hello 帧")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
