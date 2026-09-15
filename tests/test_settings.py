import json

import pytest

from system_audio_asr import settings
from system_audio_asr.settings import normalize_settings, update_from_web


def test_overlay_height_is_bounded() -> None:
    assert normalize_settings({"height": 20})["height"] == 72
    assert normalize_settings({"height": 320})["height"] == 320
    assert normalize_settings({"height": 9999})["height"] == 800


def test_lock_state_is_normalized() -> None:
    assert normalize_settings({"locked": True})["locked"] is True
    assert normalize_settings({"locked": False})["locked"] is False


def test_font_size_accepts_manual_range() -> None:
    assert normalize_settings({"fontSize": 12})["fontSize"] == 12
    assert normalize_settings({"fontSize": 17})["fontSize"] == 17
    assert normalize_settings({"fontSize": 120})["fontSize"] == 96


def test_deepseek_translation_mode_is_preserved() -> None:
    assert normalize_settings({"aiMode": "translate"})["aiMode"] == "translate"
    assert normalize_settings({"aiMode": "translate_zh"})["aiMode"] == "auto"


def test_max_tokens_snaps_to_nearest_level() -> None:
    """回答长度上限吸附到最近档位；非法值回退默认 2048（与 C# 同规则）。"""
    assert normalize_settings({"aiMaxTokens": 512})["aiMaxTokens"] == 512
    assert normalize_settings({"aiMaxTokens": 3000})["aiMaxTokens"] == 2048
    assert normalize_settings({"aiMaxTokens": 99999})["aiMaxTokens"] == 8192
    assert normalize_settings({"aiMaxTokens": "bad"})["aiMaxTokens"] == 2048


def test_live_translation_flag_is_normalized() -> None:
    assert normalize_settings({"liveTranslateEnabled": True})["liveTranslateEnabled"] is True
    assert normalize_settings({"liveTranslateEnabled": False})["liveTranslateEnabled"] is False


def test_asr_language_is_normalized() -> None:
    assert normalize_settings({"asrLanguage": "en"})["asrLanguage"] == "en"
    assert normalize_settings({"asrLanguage": "invalid"})["asrLanguage"] == "zh"


def test_frame_appearance_is_normalized() -> None:
    assert normalize_settings({"frameMode": "always"})["frameMode"] == "always"
    assert normalize_settings({"frameMode": "bad"})["frameMode"] == "hover"
    assert normalize_settings({"frameOpacity": 2})["frameOpacity"] == 1.0
    assert normalize_settings({"frameColor": "invalid"})["frameColor"] == "#7DBEFF"


def test_overlay_context_keys_survive_normalize() -> None:
    """C# Overlay 写入的面试上下文键必须经过 normalize 后原样保留。"""
    payload = {
        "resumeContext": "五年 C++/Qt 桌面开发",
        "jdContext": "负责 Windows 客户端性能优化",
        "targetCompany": "Example Corp",
        "extraContext": "熟悉 COM/WASAPI",
        "captureInvisible": False,
    }
    result = normalize_settings(payload)
    for key, value in payload.items():
        assert result[key] == value, key


def test_web_save_keeps_csharp_context_keys(tmp_path) -> None:
    """网页设置页保存不能抹掉 C# 写入的简历/JD/采集开关（回归测试）。"""
    config_path = tmp_path / "config.json"
    original = {
        "resumeContext": "五年 C++/Qt 桌面开发",
        "jdContext": "Windows 客户端性能优化",
        "targetCompany": "Example Corp",
        "extraContext": "熟悉 COM/WASAPI",
        "captureInvisible": False,
    }
    config_path.write_text(
        json.dumps({**original, "aiEnabled": False}, ensure_ascii=False),
        encoding="utf-8",
    )
    saved = update_from_web({"settings": {"aiEnabled": True}}, path=config_path)
    assert saved["aiEnabled"] is True
    on_disk = json.loads(config_path.read_text(encoding="utf-8-sig"))
    for key, value in original.items():
        assert on_disk.get(key) == value, key


