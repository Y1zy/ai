"""验证：放大 max_tokens 能否让 reasoning_effort 的正文回来。

上一轮实测（tools/probe_thinking_repeat.py）发现：真实算法题下
reasoning_effort 会让推理吃光 max_tokens，正文返回 0 字。
  基线 auto    : 推理 0      正文 1320 字
   minimal      : 推理 3235   正文 0 字
   high         : 推理 4787   正文 0 字
（max_tokens=2048）

需要确认：把 max_tokens 放大到 8192（解题链路的上限档）后，正文是否恢复正常。
若能，第四挡就成立——但必须**同时保证足够的 token 额度**，否则用户一切到该挡就得到空回答。

用法：python tools/probe_thinking_verify.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GATEWAY_URL = "http://127.0.0.1:7863/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"

# 同一道需要多步推理的算法题（贪心正确性判断）
PROBLEM = (
    "给定 n 个区间 [li, ri]，要选出尽量多的区间使它们两两不重叠。"
    "有人说「每次选右端点最小的区间」这样贪心是对的，请判断这个说法是否正确，"
    "给出理由，并用一个具体反例或证明说明。"
)

# 对比：同一参数在不同 token 额度下的表现
GROUPS: list[tuple[str, dict, int]] = [
    ("基线 auto · 2048", {}, 2048),
    ("high · 2048（上轮失败的配置）", {"reasoning_effort": "high"}, 2048),
    ("high · 4096（解题链路默认档）", {"reasoning_effort": "high"}, 4096),
    ("high · 8192（解题链路上限档）", {"reasoning_effort": "high"}, 8192),
    ("minimal · 8192", {"reasoning_effort": "minimal"}, 8192),
]

REPEATS = 2


def _api_key() -> str:
    from system_audio_asr.phone_share import load_vision_key

    key = load_vision_key()
    if not key:
        raise SystemExit("未配置视觉模型 API Key")
    return key


def _ask(api_key: str, extra: dict, max_tokens: int) -> dict:
    import httpx

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROBLEM}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    body.update(extra)
    started = time.monotonic()
    response = httpx.post(
        GATEWAY_URL,
        json=body,
        headers={"Authorization": "Bearer " + api_key},
        timeout=300.0,
    )
    seconds = time.monotonic() - started
    if response.status_code != 200:
        return {"status": response.status_code, "content": "", "reasoning": "", "seconds": seconds}
    payload = response.json()
    message = payload["choices"][0]["message"]
    usage = payload.get("usage") or {}
    return {
        "status": 200,
        "content": message.get("content") or "",
        "reasoning": message.get("reasoning_content") or "",
        "seconds": seconds,
        "completion_tokens": usage.get("completion_tokens"),
        "finish": payload["choices"][0].get("finish_reason"),
    }


def main() -> int:
    api_key = _api_key()
    print(f"题目：{PROBLEM[:46]}…")
    header = f"{'配置':<34} {'轮':>3} {'推理':>6} {'正文':>6} {'tok':>6} {'耗时':>6}  finish"
    print(header)
    print("-" * len(header))

    collected: dict[str, list[dict]] = {}
    for label, extra, max_tokens in GROUPS:
        rows = []
        for index in range(REPEATS):
            item = _ask(api_key, extra, max_tokens)
            reasoning_len = len(item.get("reasoning") or "")
            content_len = len(item.get("content") or "")
            rows.append(
                {
                    "reasoning_len": reasoning_len,
                    "content_len": content_len,
                    "seconds": round(item["seconds"], 1),
                    "tokens": item.get("completion_tokens"),
                    "finish": item.get("finish"),
                }
            )
            print(
                f"{label:<34} {index + 1:>3} {reasoning_len:>6} {content_len:>6} "
                f"{str(item.get('completion_tokens')):>6} {item['seconds']:>5.1f}s  {item.get('finish')}"
            )
        collected[label] = rows

    print("\n" + "=" * 100)
    print(f"{'配置':<34} {'推理均值':>8} {'正文均值':>8} {'耗时均值':>8}  正文是否非空")
    for label, rows in collected.items():
        avg_reasoning = sum(r["reasoning_len"] for r in rows) / len(rows)
        avg_content = sum(r["content_len"] for r in rows) / len(rows)
        avg_seconds = sum(r["seconds"] for r in rows) / len(rows)
        ok = "是" if avg_content > 0 else "否（空回答！）"
        print(f"{label:<34} {avg_reasoning:>8.0f} {avg_content:>8.0f} {avg_seconds:>7.1f}s  {ok}")

    report = Path(__file__).resolve().parents[1] / ".runtime" / "thinking_probe_verify.json"
    report.write_text(
        json.dumps({"problem": PROBLEM, "groups": collected}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n完整结果已写入 {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
