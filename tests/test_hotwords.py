"""热词提取测试：覆盖「手动补充中文热词被旧正则丢弃」的回归问题。"""
from system_audio_asr.recognizer import (
    _MAX_HOTWORDS,
    _MAX_HOTWORD_CHARS,
    _MAX_ZH_HOTWORDS,
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


def test_describe_hotwords_reports_sources() -> None:
    """设置页要把「全部生效热词」展示出来，必须能区分来源。"""
    from system_audio_asr.recognizer import describe_hotwords

    info = describe_hotwords(
        {"hotwordExtra": "幂等", "resumeContext": "熟悉 Kubernetes 与 Redis"}
    )
    by_word = {entry["word"]: entry["source"] for entry in info["words"]}
    assert by_word["幂等"] == "manual"
    assert by_word["Kubernetes"] == "resume"
    assert info["enabled"] is True
    assert info["limits"] == {"total": _MAX_HOTWORDS, "zh": _MAX_ZH_HOTWORDS}


def test_describe_hotwords_matches_get_hotwords() -> None:
    """设置页展示的词表必须与实际送入模型的一字不差（共用同一套选择逻辑）。"""
    from system_audio_asr.recognizer import describe_hotwords

    settings = {"hotwordExtra": "幂等,Kubernetes", "resumeContext": "熟悉 Redis 与 Qt"}
    shown = [entry["word"] for entry in describe_hotwords(settings)["words"]]
    assert shown == _get_hotwords(settings)


def test_describe_hotwords_reports_dropped_over_limit() -> None:
    """超上限而未生效的词要如实报出，用户才知道「加了为什么不生效」。"""
    from system_audio_asr.recognizer import describe_hotwords

    payload = {"hotwordExtra": ",".join(f"term{i}" for i in range(_MAX_HOTWORDS + 25))}
    info = describe_hotwords(payload)
    assert len(info["words"]) == _MAX_HOTWORDS
    assert len(info["dropped"]) == 25
    assert {entry["source"] for entry in info["dropped"]} == {"manual"}


def test_describe_hotwords_chinese_limit_reports_dropped() -> None:
    """中文名额（_MAX_ZH_HOTWORDS）被占满时，多余中文词也算「未生效」。"""
    from system_audio_asr.recognizer import describe_hotwords

    payload = {"hotwordExtra": ",".join(f"术语{i}" for i in range(_MAX_ZH_HOTWORDS + 5))}
    info = describe_hotwords(payload)
    zh = [e for e in info["words"] if any("\u4e00" <= c <= "\u9fa5" for c in e["word"])]
    assert len(zh) == _MAX_ZH_HOTWORDS
    assert len(info["dropped"]) >= 5


def test_describe_hotwords_disabled_flag_is_reported() -> None:
    """关闭热词纠正时词表仍返回（便于编辑），但要标明当前不生效。"""
    from system_audio_asr.recognizer import describe_hotwords

    info = describe_hotwords({"hotwordExtra": "幂等", "hotwordEnabled": False})
    assert info["enabled"] is False
    assert any(entry["word"] == "幂等" for entry in info["words"])


def test_describe_hotwords_fallback_source_is_builtin() -> None:
    """三层全空时回落到内置词库，来源应标为 builtin。"""
    from system_audio_asr.recognizer import describe_hotwords

    info = describe_hotwords({})
    assert info["words"], "兜底词表不应为空"
    assert {entry["source"] for entry in info["words"]} == {"builtin"}
    assert len(info["words"]) == len(_BASE_HOTWORDS)

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


# ---------------------------------------------------------------- 提取阶段不丢词
# 早期 _extract_hotwords 内部先截断一轮，被它丢掉的词根本没机会进入
# _select_hotwords 的统计，设置页「超出上限」提示远少于实际丢弃数量。


def test_extraction_does_not_truncate() -> None:
    """提取阶段不做数量截断：上限判断集中在一处，才统计得准。"""
    payload = {"resumeContext": ", ".join(f"Term{i}" for i in range(_MAX_HOTWORDS + 40))}
    extracted = _extract_hotwords(payload)
    assert len(extracted) == _MAX_HOTWORDS + 40, "提取阶段提前截断了"


def test_every_candidate_is_either_kept_or_reported_dropped() -> None:
    """每个候选词都要有归属：要么生效，要么出现在 dropped 里。"""
    from system_audio_asr.recognizer import describe_hotwords

    en = ", ".join(f"Term{i}" for i in range(_MAX_HOTWORDS + 30))
    payload = {"resumeContext": en}
    candidates = _extract_hotwords(payload)
    info = describe_hotwords(payload)
    kept = {e["word"] for e in info["words"]}
    dropped = {e["word"] for e in info["dropped"]}
    missing = set(candidates) - kept - dropped
    assert not missing, f"这些词被静默丢弃且未报告: {sorted(missing)[:5]}"
    assert len(kept) == _MAX_HOTWORDS
    assert len(dropped) == len(candidates) - _MAX_HOTWORDS


# ---------------------------------------------------------------- 碎片过滤
# 历史版本曾把设置页展示用的「全部生效热词」整体回写，使简历长句被标点截断后的
# 碎片固化成手动词（「保证各步骤仅在安全联锁条」「像素数据解码与」），
# 它们占满中文配额（40）把真术语挤出去。以下用例锁定过滤规则。


def test_zh_fragment_with_dangling_particle_is_rejected() -> None:
    """以连词/助词结尾的片段是整句被标点截断的残留，不能当热词。"""
    from system_audio_asr.recognizer import _is_meaningful_zh

    for fragment in ("像素数据解码与", "体素坐标与", "实时读取的", "切片导航及"):
        assert not _is_meaningful_zh(fragment), f"碎片未被过滤: {fragment}"


def test_zh_long_sentence_residue_is_dropped() -> None:
    """超过 6 字的整段汉字视为句子残留，直接丢弃（不切碎）。"""
    from system_audio_asr.recognizer import _extract_zh_hotwords

    result = _extract_zh_hotwords("保证各步骤仅在安全联锁条模式下按序执行完毕")
    for term in result:
        assert len(term) <= 6, f"超长碎片未被拦截: {term!r}"


def test_zh_real_terms_survive_fragment_filter() -> None:
    """过滤规则不能误伤真术语（这是配额能否落到实处的关键）。"""
    from system_audio_asr.recognizer import _extract_zh_hotwords

    for term in ("多平面重建", "连通域分析", "窗宽窗位", "橡皮擦", "智能指针", "观察者模式"):
        assert _extract_zh_hotwords(term) == [term], f"真术语被误杀: {term}"


def test_narrative_common_words_are_stopwords() -> None:
    """简历叙述性常用词（描述/面向/通过…）不应进热词表。"""
    from system_audio_asr.recognizer import _is_meaningful_zh

    for word in ("描述", "面向", "通过", "机制", "主要", "读取"):
        assert not _is_meaningful_zh(word), f"叙述词未被过滤: {word}"


# ---------------------------------------------------------------- 长串与标点


def test_long_chinese_run_is_not_sliced() -> None:
    """整段连续汉字不能被切成碎片混进热词表。

    早期用 {2,12} 定长窗口，会把「熟练掌握高并发分布式系统设计与优化能力」
    从中间切断，产生「高并发分布式系统设计」这类 7-12 字碎片：它们不是术语，
    却占满中文配额（40），把真术语挤出去。

    现策略（_ZH_TERM_MAX_CHARS=6）：剥离前后缀与悬空虚词后仍超过 6 字的整段
    直接丢弃——宁可漏收也不塞碎片。中文真术语（高并发 / 分布式锁 / 多平面重建 /
    连通域分析）都在 6 字内。
    """
    from system_audio_asr.recognizer import _extract_zh_hotwords

    result = _extract_zh_hotwords("熟练掌握高并发分布式系统设计与优化能力")
    for term in result:
        assert len(term) <= 6, f"出现超长碎片: {term!r}"
        assert not term.startswith("描述"), f"出现切片碎片: {term!r}"
    # 剥离逻辑本身不能被削弱：能落回术语长度的照常收
    assert _extract_zh_hotwords("负责高并发系统优化") == ["高并发"]


def test_overlong_chinese_run_is_dropped_not_sliced() -> None:
    """剥离后仍超长的整句直接丢弃，绝不切碎。"""
    from system_audio_asr.recognizer import _extract_zh_hotwords

    sentence = "这句话明显是一个完整的句子而不是术语" * 2
    assert _extract_zh_hotwords(sentence) == []


def test_english_hotword_trailing_punctuation_stripped() -> None:
    result = _extract_hotwords({"resumeContext": "using Redis. Kafka and docker-compose."})
    assert "Redis" in result
    assert "Redis." not in result
    assert "docker-compose" in result, "词内连字符应保留"
    assert "using" not in result and "and" not in result, "停用词应过滤"


def test_clean_en_hotword_keeps_inner_symbols() -> None:
    from system_audio_asr.recognizer import _clean_en_hotword

    assert _clean_en_hotword("Redis.") == "Redis"
    assert _clean_en_hotword("Node.js") == "Node.js"
    assert _clean_en_hotword("C++") == "C++"
