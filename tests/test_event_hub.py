"""EventHub 健康状态语义：错误上报后会随服务恢复而清除。

早期 latest_error 一旦设置就永不清除，导致发生过一次瞬时错误之后
/health 永远返回 ok=false，启动脚本据此误判服务故障。
"""
from __future__ import annotations

import pytest

from system_audio_asr.server import EventHub


def test_error_cleared_when_service_reports_healthy_state() -> None:
    hub = EventHub()
    assert hub.latest_error is None

    hub.publish({"type": "error", "where": "recognizer", "message": "识别跟不上音频"})
    assert hub.latest_error is not None, "错误未被记录"

    hub.publish({"type": "status", "state": "capturing"})
    assert hub.latest_error is None, "服务已恢复但错误状态未清除"


@pytest.mark.parametrize("state", ["capturing", "model_ready", "paused"])
def test_all_healthy_states_clear_error(state: str) -> None:
    hub = EventHub()
    hub.publish({"type": "error", "where": "x", "message": "boom"})
    hub.publish({"type": "status", "state": state})
    assert hub.latest_error is None, f"{state} 应被视为健康状态"


@pytest.mark.parametrize("state", ["stopped", "loading_model", "switching_language"])
def test_non_healthy_states_keep_error(state: str) -> None:
    """停止/切换这类中间态不代表服务正常，不应清掉错误记录。"""
    hub = EventHub()
    hub.publish({"type": "error", "where": "x", "message": "boom"})
    hub.publish({"type": "status", "state": state})
    assert hub.latest_error is not None, f"{state} 不应清除错误"
