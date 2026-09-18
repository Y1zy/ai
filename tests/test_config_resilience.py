"""config.json 抗损坏与跨端不丢数据的行为回归测试。

背景（真实事故）：C# OverlayConfig.Load() 此前是一个大 try/catch 里逐字段裸转，
任一字段类型不合法（手改配置、旧版本写入别的格式）会让整段 Load 抛错并被外层
catch 吞掉——该字段之后的所有字段一起退回空值，其中就包括 resumeContext（简历）。
设置窗关闭时 ApplyAllSettings() 把这些空值写回磁盘，用户简历被永久清空。
配置目录里遗留的 config.json.bak-reset-by-overlay-2236（resume 长度为 0）即
这类事故的痕迹。

本文件把 OverlayConfig 源码抽出来编译成独立探针做真实行为验证，而不是只看源码
文本。探针是纯配置类（不依赖 WPF 窗口），因此可脱离整个 OverlayApp 单独运行。

C# 侧行为由 tests/test_overlay_config_keys.py 的同族用例 + 本文件的源码契约用例
共同覆盖；本文件另外覆盖 Python 侧保存链路（网页设置页实际走的那条）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_OVERLAY_CS = _ROOT / "overlay_cs" / "OverlayApp.cs"
_PROBE = _ROOT / "tools" / "config_probe.py"

# 覆盖长期资料，且类型故意写坏：height/fontSize/maxLines/opacity 是垃圾字符串，
# locked/captureInvisible 不是布尔，aiMaxTokens 不可解析。
# 关键点：这些坏字段在配置里靠前，坏字段之后的简历等字段必须照常读出。
_DIRTY_CONFIG = {
    "height": "不是数字",
    "fontSize": "也不是",
    "maxLines": "x",
    "opacity": "y",
    "locked": "yes",
    "captureInvisible": "no",
    "aiMaxTokens": "abc",
    "aiSilenceSeconds": "nope",
    "resumeContext": "项目一：某医疗设备公司 | 医学影像软件",
    "jdContext": "目标职位 JD 文本",
    "targetCompany": "字节跳动",
    "extraContext": "附加背景",
    "aiSystemPrompt": "系统提示词",
    "aiOverridePrompt": "自定义覆盖提示词",
    "hotwordExtra": "Kubernetes, 微服务",
    "visionAnswerMode": "acm",
    "visionThinkingMode": "off",
    "visionMaxTokens": 4096,
    "width": 900,
    # 本版本 C# 不认识的键：Save() 必须原样保留
    "someFutureKey": "未来版本才有的值",
    "recordImageCap": 300,
}


@pytest.fixture(scope="module")
def config_probe():
    """加载 C# 探针（编译 + 运行封装见 tools/config_probe.py）。"""
    import sys

    sys.path.insert(0, str(_ROOT / "tools"))
    import config_probe as probe_module

    if not probe_module.available():
        pytest.skip(".NET Framework C# 编译器不存在，跳过 C# 行为测试")
    return probe_module


def test_dirty_field_does_not_wipe_later_fields(config_probe, tmp_path: Path) -> None:
    """单个坏字段不得殃及其后字段：简历必须照常读出（此前会整段回退为空）。"""
    config_probe.write_config(tmp_path, _DIRTY_CONFIG)
    got = config_probe.load(tmp_path)
    assert got["resumeContext"] == _DIRTY_CONFIG["resumeContext"], (
        "坏字段导致简历被清空 —— Load() 又整段回退了"
    )
    assert got["jdContext"] == _DIRTY_CONFIG["jdContext"]
    assert got["targetCompany"] == _DIRTY_CONFIG["targetCompany"]
    assert got["extraContext"] == _DIRTY_CONFIG["extraContext"]
    assert got["hotwordExtra"] == _DIRTY_CONFIG["hotwordExtra"]
    assert got["aiSystemPrompt"] == _DIRTY_CONFIG["aiSystemPrompt"]
    assert got["aiOverridePrompt"] == _DIRTY_CONFIG["aiOverridePrompt"]


def test_dirty_field_falls_back_only_for_itself(config_probe, tmp_path: Path) -> None:
    """坏字段自己回退默认值，同批的合法字段不受影响。"""
    config_probe.write_config(tmp_path, _DIRTY_CONFIG)
    got = config_probe.load(tmp_path)
    assert got["aiMaxTokens"] == 2048, "坏的长度档位应回退默认档"
    assert got["width"] == 900, "同一批里的合法字段不该被殃及"
    assert got["visionAnswerMode"] == "acm"
    assert got["visionThinkingMode"] == "off"
    assert got["visionMaxTokens"] == 4096


def test_save_preserves_unknown_keys(config_probe, tmp_path: Path) -> None:
    """Save() 用固定键表重写文件，但必须保留本版本不认识的键。"""
    target = config_probe.write_config(tmp_path, _DIRTY_CONFIG)
    config_probe.save(tmp_path)
    saved = json.loads(target.read_text(encoding="utf-8-sig"))
    assert saved.get("someFutureKey") == "未来版本才有的值", (
        "Save() 抹掉了未知键：跨版本会静默丢配置"
    )
    assert saved.get("recordImageCap") == 300
    assert saved.get("resumeContext") == _DIRTY_CONFIG["resumeContext"]


def test_save_then_load_roundtrip_keeps_resume(config_probe, tmp_path: Path) -> None:
    """保存后再读，简历仍在（锁定「保存一次就丢资料」的回归）。"""
    config_probe.write_config(tmp_path, _DIRTY_CONFIG)
    config_probe.save(tmp_path)
    got = config_probe.load(tmp_path)
    assert got["resumeContext"] == _DIRTY_CONFIG["resumeContext"]
    assert got["visionAnswerMode"] == "acm"
    assert got["visionMaxTokens"] == 4096


