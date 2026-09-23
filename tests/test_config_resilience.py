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
import re
import sys
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


def test_extreme_numbers_do_not_break_config_loading(tmp_path: Path) -> None:
    """极端数值不得让配置加载失败（1e400 会被 JSON 解析成 inf）。

    漏捕 OverflowError 的后果很重：异常穿透 load_settings（它只捕 ValueError/
    TypeError，而 OverflowError 不属于这两者），服务启动、设置页、识别取热词
    全部失败。涉及的字段都是档位类，回退到默认档即可。
    """
    from system_audio_asr import phone_share
    from system_audio_asr.settings import DEFAULTS, load_settings

    cases = [
        ("recordImageCap", "1e400"),
        ("recordImageCap", "-1e400"),
        ("recordImageCap", "Infinity"),
        ("recordImageCap", "NaN"),
        ("visionMaxImages", "1e400"),
        ("visionMaxImages", "-1e400"),
        ("visionMaxImages", "Infinity"),
    ]
    for key, raw in cases:
        config = tmp_path / f"{key}-{raw.replace('/', '_')}.json"
        config.write_text('{"%s": %s}' % (key, raw), encoding="utf-8")
        loaded = load_settings(config)  # 不得抛异常
        assert isinstance(loaded[key], int), f"{key}={raw} 未回退成整数"
        assert key in DEFAULTS, f"{key} 不在 DEFAULTS 里，用例已失效"

    # 解题张数走 phone_share 的独立入口，同样不能崩
    assert phone_share.normalize_max_solve_images(float("inf")) == 3
    assert phone_share.normalize_max_solve_images(-float("inf")) == 3
    assert phone_share.normalize_max_solve_images(float("nan")) == 3


def test_load_vision_config_survives_extreme_image_count(tmp_path: Path, monkeypatch) -> None:
    """解题链路的配置读取不能被极端值打断（手机配对/hello/解题都走它）。"""
    from system_audio_asr import phone_share

    config = tmp_path / "config.json"
    config.write_text('{"visionMaxImages": 1e400}', encoding="utf-8")
    monkeypatch.setattr(phone_share, "CONFIG_PATH", config)
    vision = phone_share.load_vision_config()  # 不得抛异常
    assert vision["maxImages"] == 3


def test_csharp_prose_cleanup_preserves_code() -> None:
    """C# 字幕窗：正文清理标记、代码段逐字保留（含 #include 的井号与缩进）。

    改用 AppendAiText 后正文的 ** / # / 反引号不再被清理，会原样显示给用户；
    但清理又不能伤到代码（`#include` 若被当标题前缀删掉，代码就废了）。
    这条用例调用 tools/prose_clean_probe.py：抽真实方法体编译运行后断言，
    而不是对源码做文本匹配。
    """
    import subprocess

    compiler_found = any(
        Path(candidate).exists()
        for candidate in (
            r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
            r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
        )
    )
    if not compiler_found:
        pytest.skip("未找到 .NET Framework C# 编译器")
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, str(root / "tools" / "prose_clean_probe.py")],
        capture_output=True,
        shell=False,
        check=False,
    )
    output = done.stdout.decode("utf-8", errors="replace") + done.stderr.decode(
        "utf-8", errors="replace"
    )
    assert done.returncode == 0, "C# 正文/代码分段检查失败：\n" + output


def test_load_has_no_bare_conversions() -> None:
    """Load() 内不得出现裸 Convert.To*：单个坏字段会整段中止、清空其后的简历。

    注意取的是带 out 参数的那个重载（真正的逐字段赋值体）；
    无参 Load() 只是转发，检查它没有意义。
    """
    body = _method_body("internal static OverlayConfig Load(out bool ok)")
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


def test_disk_state_is_unknown_when_file_unreadable(config_probe, tmp_path: Path) -> None:
    """文件读不到时必须能区分出来（Load(out ok) 的 ok=false）。

    这是「简历被写回默认值」的活路径：Load 契约是永不失败（失败返回全默认对象），
    而调用方把它当磁盘真值用 —— 读不到时以全默认为合并基线，就会把内存里的
    简历/JD 覆盖成空值并写回磁盘。实测 Python 的 os.replace 与 .NET 的
    File.ReadAllText 并发时 8 秒内出现 9 次读失败，不是理论情况。
    """
    config_probe.write_config(
        tmp_path, {"resumeContext": "项目一：某医疗设备公司", "aiEnabled": True}
    )
    target = config_probe.config_file(tmp_path)
    assert config_probe.load_disk_known(tmp_path) is True, "可读时应对调用方报告「已知」"

    # 独占打开模拟「Python 侧正在写入」：.NET 读会抛 IOException
    handle = open(target, "r+b")
    try:
        assert config_probe.load_disk_known(tmp_path) is False, (
            "文件被占用时仍报告「已读到」，调用方会拿全默认对象当合并基线"
        )
        # 被占用时返回的仍是可用的默认对象（不抛异常）——必须在释放之前读，
        # 否则测的是「文件正常可读」这条路，等于没覆盖读失败分支。
        fallback = config_probe.load(tmp_path)
        assert fallback["resumeContext"] == "", "读失败时应返回默认对象而不是抛异常"
    finally:
        handle.close()

    assert config_probe.load_disk_known(tmp_path) is True, "释放后应恢复为「已知」"


def test_missing_file_counts_as_known(config_probe, tmp_path: Path) -> None:
    """文件不存在 = 确实没有配置，算「已知」，不能和读失败混为一谈。

    否则全新安装（还没写过 config.json）时三方合并会被无谓地跳过。
    """
    assert config_probe.load_disk_known(tmp_path) is True


