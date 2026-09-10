import json

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
