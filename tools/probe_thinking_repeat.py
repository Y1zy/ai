"""对「思考强度」做重复采样的 A/B 实测，确认分级是否稳定（而非单次噪声）。

前一轮探测（tools/probe_thinking.py）发现：
  - reasoning_effort 的 graded 值会让网关返回 reasoning_content，基线为 0
  - reasoning_effort=none 反而产生大量推理（复现了代码注释里的老 bug）
  - thinking:{type:enabled} 不产生任何推理字段，等于没用

但单次采样可能只是输出长度波动。本工具用**真实算法题**重复采样，比较：
  - 推理字数、正文长度、耗时
  - 答案是否真的更对（人工/规则判定见 CHECK 说明）

用法：python tools/probe_thinking_repeat.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GATEWAY_URL = "http://127.0.0.1:7863/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"

# 真实使用场景：算法题（截图解题主要就是这类），需要多步推理才看得出差别。
# 题目刻意选有陷阱的：贪心看似可行但反例存在，只有真的推导才能发现。
PROBLEM = (
    "给定 n 个区间 [li, ri]，要选出尽量多的区间使它们两两不重叠。"
    "有人说「每次选右端点最小的区间」这样贪心是对的，请判断这个说法是否正确，"
    "给出理由，并用一个具体反例或证明说明。"
)

# 对比组：基线（不干预） vs 各级 reasoning_effort
GROUPS: list[tuple[str, dict]] = [
    ("基线 auto（不发字段）", {}),
    ("reasoning_effort=minimal", {"reasoning_effort": "minimal"}),
    ("reasoning_effort=high", {"reasoning_effort": "high"}),
]

REPEATS = 3

# 判定「答对」的规则：这题正确答案是「该贪心是对的」（按右端点最小选是经典正确解法）。
# 模型若答「对」并提到交换论证/按右端点排序，计为 correct。
CORRECT_MARKERS = ("正确", "对的", "成立", "是有效的", "可以")
EXPLAIN_MARKERS = ("右端点", "排序", "交换")


def _api_key() -> str:
    from system_audio_asr.phone_share import load_vision_key

    key = load_vision_key()
    if not key:
        raise SystemExit("未配置视觉模型 API Key")
    return key


def _ask(api_key: str, extra: dict) -> dict:
    import httpx

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROBLEM}],
        "max_tokens": 2048,
        "temperature": 0.2,
    }
    body.update(extra)
    started = time.monotonic()
    response = httpx.post(
        GATEWAY_URL,
        json=body,
        headers={"Authorization": "Bearer " + api_key},
        timeout=180.0,
    )
    seconds = time.monotonic() - started
    if response.status_code != 200:
        return {"status": response.status_code, "content": "", "reasoning": "", "seconds": seconds}
    message = response.json()["choices"][0]["message"]
    return {
        "status": 200,
        "content": message.get("content") or "",
        "reasoning": message.get("reasoning_content") or "",
        "seconds": seconds,
    }


def _verdict(content: str) -> str:
    """粗判定答案方向：说贪心对（正确）还是说错（错误）。"""
    has_explain = any(marker in content for marker in EXPLAIN_MARKERS)
    # 先排除否定语序
    if "不正确" in content or "不成立" in content or "是错的" in content:
        return "错（否定了正确解法）"
    if any(marker in content for marker in CORRECT_MARKERS):
        return "对" + ("+论证" if has_explain else "")
    return "无法判定"


def main() -> int:
    api_key = _api_key()
    print(f"题目：{PROBLEM[:50]}…")
    print(f"每组重复 {REPEATS} 次，共 {len(GROUPS) * REPEATS} 次调用\n")
    header = f"{'参数':<28} {'轮':>3} {'状态':>5} {'推理':>6} {'正文':>6} {'耗时':>6}  判定"
    print(header)
    print("-" * len(header))

    collected: dict[str, list[dict]] = {}
    for label, extra in GROUPS:
        rows = []
        for index in range(REPEATS):
            item = _ask(api_key, extra)
            reasoning_len = len(item.get("reasoning") or "")
            content = item.get("content") or ""
            verdict = _verdict(content)
            rows.append(
                {
                    "reasoning_len": reasoning_len,
                    "content_len": len(content),
                    "seconds": round(item["seconds"], 1),
                    "verdict": verdict,
                }
            )
            print(
                f"{label:<28} {index + 1:>3} {item['status']:>5} "
                f"{reasoning_len:>6} {len(content):>6} {item['seconds']:>5.1f}s  {verdict}"
            )
        collected[label] = rows

    print("\n" + "=" * 90)
    print(f"{'参数':<28} {'推理均值':>8} {'正文均值':>8} {'耗时均值':>8}  判定分布")
    for label, rows in collected.items():
        avg_reasoning = sum(r["reasoning_len"] for r in rows) / len(rows)
        avg_content = sum(r["content_len"] for r in rows) / len(rows)
        avg_seconds = sum(r["seconds"] for r in rows) / len(rows)
        verdicts = "、".join(r["verdict"] for r in rows)
        print(
            f"{label:<28} {avg_reasoning:>8.0f} {avg_content:>8.0f} {avg_seconds:>7.1f}s  {verdicts}"
        )

    report = Path(__file__).resolve().parents[1] / ".runtime" / "thinking_probe_repeat.json"
    report.write_text(
        json.dumps({"problem": PROBLEM, "groups": collected}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n完整结果已写入 {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
