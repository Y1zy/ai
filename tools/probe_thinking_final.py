"""为新增的「中度思考 / 深度思考」两挡确定精确映射与 token 额度（最终定档实测）。

已知（前两轮实测）：
  - reasoning_effort 会真正触发推理（reasoning_content），基线为 0
  - 但推理会吃光 max_tokens：high 在 2048 / 4096 下正文均为 0 字（finish=length）
  - 只有抬到 8192 才出现正文

本工具补齐 low / medium 在 8192 下的数据（上一轮只测了 minimal 与 high），
并对每个候选跑多次取均值，用于把「中度」「深度」两挡映射到确定的参数值。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GATEWAY_URL = "http://127.0.0.1:7863/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"

# 与前面几轮同一道题，保证数据可横向比较
PROBLEM = (
    "给定 n 个区间 [li, ri]，要选出尽量多的区间使它们两两不重叠。"
    "有人说「每次选右端点最小的区间」这样贪心是对的，请判断这个说法是否正确，"
    "给出理由，并用一个具体反例或证明说明。"
)

# 统一用解题链路的最高档 token（8192），因为推理必须靠它兜住
MAX_TOKENS = 8192

# 候选：覆盖各档，用于最终定档
CANDIDATES: list[tuple[str, dict]] = [
    ("基线 auto（不发字段）", {}),
    ("reasoning_effort=low", {"reasoning_effort": "low"}),
    ("reasoning_effort=medium", {"reasoning_effort": "medium"}),
    ("reasoning_effort=high", {"reasoning_effort": "high"}),
]

REPEATS = 3


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
        "max_tokens": MAX_TOKENS,
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
    return {
        "status": 200,
        "content": message.get("content") or "",
        "reasoning": message.get("reasoning_content") or "",
        "finish": payload["choices"][0].get("finish_reason"),
        "tokens": (payload.get("usage") or {}).get("completion_tokens"),
        "seconds": seconds,
    }


def _verdict(content: str) -> str:
    """粗判答案方向（这题正确结论是「该贪心是对的」）。"""
    if "不正确" in content or "不成立" in content or "是错的" in content:
        return "否定（错）"
    has_explain = any(m in content for m in ("右端点", "排序", "交换", "归纳"))
    if any(m in content for m in ("正确", "对的", "成立", "有效")):
        return "对+论证" if has_explain else "对"
    return "无法判定"


def main() -> int:
    api_key = _api_key()
    print(f"题目：{PROBLEM[:44]}…")
    print(f"max_tokens 固定 {MAX_TOKENS}（解题链路上限档）· 每档 {REPEATS} 次\n")
    header = f"{'档位':<26} {'轮':>3} {'推理':>7} {'正文':>6} {'tok':>6} {'耗时':>7} {'finish':>7}  判定"
    print(header)
    print("-" * len(header))

    collected: dict[str, list[dict]] = {}
    for label, extra in CANDIDATES:
        rows = []
        for index in range(REPEATS):
            item = _ask(api_key, extra)
            reasoning_len = len(item.get("reasoning") or "")
            content = item.get("content") or ""
            rows.append(
                {
                    "reasoning_len": reasoning_len,
                    "content_len": len(content),
                    "tokens": item.get("tokens"),
                    "finish": item.get("finish"),
                    "seconds": round(item["seconds"], 1),
                    "verdict": _verdict(content),
                }
            )
            print(
                f"{label:<26} {index + 1:>3} {reasoning_len:>7} {len(content):>6} "
                f"{str(item.get('tokens')):>6} {item['seconds']:>6.1f}s {str(item.get('finish')):>7}  "
                f"{rows[-1]['verdict']}"
            )
        collected[label] = rows

    print("\n" + "=" * 104)
    print(f"{'档位':<26} {'推理均值':>9} {'正文均值':>9} {'耗时均值':>9}  正文非空  判定")
    for label, rows in collected.items():
        avg_r = sum(r["reasoning_len"] for r in rows) / len(rows)
        avg_c = sum(r["content_len"] for r in rows) / len(rows)
        avg_s = sum(r["seconds"] for r in rows) / len(rows)
        ok = "是" if avg_c > 0 else "否（空！）"
        verdicts = "/".join(sorted({r["verdict"] for r in rows}))
        print(f"{label:<26} {avg_r:>9.0f} {avg_c:>9.0f} {avg_s:>8.1f}s  {ok:<8}  {verdicts}")

    report = Path(__file__).resolve().parents[1] / ".runtime" / "thinking_probe_final.json"
    report.write_text(
        json.dumps(
            {"problem": PROBLEM, "max_tokens": MAX_TOKENS, "groups": collected},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n完整结果已写入 {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