def test_real_shape_config_survives_every_field(config_probe, tmp_path: Path) -> None:
    """全字段合法的真实配置：Save() 后所有长期资料逐字不变。"""
    payload = {
        "left": 1554, "top": 449, "width": 793, "height": 510, "fontSize": 25,
        "maxLines": 3, "opacity": 0.98, "fadeDelayMs": 1800,
        "fontFamily": "Microsoft YaHei UI", "textColor": "#110d0d",
        "frameMode": "hover", "frameColor": "#7dbeff", "frameOpacity": 0.66,
        "locked": False, "captureInvisible": True, "screenName": "\\\\.\\DISPLAY6",
        "webSocketUrl": "ws://127.0.0.1:8765/ws", "asrLanguage": "zh",
        "liveTranslateEnabled": False, "aiEnabled": True,
        "aiModel": "deepseek-v4-flash", "aiMode": "auto", "aiThinkingMode": "off",
        "aiMaxTokens": 2048, "aiSilenceSeconds": 0.6, "aiSystemPrompt": "",
        "aiBaseUrl": "http://127.0.0.1:7863/v1", "aiOverridePrompt": "",
        "aiBuiltInPrompt": "把面试转写整理成回答。",
        "resumeContext": _DIRTY_CONFIG["resumeContext"],
        "jdContext": "", "targetCompany": "", "extraContext": "",
        "visionEnabled": True, "visionBaseUrl": "http://127.0.0.1:7863/v1",
        "visionModel": "deepseek-v4.1-flash", "solvePrompt": "",
        "visionThinkingMode": "", "visionMaxTokens": 2048,
        "visionAnswerMode": "core_code",
        "hotwordEnabled": True, "hotwordExtra": "",
    }
    target = config_probe.write_config(tmp_path, payload)
    config_probe.save(tmp_path)
    saved = json.loads(target.read_text(encoding="utf-8-sig"))
    for key, expected in payload.items():
        assert saved.get(key) == expected, f"字段 {key} 在保存后被改变"


def test_unparseable_file_does_not_raise(config_probe, tmp_path: Path) -> None:
    """整份 JSON 坏掉时也不能抛异常：退化为默认配置，程序仍要能启动。"""
    target = config_probe.config_file(tmp_path)
    target.write_text("{ 这不是合法 JSON ", encoding="utf-8")
    got = config_probe.load(tmp_path)
    assert got["resumeContext"] == ""
    assert got["width"] == 980


def test_missing_file_uses_defaults(config_probe, tmp_path: Path) -> None:
    """配置文件不存在时给默认值，不抛异常。"""
    got = config_probe.load(tmp_path)
    assert got["width"] == 980
    assert got["resumeContext"] == ""


# ---------------------------------------------------------------- 源码契约
# 上面是行为验证；这里锁定「不许退回旧写法」，防止以后有人把 Safe* 改回裸 Convert。


def _method_body(marker: str) -> str:
    """取出 C# 方法体：从签名行到「4 空格缩进的下一个成员」为止。

    用结构边界而不是固定字符数：方法体每加一个控件就会变长，写死长度会在加字段后
    误报（本文件早期用 4200 字符，加了两个下拉框后就扫不到 finally 了）。
    """
    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    start = text.index(marker)
    # 方法体在其后的第一个「4 空格缩进的收尾大括号」结束
    end = text.index("\n        }\n", start) + len("\n        }\n")
    return text[start:end]


def test_load_has_no_bare_conversions() -> None:
    """Load() 内不得出现裸 Convert.To*：单个坏字段会整段中止、清空其后的简历。"""
    body = _method_body("internal static OverlayConfig Load()")
    assert "Convert.To" not in body, (
        "Load() 又用了裸 Convert.To*：任一字段类型不合法会清空其后的所有字段（含简历）"
    )
    assert "SafeDouble(" in body and "SafeString(" in body and "SafeTokens(" in body


def test_save_merges_unknown_keys() -> None:
    """Save() 必须并入磁盘上的未知键，否则跨版本/网页端新增键会被抹掉。"""
    body = _method_body("internal void Save()")
    assert "MergeUnknownKeys(" in body, "Save() 未保留未知键：一次保存就会丢配置"


def test_sync_wraps_in_try_finally() -> None:
    """Sync() 必须用 try/finally 复位 syncing，否则一次异常会永久冻结控件更新。"""
    body = _method_body("internal void Sync(OverlayConfig config)")
    assert "try" in body and "finally" in body, "Sync() 未用 try/finally 保护 syncing"
    assert "syncing = false;" in body


def test_sync_fills_user_data_before_other_controls() -> None:
    """简历等用户手输资料必须先于其它控件填充。

    若排在后面，前面任一控件赋值抛错就会让简历框保持空白，而关闭设置窗时
    ApplyAllSettings() 会把空值写回磁盘 —— 永久抹掉用户资料。
    """
    body = _method_body("internal void Sync(OverlayConfig config)")
    resume_at = body.index("resumeBox.Text = config.ResumeContext;")
    for later in (
        "fontSizeBox.Text =",
        "aiPromptPreviewBox.Text =",
        "visionAnswerModeBox.SelectedIndex =",
    ):
        assert resume_at < body.index(later), (
            f"简历填充排在了 {later} 之后：前面控件出问题会让简历变空并写回磁盘"
        )