def test_source_aborts_when_disk_unreadable() -> None:
    """两个消费点都必须在「磁盘状态未知」时中止，而不是继续用默认值合并。"""
    source = _OVERLAY_CS.read_text(encoding="utf-8-sig")

    merge_start = source.index("private void MergeExternalChangesBeforeSave()")
    merge_body = source[merge_start : merge_start + 1400]
    assert "Load(out diskKnown)" in merge_body, "合并前未取「是否读到」标记"
    assert "if (!diskKnown) return;" in merge_body, (
        "读失败时没有中止：会把全默认对象当基线，简历被覆盖后写盘"
    )

    reload_start = source.index("private void ReloadConfigIfChanged()")
    reload_body = source[reload_start : reload_start + 1400]
    assert "Load(out freshKnown)" in reload_body, "热重载未取「是否读到」标记"
    assert "if (!freshKnown) return;" in reload_body, (
        "热重载读失败时没有中止：ApplyFrom(全默认) 会清空内存里的简历"
    )


def test_config_model_name_is_not_reset_to_preset() -> None:
    """自定义模型名不能被设置窗换成预设值。

    网页设置页允许任意模型名（自由文本），而 Sync() 此前写的是
    SelectedItem + 找不到就选 "deepseek-v4-flash"，于是开关一次设置窗
    就把 qwen-max 改成 deepseek-v4-flash，下次请求用错误的模型名发出。
    """
    body = _method_body("internal void Sync(OverlayConfig config)")
    assert "aiModelBox.Text = config.AiModel;" in body, (
        "Sync 未把模型名写进可编辑下拉的 Text（自定义值会丢）"
    )
    assert "aiModelBox.SelectedItem = config.AiModel;" not in body, (
        "Sync 仍在用 SelectedItem 填模型名：不在预设列表里的值会被换成预设值"
    )
    apply_body = _method_body("private void ApplyAllSettings()")
    assert "aiModelBox.Text" in apply_body, "ApplyAllSettings 未以 Text 为准"
    assert "aiModelBox.SelectedItem" not in apply_body, (
        "ApplyAllSettings 仍会回落到预设模型名"
    )


def test_nan_config_values_fall_back_to_defaults(config_probe, tmp_path: Path) -> None:
    """NaN 必须回落默认值；±Infinity 按边界夹紧（与 Python 同口径）。

    Math.Max/Min 遇 NaN 返回 NaN（不是夹紧），C# 会把它继续写盘成非法 JSON
    `{"opacity":NaN}`；此后 Python 渲染 /api/settings 直接 500，整个设置页不可用，
    C# 也会在 TimeSpan.FromSeconds(NaN) 抛 ArgumentException。
    Infinity 则不同：Math.Min(0.98, +∞)=0.98 本来就对，且 Python 的 max/min
    同样把 ±∞ 夹到边界，两端结果一致 —— 不要一律换成默认值。
    """
    config_probe.write_config(tmp_path, {"opacity": float("nan")})
    loaded = config_probe.load(tmp_path)
    assert loaded["opacityIsNaN"] is False, "opacity 仍是 NaN（会被写成非法 JSON）"
    # 必须等于该字段的默认值（与 Python DEFAULTS 一致），不是随手取的边界值
    assert loaded["opacityRaw"] == "0.88", f"实际={loaded['opacityRaw']}"

    config_probe.write_config(
        tmp_path, {"aiSilenceSeconds": float("inf"), "width": float("inf")}
    )
    loaded = config_probe.load(tmp_path)
    assert loaded["silenceRaw"] == "8", f"+∞ 未夹到上限: {loaded['silenceRaw']}"
    assert loaded["width"] == 2200.0, f"+∞ 未夹到上限: {loaded['width']}"

    config_probe.write_config(tmp_path, {"opacity": float("-inf"), "width": float("-inf")})
    loaded = config_probe.load(tmp_path)
    assert loaded["opacityRaw"] == "0.45", f"-∞ 未夹到下限: {loaded['opacityRaw']}"
    assert loaded["width"] == 280.0, f"-∞ 未夹到下限: {loaded['width']}"


def test_nan_fallback_values_match_python_defaults() -> None:
    """C# Normalize 里每个 Finite(x, fallback) 的 fallback 必须等于 Python DEFAULTS。

    写错一个字面量就会让同一份含 NaN 的配置在两端得到不同结果
    （Python 回默认 0.88、C# 回上界 0.98），表现为「设置页与悬浮窗显示不一致」。
    这类跨端数值没有类型系统兜底，只能靠表驱动断言守住。
    """
    sys.path.insert(0, str(_ROOT))
    from system_audio_asr.settings import DEFAULTS

    source = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    mapping = {
        "Width": "width",
        "Height": "height",
        "FontSize": "fontSize",
        "Opacity": "opacity",
        "FrameOpacity": "frameOpacity",
        "AiSilenceSeconds": "aiSilenceSeconds",
    }
    found = re.findall(r"(\w+) = Finite\((\w+), ([0-9.]+)\);", source)
    assert found, "未找到 Finite(...) 调用：NaN 兜底被移除了？"
    for field, _, fallback in found:
        key = mapping.get(field)
        assert key, f"Finite 用在了未登记的字段 {field}，请同步本测试的两端映射表"
        expected = float(DEFAULTS[key])
        assert abs(float(fallback) - expected) < 1e-9, (
            f"{field} 的 NaN 兜底是 {fallback}，Python DEFAULTS 是 {expected}（两端会不一致）"
        )

