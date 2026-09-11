"""热词提取测试：覆盖「手动补充中文热词被旧正则丢弃」的回归问题。"""
from system_audio_asr.recognizer import (
    _MAX_HOTWORDS,
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
    result = _get_hotwords({"hotwordExtra": "高并发，分布式锁、幂等;jvm"})
    for word in ("高并发", "分布式锁", "幂等", "jvm"):
        assert word in result


def test_manual_hotwords_priority_over_resume() -> None:
    """手动补充是用户显式指定，应排在简历自动提取之前。"""
    result = _get_hotwords(
        {"hotwordExtra": "幂等", "resumeContext": "熟悉 Kubernetes 与 Redis"}
    )
    assert result.index("幂等") < result.index("Kubernetes")


def test_chinese_phrase_not_split_by_inner_space() -> None:
    """中文词之间没有空格，不应把「高并发 场景」这类词组切开。"""
    assert _split_manual_hotwords("高并发 分布式 缓存") == ["高并发 分布式 缓存"]


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