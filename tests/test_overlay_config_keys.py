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

# 扫描 C# 方法体时的行数上限：仅作防御（防止哨兵行被误删后无限扫下去），
# 真实边界由哨兵行决定。切勿收窄——Save()/Load() 每加一个配置键就会变长，
# 上限卡太紧会在加键时报出「键集合不一致」的假故障（曾用 80 行，Load() 长到
# 83 行后误报 hotwordExtra 缺失）。
_SCAN_LIMIT = 400


def _scan_keys(method_marker: str, stop_marker: str, pattern: str) -> set[str]:
    """从 C# 源码扫出某个方法体里匹配 pattern 的键名（到 stop_marker 行为止）。"""
    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if method_marker in line:
            start = index
            break
    assert start is not None, f"未找到 {method_marker}"
    keys: set[str] = set()
    scanned = 0
    for line in lines[start:_SCAN_LIMIT + start]:
        keys.update(re.findall(pattern, line))
        scanned += 1
        if stop_marker in line:
            break
    assert scanned < _SCAN_LIMIT, (
        f"{method_marker} 的结束标记 {stop_marker!r} 超出扫描上限，"
        "请检查方法是否被误改，或调大 _SCAN_LIMIT"
    )
    return keys


def _csharp_saved_keys() -> set[str]:
    """解析 OverlayConfig.Save() 里 data["..."] = ... 写入的键集合。"""
    return _scan_keys("internal void Save()", "File.WriteAllText", r'data\["(\w+)"\]')


def test_csharp_save_covers_all_default_keys() -> None:
    keys = _csharp_saved_keys()
    assert keys, "未解析到任何 C# 保存字段"
    missing = sorted(set(DEFAULTS) - keys)
    assert not missing, f"C# Save() 会抹掉这些 Python 配置键: {missing}"


def test_csharp_load_reads_all_saved_keys() -> None:
    """Load() 能读回的键必须与 Save() 写出的键一致，否则重写会丢数据。"""
    loaded = _scan_keys(
        "internal static OverlayConfig Load()", "result.Normalize()", r'ContainsKey\("(\w+)"\)'
    )
    saved = _csharp_saved_keys()
    assert saved == loaded, f"Save/Load 键集合不一致: 仅 Save={sorted(saved - loaded)}, 仅 Load={sorted(loaded - saved)}"


def test_vision_thinking_levels_match_python() -> None:
    """C# 的解题思考档位表必须与 Python 一致（含「跟随」这一档）。

    两端各存一份档位表（跨语言无法共享）。C# 的下拉下标 ↔ 取值映射、以及
    Normalize 的白名单都依赖它：不一致会让某个档位在桌面端被重置成「跟随」，
    而界面上看不出异常。
    """
    from system_audio_asr.ai_stream import THINKING_MODES

    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    match = re.search(r"VisionThinkingLevels\s*=\s*\{([^}]*)\}", text)
    assert match, "未找到 C# VisionThinkingLevels 档位表"
    levels = re.findall(r'"([^"]*)"', match.group(1))
    # C# 表含「跟随」（空串），Python 的 THINKING_MODES 不含
    assert levels == [""] + list(THINKING_MODES), (
        f"档位表不一致: C#={levels} Python(含跟随)={[''] + list(THINKING_MODES)}"
    )
    # 下拉项数量必须与档位表一一对应，否则下标会错位
    items = re.findall(r"visionThinkingBox\.Items\.Add\(\"", text)
    assert len(items) == len(levels), (
        f"下拉项数({len(items)})与档位表({len(levels)})不匹配：下标会错位、选错档"
    )


def test_vision_thinking_save_uses_shared_levels() -> None:
    """加载与保存两个方向都必须走同一张档位表。

    各自写一份映射会漂移，后果是「选了深度思考却存成跟随」，而界面看起来正常。
    """
    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    usages = re.findall(r"OverlayConfig\.VisionThinkingLevels", text)
    assert len(usages) >= 3, (
        f"VisionThinkingLevels 只被用了 {len(usages)} 处，"
        "加载/保存/Normalize 三处都应使用它"
    )


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


# ---------------------------------------------------------------- 知识库接入
# 知识库此前只在 Python 侧拼装（设置页「回答测试」/手机追问生效），而字幕 AI 的
# 提示词由 C# 的 PromptForMode 拼装 —— 用户存的话术/FAQ 在字幕 AI 里永远不生效。
# 以下用例锁定「C# 必须读 knowledge.json」这一契约。


def test_csharp_prompt_includes_knowledge_feed() -> None:
    """PromptForMode 必须把知识库拼进上下文，否则该功能对字幕 AI 无效。"""
    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    assert "KnowledgeFeed.BuildContextBlock()" in text, (
        "PromptForMode 未接入知识库：知识库内容到不了字幕 AI"
    )


def test_csharp_knowledge_feed_is_readonly() -> None:
    """C# 只能读 knowledge.json，绝不能写 —— 否则与网页端互相覆盖。"""
    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    start = text.index("internal static class KnowledgeFeed")
    block = text[start:start + 4000]
    for writer in ("File.WriteAllText", "File.Replace", "File.Move", "File.Delete"):
        assert writer not in block, f"KnowledgeFeed 不应包含写操作: {writer}"


def test_csharp_knowledge_feed_matches_python_rules() -> None:
    """两端对知识库条目的读取规则必须一致（正文上限、启用开关）。"""
    from system_audio_asr.knowledge import MAX_CONTEXT_ENTRY_CHARS

    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    start = text.index("internal static class KnowledgeFeed")
    block = text[start:start + 4000]

    entry_limit = re.search(r"MaxEntryChars\s*=\s*(\d+)", block)
    assert entry_limit, "未找到 C# 知识库条目长度上限"
    assert int(entry_limit.group(1)) == MAX_CONTEXT_ENTRY_CHARS, (
        f"条目长度上限不一致: C#={entry_limit.group(1)} Python={MAX_CONTEXT_ENTRY_CHARS}"
    )
    # 启用开关：必须检查 enabled 字段，否则停用的条目也会被送入模型
    assert '"enabled"' in block, "C# 未检查 enabled 字段，停用的条目也会被送入模型"


def test_context_total_limit_matches_python() -> None:
    """上下文总长上限两端一致，避免 C# 侧无截断导致超长请求失败。"""
    from system_audio_asr.settings import _CONTEXT_TOTAL_LIMIT

    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    match = re.search(r"ContextTotalLimit\s*=\s*(\d+)", text)
    assert match, "C# 未定义 ContextTotalLimit"
    assert int(match.group(1)) == _CONTEXT_TOTAL_LIMIT, (
        f"总长上限不一致: C#={match.group(1)} Python={_CONTEXT_TOTAL_LIMIT}"
    )


def test_csharp_truncates_oversized_context() -> None:
    """上下文拼接必须走截断函数，而不是无条件全量拼接。"""
    text = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    start = text.index("internal static string PromptForMode")
    block = text[start:start + 3000]
    assert "JoinContextSections" in block, (
        "PromptForMode 未使用 JoinContextSections：长简历会撑爆模型上下文"
    )
