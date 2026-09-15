"""跨端配置一致性回归测试。

C# Overlay 与 Python 设置页共用 config.json。C# 的 OverlayConfig.Save()
用固定的字段列表整体重写文件，因此它写入的键集合必须覆盖 Python DEFAULTS
的全部键，否则 C# 每保存一次就会把 Python 独有的键（如 hotwordEnabled /
hotwordExtra）从磁盘抹掉。
"""
from __future__ import annotations

import re
from pathlib import Path

from system_audio_asr.settings import DEFAULTS

_OVERLAY_CS = Path(__file__).resolve().parents[1] / "overlay_cs" / "OverlayApp.cs"


def _csharp_saved_keys() -> set[str]:
    """解析 OverlayConfig.Save() 里 data["..."] = ... 写入的键集合。"""
    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if "internal void Save()" in line:
            start = index
            break
    assert start is not None, "未找到 OverlayConfig.Save()"
    keys: set[str] = set()
    for line in lines[start:start + 80]:
        keys.update(re.findall(r'data\["(\w+)"\]', line))
        if "File.WriteAllText" in line:
            break
    return keys


def test_csharp_save_covers_all_default_keys() -> None:
    keys = _csharp_saved_keys()
    assert keys, "未解析到任何 C# 保存字段"
    missing = sorted(set(DEFAULTS) - keys)
    assert not missing, f"C# Save() 会抹掉这些 Python 配置键: {missing}"


def test_csharp_load_reads_all_saved_keys() -> None:
    """Load() 能读回的键必须与 Save() 写出的键一致，否则重写会丢数据。"""
    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if "internal static OverlayConfig Load()" in line:
            start = index
            break
    assert start is not None, "未找到 OverlayConfig.Load()"
    loaded: set[str] = set()
    for line in lines[start:start + 80]:
        loaded.update(re.findall(r'ContainsKey\("(\w+)"\)', line))
        if "result.Normalize()" in line:
            break
    saved = _csharp_saved_keys()
    assert saved == loaded, f"Save/Load 键集合不一致: 仅 Save={sorted(saved - loaded)}, 仅 Load={sorted(loaded - saved)}"


def _csharp_max_token_levels() -> tuple[list[int], int]:
    """解析 OverlayApp.cs 的回答长度档位表与默认值。"""
    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    match = re.search(r"MaxTokenLevels\s*=\s*\{([^}]*)\}", text)
    assert match, "未找到 C# MaxTokenLevels 档位表"
    levels = [int(token) for token in re.findall(r"\d+", match.group(1))]
    default_match = re.search(r"DefaultMaxTokens\s*=\s*(\d+)", text)
    assert default_match, "未找到 C# DefaultMaxTokens"
    return levels, int(default_match.group(1))


def test_max_token_levels_match_between_csharp_and_python() -> None:
    """档位表跨语言各存一份，必须同步：只改一边会让同一配置在两端吸附出不同档位。

    （Python 侧: ai_stream.MAX_TOKENS_LEVELS / DEFAULT_MAX_TOKENS）
    """
    from system_audio_asr.ai_stream import DEFAULT_MAX_TOKENS, MAX_TOKENS_LEVELS

    csharp_levels, csharp_default = _csharp_max_token_levels()
    assert csharp_levels == list(MAX_TOKENS_LEVELS), (
        f"档位表不一致: C#={csharp_levels} Python={list(MAX_TOKENS_LEVELS)}"
    )
    assert csharp_default == DEFAULT_MAX_TOKENS, (
        f"默认档不一致: C#={csharp_default} Python={DEFAULT_MAX_TOKENS}"
    )
