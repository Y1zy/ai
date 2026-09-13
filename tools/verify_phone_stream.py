"""真实链路验证：手机端 WebSocket 监听 C# 推送的流式帧，记录每帧到达时刻。

用法：先运行本脚本（后台），再播放语音触发桌面识别与 AI 请求。
"""
import asyncio
import json
import sys
import time

from system_audio_asr import phone_share

cfg = phone_share.load_phone_config()
url = f"ws://127.0.0.1:8765/relay?role=phone&sid={cfg['sid']}&t={cfg['token']}"
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0


async def main():
    import websockets

    async with websockets.connect(url) as ws:
        await asyncio.wait_for(ws.recv(), timeout=3)  # hello
        t0 = time.monotonic()
        frames = []
        try:
            while time.monotonic() - t0 < DURATION:
                remaining = DURATION - (time.monotonic() - t0)
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
                if not isinstance(raw, str):
                    continue
                msg = json.loads(raw)
                if msg.get("type") != "ai":
                    continue
                frames.append({
                    "t": round(time.monotonic() - t0, 2),
                    "turn": msg.get("turn"),
                    "done": msg.get("done"),
                    "question": (msg.get("question") or "")[:30],
                    "len": len(msg.get("text") or ""),
                })
        except asyncio.TimeoutError:
            pass

        print(f"共收到 {len(frames)} 帧：")
        for f in frames:
            q = f" Q={f['question']!r}" if f["question"] else ""
            print(f"  +{f['t']:5.2f}s turn={f['turn']} done={str(f['done']):5s} len={f['len']:4d}{q}")

        # 判定：同一 turn 多帧且时间散开 = 真流式
        by_turn = {}
        for f in frames:
            by_turn.setdefault(f["turn"], []).append(f)
        print()
        for turn, group in by_turn.items():
            if turn is None:
                continue
            span = group[-1]["t"] - group[0]["t"]
            growing = [g["len"] for g in group]
            monotonic = growing == sorted(growing)
            print(f"turn={turn}: {len(group)} 帧, 跨度 {span:.2f}s, "
                  f"长度递增={monotonic}, 有题目={'是' if any(g['question'] for g in group) else '否'}")
        print()
        multi = [t for t, g in by_turn.items() if t and len(g) > 2]
        print("✓ 真流式：多帧随时间到达" if multi else "✗ 仍是单帧（未流式）")


asyncio.run(main())
