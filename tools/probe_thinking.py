"""实测本地网关支持哪些「思考强度」参数，为第四挡提供依据。

背景：代码里有一条踩坑记录——`reasoning_effort` 在本机网关上适得其反
（`none` 会让模型把全部 token 用于思考、正文返回 0 字）。因此加「思考强度」
挡位前必须先实测确认哪些参数真的可用，不能凭猜。

本工具对同一个网关、同一道题逐个参数各发一次请求，记录：
  - HTTP 状态码（是否被拒）
  - 正文长度（0 字即已知的失败模式）
  - 是否返回了推理内容字段（判断模型是否真的在思考）
  - 耗时（面试场景要在可接受范围）

逐个单测、不混合参数，否则分不清是哪个参数生效。

用法：python tools/probe_thinking.py
只访问本机 127.0.0.1 上的网关（项目配置里的地址），不访问外部服务。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 固定探测目标：本机网关（与 config.json 的 visionBaseUrl 一致）。
GATEWAY_URL = "http://127.0.0.1:7863/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"

# 同一道题，便于横向比较各参数的输出差异。
QUESTION = (
    "用一句话回答：为什么哈希表在平均情况下的查找是 O(1)，"
    "而最坏情况会退化到 O(n)？"
)

# 逐个候选参数：名称 → 要并入请求体的字段。
# 第一项是基线（不干预），最后几项是已知或可能失败的写法。
CANDIDATES: list[tuple[str, dict]] = [
    ("基线（不发字段，= 当前 auto）", {}),
    ("thinking disabled（= 当前 off）", {"thinking": {"type": "disabled"}}),
    ("thinking enabled", {"thinking": {"type": "enabled"}}),
    ("thinking enabled + budget 2048", {"thinking": {"type": "enabled", "budget_tokens": 2048}}),
    ("reasoning_effort=low", {"reasoning_effort": "low"}),
    ("reasoning_effort=medium", {"reasoning_effort": "medium"}),
    ("reasoning_effort=high", {"reasoning_effort": "high"}),
    ("reasoning_effort=minimal", {"reasoning_effort": "minimal"}),
    ("reasoning_effort=none（已知失败，用于复现）", {"reasoning_effort": "none"}),
    ("enable_thinking=true（DashScope 风格）", {"enable_thinking": True}),
    ("chat_template_kwargs.enable_thinking", {"chat_template_kwargs": {"enable_thinking": True}}),
    ("thinking_budget=2048", {"thinking_budget": 2048}),
]

# 响应里可能承载推理内容的字段名（不同网关叫法不同）。
REASONING_KEYS = (
    "reasoning_content",
    "reasoning",
    "thinking_content",
    "thinking",
    "analysis",
)


def _api_key() -> str:
    """用应用自己的解密逻辑取视觉模型 Key（与解题链路同一个 Key）。"""
    from system_audio_asr.phone_share import load_vision_key

    key = load_vision_key()
    if not key:
        raise SystemExit("未配置视觉模型 API Key（设置页填写后重试）")
    return key


def _post(api_key: str, extra: dict, timeout: float = 120.0) -> dict:
    """发一次非流式请求，返回观测结果。异常一律转成结果字典，便于并列对比。"""
    import httpx

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": QUESTION}],
        "max_tokens": 2048,
        "temperature": 0.2,
    }
    body.update(extra)

    started = time.monotonic()
    try:
        response = httpx.post(
            GATEWAY_URL,
            json=body,
            headers={"Authorization": "Bearer " + api_key},
            timeout=timeout,
        )
        seconds = time.monotonic() - started
    except Exception as exc:  # 网络/超时：记录原因而不是中断整轮
        return {
            "status": "EXC",
            "content_len": 0,
            "reasoning_len": 0,
            "seconds": round(time.monotonic() - started, 1),
            "note": f"{type(exc).__name__}: {exc}"[:160],
        }

    if response.status_code != 200:
        text = response.text[:200].replace("\n", " ")
        return {
            "status": response.status_code,
            "content_len": 0,
            "reasoning_len": 0,
            "seconds": round(seconds, 1),
            "note": text,
        }

    try:
        payload = response.json()
        message = payload["choices"][0]["message"]
    except Exception as exc:
        return {
            "status": 200,
            "content_len": 0,
            "reasoning_len": 0,
            "seconds": round(seconds, 1),
            "note": f"响应结构异常: {exc}"[:160],
        }

    content = message.get("content") or ""
    reasoning_total = 0
    reasoning_field = ""
    for key in REASONING_KEYS:
        value = message.get(key)
        if isinstance(value, str) and value:
            reasoning_total += len(value)
            reasoning_field = key
    usage = payload.get("usage") or {}
    return {
        "status": 200,
        "content_len": len(content),
        "reasoning_len": reasoning_total,
        "reasoning_field": reasoning_field,
        "completion_tokens": usage.get("completion_tokens"),
        "seconds": round(seconds, 1),
        "note": "",
        "content_head": content[:60].replace("\n", " "),
    }


def main() -> int:
    api_key = _api_key()
    print(f"网关: {GATEWAY_URL}")
    print(f"模型: {MODEL}")
    print(f"共 {len(CANDIDATES)} 个候选，逐个单测\n")
    print(f"{'参数':<44} {'状态':>5} {'正文':>6} {'推理':>6} {'耗时':>6}  备注")
    print("-" * 110)

    results: list[tuple[str, dict]] = []
    for label, extra in CANDIDATES:
        observed = _post(api_key, extra)
        results.append((label, observed))
        print(
            f"{label:<44} {str(observed['status']):>5} "
            f"{observed['content_len']:>6} {observed['reasoning_len']:>6} "
            f"{observed['seconds']:>5}s  {observed.get('note', '')[:40]}"
        )

    # 结论：哪些参数「可用」= 返回 200 且正文非空
    usable = [
        (label, item)
        for label, item in results
        if item["status"] == 200 and item["content_len"] > 0
    ]
    print("\n" + "=" * 110)
    if usable:
        print("可用参数（200 且正文非空）：")
        for label, item in usable:
            reasoning = item["reasoning_len"]
            tag = f"推理 {reasoning} 字（{item.get('reasoning_field')}）" if reasoning else "无推理字段"
            print(f"  - {label:<44} 正文 {item['content_len']} 字 · {tag}")
    else:
        print("没有可用参数：全部失败或正文为空。")

    baseline = dict(results)["基线（不发字段，= 当前 auto）"]
    print(
        f"\n基线对照：正文 {baseline['content_len']} 字 · "
        f"推理 {baseline['reasoning_len']} 字 · {baseline['seconds']}s"
    )
    report = Path(__file__).resolve().parents[1] / ".runtime" / "thinking_probe.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {"gateway": GATEWAY_URL, "model": MODEL, "results": [
                {"label": label, **item} for label, item in results
            ]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n完整结果已写入 {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