def _point_config_at(tmp_path, monkeypatch, payload: dict):
    """把 settings 模块整体指向临时配置。

    load_settings(path=CONFIG_PATH) 的默认值在函数定义时即绑定，只 patch CONFIG_PATH
    不会影响 builtin_prompts() 内部的 load_settings() 调用（它会读到开发者本机真实配置），
    因此这里必须连 load_settings 一起重定向，测试才真正隔离。
    """
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    original = settings.load_settings
    monkeypatch.setattr(settings, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        settings, "load_settings",
        lambda path=None, strict=False: original(config_path, strict=strict),
    )
    return config_path


def test_web_save_can_write_resume_and_clear_override(tmp_path) -> None:
    """网页设置页现在直接编辑简历/JD；清空旧「完全自定义提示词」必须真的落盘。"""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"resumeContext": "", "aiOverridePrompt": "旧简历全文"},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    saved = update_from_web(
        {"settings": {"resumeContext": "新简历", "jdContext": "岗位 JD",
                      "targetCompany": "示例公司", "extraContext": "工作三年",
                      "aiOverridePrompt": ""}},
        path=config_path,
    )
    assert saved["resumeContext"] == "新简历"
    assert saved["jdContext"] == "岗位 JD"
    assert saved["targetCompany"] == "示例公司"
    assert saved["extraContext"] == "工作三年"
    assert saved["aiOverridePrompt"] == ""
    on_disk = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert on_disk["aiOverridePrompt"] == ""


def test_clearing_override_restores_mode_prompt(tmp_path, monkeypatch) -> None:
    """清空旧自定义提示词后，生效提示词应回到带角色定义的模式模板，而不是只剩简历。"""
    _point_config_at(tmp_path, monkeypatch, {
        "resumeContext": "五年 C++/Qt", "aiOverridePrompt": "", "aiMode": "auto",
    })
    prompts = settings.builtin_prompts()
    assert prompts["overridePrompt"] is None
    auto = prompts["modes"]["auto"]
    assert "[Resume]" in auto and "五年 C++/Qt" in auto
    assert "面试辅助助手" in auto


def test_templates_field_is_context_free_default(tmp_path, monkeypatch) -> None:
    """templates 是「AI 自定义指令」框的默认文本：不含简历上下文和附加要求。"""
    _point_config_at(tmp_path, monkeypatch, {
        "resumeContext": "五年 C++/Qt", "aiSystemPrompt": "回答控制在 50 字内",
        "aiMode": "auto", "aiBuiltInPrompt": "",
    })
    prompts = settings.builtin_prompts()
    template = prompts["templates"]["auto"]
    assert "[Resume]" not in template and "五年 C++/Qt" not in template
    assert "附加要求" not in template and "回答控制在 50 字内" not in template
    assert "面试辅助助手" in template
    # modes 才是真正发送的内容：上下文与附加要求都在
    assert "[Resume]" in prompts["modes"]["auto"]
    assert "附加要求：回答控制在 50 字内" in prompts["modes"]["auto"]


def test_custom_builtin_prompt_replaces_all_modes(tmp_path, monkeypatch) -> None:
    """网页改过的内置提示词要替换默认模板并覆盖所有模式，与 C# PromptForMode 分支一致。"""
    _point_config_at(tmp_path, monkeypatch, {
        "resumeContext": "五年 C++/Qt", "aiBuiltInPrompt": "你只输出五个字以内的结论。",
        "aiMode": "auto",
    })
    prompts = settings.builtin_prompts()
    for mode, text in prompts["modes"].items():
        assert "你只输出五个字以内的结论。" in text, mode
        assert "直接输出纯文本，不要使用 Markdown" not in text, mode
        assert "[Resume]" in text and "五年 C++/Qt" in text, mode


# ---------------------------------------------------------------- 连接测试
# 三个调试入口（连接测试 / 回答测试 / 实际提示词）应与面试时的真实请求行为一致，
# 否则「测试通过」不代表面试时可用。


