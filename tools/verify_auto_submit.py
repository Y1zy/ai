"""真实链路验证：手机端「自动提交问题」是否按选定间隔真的在循环。

背景（2026-09-23）：`_set_auto` 把毫秒直接喂给 asyncio.sleep，每 5 秒变成 83 分钟，
用户勾选后只看到第一轮就再无动静。单元测试用桩覆盖了单位换算，这里做真机复核。

两种模式：
  --mode capture（默认）只截屏推送，不调模型也不受忙碌跳过影响，
      帧间隔应≈设定间隔 —— 这是单位换算最干净的证据；
  --mode solve 走真实解题（会消耗模型额度），帧间隔会被「上一题还在解」拉长，
      只验证「第一轮之后循环仍在继续」（单位错误时第二轮要等 50 分钟以上）。

用法：先启动服务，再运行
    .venv\Scripts\python.exe tools\verify_auto_submit.py --mode capture --seconds 3 --observe 15
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from system_audio_asr import phone_share


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=3, help="自动提交间隔（秒）")
    parser.add_argument("--observe", type=float, default=15.0, help="观察时长（秒）")
    parser.add_argument(
        "--mode", choices=("capture", "solve"), default="capture",
        help="capture=只截屏（默认，不花钱）；solve=真实解题（会消耗模型额度）",
    )
    args = parser.parse_args()
    solve = args.mode == "solve"

    cfg = phone_share.load_phone_config()
    url = (
        f"ws://127.0.0.1:8765/relay?role=phone&sid={cfg['sid']}&t={cfg['token']}"
    )

    import websockets

    jpegs: list[float] = []
    answers: list[tuple[float, bool, int]] = []

    async with websockets.connect(url) as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        print(f"已连接：hello 帧 autoSolve={hello.get('autoSolve')} "
              f"autoIntervalSec={hello.get('autoIntervalSec')}")

        await ws.send(json.dumps({
            "type": "auto", "on": True,
            "interval": args.seconds * 1000, "solve": solve,
        }))
        label = "自动提交（每轮截图并解题）" if solve else "自动刷新（每轮只截图，不调模型）"
        print(f"已开启{label}：每 {args.seconds} 秒（观察 {args.observe:.0f} 秒）")

        started = time.monotonic()
        # 每轮先推一张 jpeg（截屏），解题模式下随后是若干 ai 帧、以 done 结尾。
        try:
            while time.monotonic() - started < args.observe:
                remaining = args.observe - (time.monotonic() - started)
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
                now = time.monotonic() - started
                if isinstance(raw, (bytes, bytearray)):
                    jpegs.append(now)
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "ai" and msg.get("source") == "solve":
                    answers.append((now, bool(msg.get("done")), len(msg.get("text") or "")))
                elif msg.get("type") == "auto_state":
                    print(f"  [{now:5.2f}s] auto_state on={msg.get('on')}")
        except asyncio.TimeoutError:
            pass

        await ws.send(json.dumps({"type": "auto", "on": False, "interval": args.seconds * 1000}))
        print("已发送停止指令")

    print(f"\n截屏帧 {len(jpegs)} 张：{[round(t, 1) for t in jpegs]}")
    gaps = [round(b - a, 2) for a, b in zip(jpegs, jpegs[1:])]
    print(f"帧间隔：{gaps}")
    print(f"解题帧 {len(answers)} 条（时刻, done, 字数）：")
    for item in answers:
        print(f"  {item[0]:5.2f}s done={item[1]} len={item[2]}")

    print()
    if solve:
        # 解题模式下引擎忙碌时本轮跳过（解题要十几秒、比间隔慢），所以帧间隔
        # **不等于**设定间隔，张数也不是 observe/interval。能区分单位 bug 的证据是：
        # 第一轮之后必须再来一轮 —— 若毫秒被当成秒，第二轮要等 50 分钟以上。
        if len(jpegs) < 2:
            print("失败：只收到 1 张截屏，第一轮之后循环没有继续 —— 间隔很可能又被当成了毫秒")
            return 1
        print(f"通过：{args.observe:.0f} 秒内发生 {len(jpegs)} 轮提交（设定间隔 {args.seconds} 秒，"
              f"其余轮次因上一题还在解而按设计跳过）")
        return 0

    # 纯截屏模式：不受忙碌跳过影响，帧间隔应贴近设定间隔（含截屏耗时约 0.1-0.3 秒）。
    if len(jpegs) < 2:
        print("失败：只收到 1 张截屏，第一轮之后循环没有继续 —— 间隔很可能又被当成了毫秒")
        return 1
    worst = max(gaps)
    tolerance = max(1.5, args.seconds * 0.5)
    if worst > args.seconds + tolerance:
        print(f"失败：最大帧间隔 {worst}s 明显超过设定 {args.seconds}s（+{tolerance}s 容差）")
        return 1
    print(f"通过：{len(jpegs)} 轮截屏，最大间隔 {worst}s ≈ 设定 {args.seconds}s（容差 {tolerance}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
