"""热词提取测试：覆盖「手动补充中文热词被旧正则丢弃」的回归问题。"""
from system_audio_asr.recognizer import (
    _MAX_HOTWORDS,
    _MAX_HOTWORD_CHARS,
    _BASE_HOTWORDS,
    _extract_hotwords,
    _get_hotwords,
    _is_meaningful_zh,
    _split_manual_hotwords,
)


def test_manual_chinese_hotwords_are_kept() -> None:
    """旧实现用 [A-Za-z] 开头的正则，中文词会被整体丢弃。"""
    result = _get_hotwords({"hotwordExtra": "高并发"})
    assert "高并发" in result


def test_manual_chinese_hotwords_split_by_punctuation() -> None:
    """分隔符：换行 / 英文逗号 / 全角逗号 / 顿号。"""
    result = _get_hotwords({"hotwordExtra": "高并发，分布式锁、幂等\njvm"})
    for word in ("高并发", "分布式锁", "幂等", "jvm"):
        assert word in result


def test_long_term_is_kept_intact() -> None:
    """关键回归：完整术语绝不能被按字数切碎。

    早期实现曾按固定窗口切分，把「高并发缓存穿透」切成
    「高并发缓存穿」+「透」，碎片混入热词表会污染识别结果。
    """
    assert _split_manual_hotwords("高并发缓存穿透") == ["高并发缓存穿透"]
    assert _get_hotwords({"hotwordExtra": "高并发缓存穿透"}) == ["高并发缓存穿透"]


def test_semicolon_is_not_a_separator() -> None:
    """分号/竖线/制表符已从分隔符中移除，避免规则歧义。"""
    assert _split_manual_hotwords("高并发;分布式") == ["高并发;分布式"]
    assert _split_manual_hotwords("A|B") == ["A|B"]


def test_oversized_term_is_dropped_not_split() -> None:
    """超长条目直接丢弃（而非切碎），防误粘贴整篇简历。"""
    long_term = "测" * (_MAX_HOTWORD_CHARS + 8)
    assert _split_manual_hotwords(long_term) == []
    assert _get_hotwords({"hotwordExtra": long_term}) == _BASE_HOTWORDS


def test_term_at_limit_is_kept() -> None:
    exact = "测" * _MAX_HOTWORD_CHARS
    assert _split_manual_hotwords(exact) == [exact]


def test_dunhao_separates_terms() -> None:
    assert _split_manual_hotwords("高并发、分布式锁、幂等") == ["高并发", "分布式锁", "幂等"]


def test_manual_hotwords_priority_over_resume() -> None:
    """手动补充是用户显式指定，应排在简历自动提取之前。"""
    result = _get_hotwords(
        {"hotwordExtra": "幂等", "resumeContext": "熟悉 Kubernetes 与 Redis"}
    )
    assert result.index("幂等") < result.index("Kubernetes")


def test_chinese_ignores_spaces() -> None:
    """中文不按空格切：空格在中文里不是词边界，不能猜。

    用空格分隔的中文会被视为一个整体（宁可保留也不切碎）；
    英文则正常按空格切。
    """
    assert _split_manual_hotwords("高并发 分布式 缓存") == ["高并发 分布式 缓存"]
    assert _split_manual_hotwords("Redis Kafka MySQL") == ["Redis", "Kafka", "MySQL"]


def test_english_manual_hotwords_split_by_space() -> None:
    assert _split_manual_hotwords("Redis Kafka MySQL") == ["Redis", "Kafka", "MySQL"]


def test_chinese_stopwords_are_filtered() -> None:
    """简历里的「熟悉/开发/优化」等高频词不应进热词表。"""
    extracted = _extract_hotwords({"resumeContext": "熟悉 Qt 开发，负责优化性能"})
    for noise in ("熟悉", "开发", "负责", "优化"):
        assert noise not in extracted
    assert "Qt" in extracted


def test_is_meaningful_zh() -> None:
    assert _is_meaningful_zh("高并发") is True
    assert _is_meaningful_zh("熟悉") is False
    assert _is_meaningful_zh("") is False
    assert _is_meaningful_zh("12") is False


def test_hotword_limit_is_enforced() -> None:
    payload = {"hotwordExtra": ",".join(f"term{i}" for i in range(_MAX_HOTWORDS * 3))}
    assert len(_get_hotwords(payload)) <= _MAX_HOTWORDS


def test_base_hotwords_only_used_as_fallback() -> None:
    """有用户词表时不应再叠加内置方向词，避免干扰识别。"""
    with_user_words = _get_hotwords({"hotwordExtra": "幂等"})
    assert set(with_user_words) & set(_BASE_HOTWORDS) == set()
    assert _get_hotwords({}) == _BASE_HOTWORDS


def test_hotwords_are_deduplicated() -> None:
    result = _get_hotwords({"hotwordExtra": "Kafka,kafka,KAFKA"})
    assert [w.lower() for w in result].count("kafka") == 1

def test_zh_affixes_are_stripped() -> None:
    """「负责高并发系统优化」应剥离为「高并发」，而不是整段当作术语。"""
    from system_audio_asr.recognizer import _extract_zh_hotwords, _strip_zh_affixes

    assert _strip_zh_affixes("负责高并发系统优化") == "高并发"
    assert _extract_zh_hotwords("负责高并发系统优化") == ["高并发"]


def test_extract_zh_skips_pure_noise() -> None:
    from system_audio_asr.recognizer import _extract_zh_hotwords

    assert _extract_zh_hotwords("熟悉、掌握、了解") == []


def test_extract_zh_keeps_real_terms() -> None:
    from system_audio_asr.recognizer import _extract_zh_hotwords

    result = _extract_zh_hotwords("负责高并发系统优化，掌握分布式事务")
    assert "高并发" in result
    assert "分布式事务" in result