def _capture_connection_test_request(monkeypatch, mode: str) -> dict:
    """拦截底层 HTTP，返回连接测试实际发出的请求体。"""
    captured: list[dict] = []

    def fake_request(url, *, body, headers, timeout, method="POST"):
        captured.append(json.loads(body.decode("utf-8")))
        return 200, json.dumps({"choices": [{"message": {"content": "连接成功"}}]}).encode()

    monkeypatch.setattr(settings, "request_public_http", fake_request)
    monkeypatch.setattr(settings, "load_api_key", lambda path=None: "test-key")
    monkeypatch.setattr(
        settings, "load_settings",
        lambda path=None, strict=False: {
            "aiThinkingMode": mode, "aiModel": "m", "aiBaseUrl": "https://api.example/v1",
        },
    )
    result = settings.test_deepseek()
    assert result["ok"] is True
    assert captured, "未发出请求"
    return captured[0]


def test_connection_test_respects_thinking_off(monkeypatch) -> None:
    """选「关闭思考」时，连接测试也应发送 thinking=disabled（与面试路径一致）。"""
    sent = _capture_connection_test_request(monkeypatch, "off")
    assert sent["thinking"] == {"type": "disabled"}


def test_connection_test_omits_thinking_when_auto(monkeypatch) -> None:
    """auto 档不发送任何思考字段，保持模型默认行为。"""
    sent = _capture_connection_test_request(monkeypatch, "auto")
    assert "thinking" not in sent
    assert "reasoning_effort" not in sent


def test_connection_test_downgrades_when_thinking_rejected(monkeypatch) -> None:
    """网关不认 thinking 字段时自动重试一次，而不是误报 Key/地址错误。"""
    bodies: list[dict] = []

    def fake_request(url, *, body, headers, timeout, method="POST"):
        payload = json.loads(body.decode("utf-8"))
        bodies.append(payload)
        if "thinking" in payload:
            return 400, b'{"error":"unknown parameter thinking"}'
        return 200, json.dumps({"choices": [{"message": {"content": "连接成功"}}]}).encode()

    monkeypatch.setattr(settings, "request_public_http", fake_request)
    monkeypatch.setattr(settings, "load_api_key", lambda path=None: "test-key")
    monkeypatch.setattr(
        settings, "load_settings",
        lambda path=None, strict=False: {
            "aiThinkingMode": "off", "aiModel": "m", "aiBaseUrl": "https://api.example/v1",
        },
    )
    result = settings.test_deepseek()
    assert result["ok"] is True, "降级后应成功"
    assert len(bodies) == 2, "未触发降级重试"
    assert bodies[0]["thinking"] == {"type": "disabled"}
    assert "thinking" not in bodies[1]


def test_connection_test_keeps_fixed_512_max_tokens(monkeypatch) -> None:
    """连接测试语义是"能否连通"，不跟随回答长度档位（固定 512）。"""
    sent = _capture_connection_test_request(monkeypatch, "off")
    assert sent["max_tokens"] == 512


# ---------------------------------------------------------------- 回答长度档位（回答测试链路）
# 回答测试应走与面试时相同的 max_tokens 档位，否则"测试通过"不代表面试时可用。


def _capture_ask_request(monkeypatch, settings_payload: dict) -> dict:
    """拦截 _post_chat，返回「回答测试」实际发出的请求体。"""
    from system_audio_asr import server

    captured: list[dict] = []

    def fake_post(endpoint, request_body, api_key):
        captured.append(request_body)
        return 200, json.dumps(
            {"choices": [{"message": {"content": "回答"}}], "model": "m"}
        ).encode()

    monkeypatch.setattr(server, "_post_chat", fake_post)
    result = server._ask_ai_blocking(
        "系统提示词", "测一道题", {**settings.DEFAULTS, **settings_payload}, "test-key",
    )
    assert result["answer"] == "回答"
    assert captured, "未发出请求"
    return captured[0]


def test_reply_test_uses_configured_max_tokens(monkeypatch) -> None:
    sent = _capture_ask_request(monkeypatch, {"aiMaxTokens": 512})
    assert sent["max_tokens"] == 512


