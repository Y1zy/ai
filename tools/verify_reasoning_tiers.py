"""端到端验证：推理档（medium/high）在本项目真实调用路径下正文非空。

背景：实测发现推理 token 计入 max_tokens，额度不足时正文返回 0 字
（high 在 2048/4096 下均为空）。本工具走**项目自己的** stream_chat_completion
与 apply_thinking_mode，确认「推理档 + 自动抬额度」这一组合真的能拿到正文，
而不是只验证了单独调用的 payload 拼装。

用法：python tools/verify_reasoning_tiers.py
只访问本机网关，不访问外部服务。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from system_audio_asr.ai_stream import stream_chat_completion  # noqa: E402
from system_audio_asr.phone_share import load_vision_config, load_vision_key  # noqa: E402

# 与截图解题同性质的题（需要多步推理），但用文字给出，便于自动化验证
PROBLEM = (
    "给定 n 个区间，要选出尽量多的互不重叠区间。"
    "有人主张「每次选右端点最小的区间」的贪心策略。请判断其对错并说明理由。"
)

# 对比「解题长度档位」的实际配置：当前设置是 4096，推理会吃光它
USER_MAX_TOKENS = 4096


def main() -> int:
    vision = load_vision_config()
    api_key = load_vision_key()
    if not api_key:
        raise SystemExit("未配置视觉模型 API Key")
    if not vision["baseUrl"]:
        raise SystemExit("未配置视觉模型 BaseURL")

    print(f"网关: {vision['baseUrl']}")
    print(f"模型: {vision['model']}")
    print(f"用户设置的解题长度: {USER_MAX_TOKENS}\n")

    for tier in ("off", "medium", "high"):
        chunks: list[str] = []
        started = time.monotonic()
        try:
            final = stream_chat_completion(
                url=vision["baseUrl"] + "/chat/completions",
                api_key=api_key,
                model=vision["model"],
                messages=[{"role": "user", "content": PROBLEM}],
                on_snapshot=lambda text, done: chunks.append(text),
                max_tokens=USER_MAX_TOKENS,
                temperature=0.2,
                validate=False,
                thinking_mode=tier,
            )
            seconds = time.monotonic() - started
            length = len(final or "")
            status = "正文非空 ✓" if length > 0 else "正文为空 ✗（推理吃光了额度）"
            print(f"{tier:>7}: {length:>5} 字 · {seconds:>5.1f}s · {status}")
            if length:
                print(f"         开头：{(final or '')[:70].replace(chr(10), ' ')}")
        except Exception as exc:
            print(f"{tier:>7}: 失败 {type(exc).__name__}: {exc}")

    print("\n说明：off/auto 不抬额度（保持用户设置），medium/high 自动抬到 8192。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