def test_reply_test_defaults_max_tokens_when_key_missing(monkeypatch) -> None:
    """旧配置没有 aiMaxTokens 键时回退 2048，不能因 None 报错。"""
    payload = {k: v for k, v in settings.DEFAULTS.items() if k != "aiMaxTokens"}
    sent = _capture_ask_request(monkeypatch, payload)
    assert sent["max_tokens"] == 2048


def _allow_local_file(tmp_path, monkeypatch, enabled: bool | None = None):
    """把 allow_local.json 指向 tmp 路径；enabled 非空时先写入。"""
    path = tmp_path / "allow_local.json"
    monkeypatch.setattr(settings, "ALLOW_LOCAL_PATH", path)
    if enabled is not None:
        settings.save_allow_local_endpoints(enabled, path)
    return path


def test_local_endpoints_blocked_by_default(tmp_path, monkeypatch) -> None:
    """默认（未勾选开关）本机回环与局域网地址必须被拒绝。"""
    _allow_local_file(tmp_path, monkeypatch)
    for url in (
        "http://127.0.0.1:11434/v1/chat/completions",
        "http://localhost:11434/v1/chat/completions",
        "http://192.168.1.10:8000/v1/chat/completions",
    ):
        with pytest.raises(ValueError, match="本机或内网"):
            settings.validate_public_http_url(url)


def test_local_endpoints_allowed_when_opted_in(tmp_path, monkeypatch) -> None:
    """显式开启后回环与局域网地址放行，供自建 Ollama / LM Studio 使用。"""
    _allow_local_file(tmp_path, monkeypatch, True)
    for url in (
        "http://127.0.0.1:11434/v1/chat/completions",
        "http://localhost:11434/v1/chat/completions",
        "http://192.168.1.10:8000/v1/chat/completions",
    ):
        assert settings.validate_public_http_url(url) == url


def test_link_local_metadata_stays_blocked_even_when_opted_in(tmp_path, monkeypatch) -> None:
    """云元数据端点（169.254.0.0/16）任何设置下都不放行。"""
    _allow_local_file(tmp_path, monkeypatch, True)
    with pytest.raises(ValueError, match="本机或内网"):
        settings.validate_public_http_url("http://169.254.169.254/latest/meta-data/")


def test_allow_local_flag_roundtrip(tmp_path, monkeypatch) -> None:
    """开关独立存盘：缺文件 / 内容损坏一律按「不允许」处理。"""
    path = _allow_local_file(tmp_path, monkeypatch)
    assert settings._allow_local_endpoints(path) is False
    settings.save_allow_local_endpoints(True, path)
    assert settings._allow_local_endpoints(path) is True
    path.write_text("{ 坏 JSON", encoding="utf-8")
    assert settings._allow_local_endpoints(path) is False
    settings.save_allow_local_endpoints(False, path)
    assert settings._allow_local_endpoints(path) is False


def test_allow_local_survives_overlay_config_rewrite(tmp_path, monkeypatch) -> None:
    """C# Overlay 整体重写 config.json 后开关必须仍然生效（不能存在 config.json 里）。"""
    _allow_local_file(tmp_path, monkeypatch, True)
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", config_path)
    # 模拟 C# 保存：只写它自己知道的键，不带 allowLocalEndpoints
    config_path.write_text(json.dumps({"aiEnabled": True}), encoding="utf-8")
    assert settings._allow_local_endpoints() is True


def test_public_settings_exposes_allow_local_flag(tmp_path, monkeypatch) -> None:
    """设置页需要读到开关状态才能回显勾选框。"""
    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    _allow_local_file(tmp_path, monkeypatch, True)
    assert settings.public_settings()["allowLocalEndpoints"] is True


def test_load_hotkey_info_reads_csharp_output(tmp_path) -> None:
    """热键实际生效组合由 C# 写盘，设置页据此显示而不是硬编码。

    真实踩过的坑：Ctrl+Alt+L 被别的软件占用后程序回退到 Ctrl+Shift+L，
    设置页却仍写着 Ctrl+Alt+L，用户按了没反应。
    """
    path = tmp_path / "hotkeys.json"
    path.write_text(
        json.dumps({
            "lockCombo": "Ctrl+Shift+L", "lockOk": True,
            "bossHotkeyCombo": "Ctrl+Alt+H", "bossHotkeyOk": True,
        }),
        encoding="utf-8",
    )
    info = settings.load_hotkey_info(path)
    assert info["lockCombo"] == "Ctrl+Shift+L"
    assert info["lockOk"] is True


def test_load_hotkey_info_missing_file_returns_empty(tmp_path) -> None:
    """Overlay 未运行时设置页回落为默认文案，不应报错。"""
    assert settings.load_hotkey_info(tmp_path / "absent.json") == {}


def test_load_hotkey_info_ignores_corrupt_and_non_scalar(tmp_path) -> None:
    """坏 JSON 与非标量值不能让设置页拿到垃圾数据。"""
    path = tmp_path / "hotkeys.json"
    path.write_text("{ not json", encoding="utf-8")
    assert settings.load_hotkey_info(path) == {}

    path.write_text(
        json.dumps({"lockCombo": "Ctrl+Alt+L", "nested": {"a": 1}, "num": 5, "ok": False}),
        encoding="utf-8",
    )
    info = settings.load_hotkey_info(path)
    assert info == {"lockCombo": "Ctrl+Alt+L", "ok": False}


def test_public_settings_includes_hotkeys(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    path = tmp_path / "hotkeys.json"
    path.write_text(json.dumps({"lockCombo": "Ctrl+Shift+L"}), encoding="utf-8")
    monkeypatch.setattr(settings, "HOTKEY_PATH", path)
    assert settings.public_settings()["hotkeys"]["lockCombo"] == "Ctrl+Shift+L"


# ---------------------------------------------------------------- 坏字段隔离
# 早期任一字段强转失败都会让 load_settings 整体回退 DEFAULTS，设置页表现为
# 「所有配置丢失」（磁盘其实完好），保存也会失败并给出"文件正在更新"的错误解释。


def test_bad_field_does_not_discard_other_fields(tmp_path, monkeypatch) -> None:
    """单字段类型不合法只回退该字段，其他字段必须保留。"""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"width": "abc", "hotwordExtra": "Redis", "resumeContext": "我的简历"}),
        encoding="utf-8",
    )
    loaded = settings.load_settings(path)
    assert loaded["width"] == settings.DEFAULTS["width"], "坏字段未回退默认值"
    assert loaded["hotwordExtra"] == "Redis", "好字段被牵连丢失"
    assert loaded["resumeContext"] == "我的简历"


def test_multiple_bad_fields_are_isolated(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"width": None, "height": "x", "maxLines": [], "fontSize": 30}),
        encoding="utf-8",
    )
    loaded = settings.load_settings(path)
    assert loaded["width"] == settings.DEFAULTS["width"]
    assert loaded["height"] == settings.DEFAULTS["height"]
    assert loaded["maxLines"] == settings.DEFAULTS["maxLines"]
    assert loaded["fontSize"] == 30, "未损坏的字段不应被牵连"


def test_truncated_config_still_reports_busy(tmp_path) -> None:
    """文件被读到半个（正在写入）仍是可重试的暂态失败，文案指向"正在更新"。"""
    path = tmp_path / "config.json"
    path.write_text('{"width": 1000, "hei', encoding="utf-8")
    with pytest.raises(RuntimeError, match="正在更新"):
        settings.load_settings(path, strict=True)


def test_save_accepts_settings_after_bad_field(tmp_path) -> None:
    """坏字段存在时保存不应整体失败。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"width": "abc", "hotwordExtra": "Redis"}), encoding="utf-8")
    saved = settings.update_from_web({"settings": {"aiEnabled": True}}, path=path)
    assert saved["aiEnabled"] is True
    assert saved["hotwordExtra"] == "Redis", "保存时把好字段弄丢了"
