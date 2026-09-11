"""知识库测试：条目 CRUD、启用过滤、文档解析与提示词集成。"""
import json

import pytest

from system_audio_asr import knowledge as kb
from system_audio_asr.settings import overlay_context_block


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """每个测试用独立 knowledge.json，避免污染真实用户数据。"""
    path = tmp_path / "knowledge.json"
    monkeypatch.setattr(kb, "KNOWLEDGE_PATH", path)
    return path


def test_upsert_adds_entry(store) -> None:
    entry = kb.upsert_entry({"title": "自我介绍稿", "content": "你好"})
    assert entry["title"] == "自我介绍稿"
    assert entry["enabled"] is True
    assert len(kb.load_entries()) == 1


def test_upsert_updates_existing_without_duplicating(store) -> None:
    first = kb.upsert_entry({"title": "A", "content": "1"})
    kb.upsert_entry({"id": first["id"], "title": "A2", "content": "2"})
    entries = kb.load_entries()
    assert len(entries) == 1
    assert entries[0]["title"] == "A2"


def test_delete_entry(store) -> None:
    entry = kb.upsert_entry({"title": "A", "content": "1"})
    kb.delete_entry(entry["id"])
    assert kb.load_entries() == []


def test_empty_content_is_dropped(store) -> None:
    kb.save_entries([{"title": "blank", "content": "   "}])
    assert kb.load_entries() == []


def test_title_fallback(store) -> None:
    entry = kb.upsert_entry({"content": "只有内容"})
    assert entry["title"] == "未命名条目"


def test_context_block_skips_disabled(store) -> None:
    kb.save_entries(
        [
            {"title": "on", "content": "AAA", "enabled": True},
            {"title": "off", "content": "BBB", "enabled": False},
        ]
    )
    block = kb.build_context_block(kb.load_entries())
    assert "AAA" in block
    assert "BBB" not in block


def test_context_block_respects_total_limit(store) -> None:
    kb.save_entries([{"title": "big", "content": "x" * 5000}])
    block = kb.build_context_block(kb.load_entries(), limit=100)
    assert len(block) <= 200  # 首条允许少量超出后即停止


def test_context_block_truncates_long_entry(store) -> None:
    kb.save_entries([{"title": "long", "content": "y" * (kb.MAX_CONTEXT_ENTRY_CHARS + 500)}])
    block = kb.build_context_block(kb.load_entries())
    assert "…" in block
    assert len(block) < kb.MAX_CONTEXT_ENTRY_CHARS + 200


def test_load_entries_survives_corrupt_file(store) -> None:
    store.write_text("{ not json", encoding="utf-8")
    assert kb.load_entries() == []


def test_save_is_atomic_and_readable(store) -> None:
    kb.save_entries([{"title": "t", "content": "c"}])
    raw = json.loads(store.read_text(encoding="utf-8"))
    assert raw["entries"][0]["content"] == "c"


def test_parse_txt() -> None:
    assert kb.parse_document("你好 World".encode("utf-8"), "a.txt").strip() == "你好 World"


def test_parse_markdown() -> None:
    assert "标题" in kb.parse_document("# 标题\n正文".encode("utf-8"), "a.md")


def test_parse_rejects_unsupported_suffix() -> None:
    with pytest.raises(RuntimeError):
        kb.parse_document(b"data", "a.exe")


def test_parse_rejects_empty_file() -> None:
    with pytest.raises(RuntimeError):
        kb.parse_document(b"", "a.txt")


def test_parse_docx_includes_table_cells() -> None:
    docx = pytest.importorskip("docx")
    import io

    document = docx.Document()
    document.add_paragraph("五年 C++ 经验")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "段位"
    table.rows[0].cells[1].text = "字节跳动"
    buffer = io.BytesIO()
    document.save(buffer)
    text = kb.parse_document(buffer.getvalue(), "resume.docx")
    assert "五年 C++ 经验" in text
    assert "字节跳动" in text


def test_overlay_context_block_includes_knowledge(store, monkeypatch, tmp_path) -> None:
    """知识库内容必须出现在 AI 系统提示词的上下文块中。"""
    kb.save_entries([{"title": "KB", "content": "KB_MARKER_XYZ", "enabled": True}])
    import system_audio_asr.settings as settings

    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    block = overlay_context_block()
    assert "KB_MARKER_XYZ" in block
    assert "[KB]" in block


def test_knowledge_not_written_into_config_json(store, monkeypatch, tmp_path) -> None:
    """方案 B 的核心保证：知识库独立存储，不写 config.json（避免被 C# 重写抹掉）。"""
    config_path = tmp_path / "config.json"
    import system_audio_asr.settings as settings

    monkeypatch.setattr(settings, "CONFIG_PATH", config_path)
    kb.upsert_entry({"title": "KB", "content": "KB_MARKER_XYZ"})
    overlay_context_block()
    if config_path.exists():
        assert "KB_MARKER" not in config_path.read_text(encoding="utf-8")

def test_corrupt_docx_raises_runtime_error() -> None:
    """损坏的 DOCX 必须转换为 RuntimeError，否则接口层会返回 500 而不是 400。"""
    with pytest.raises(RuntimeError):
        kb.parse_document(b"not a zip file", "broken.docx")


def test_corrupt_pdf_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        kb.parse_document(b"not a real pdf", "broken.pdf")


def test_container_content_is_rejected() -> None:
    """列表/字典等容器类型不能经 str() 变成垃圾内容。"""
    assert kb._normalize_entry({"title": "t", "content": ["a", "b"]}) is None
    assert kb._normalize_entry({"title": "t", "content": {"k": "v"}}) is None


def test_scalar_content_is_coerced() -> None:
    entry = kb._normalize_entry({"title": 123, "content": 456})
    assert entry is not None
    assert entry["content"] == "456"


def test_title_is_truncated(store) -> None:
    entry = kb.upsert_entry({"title": "T" * 500, "content": "c"})
    assert len(entry["title"]) == kb.MAX_TITLE_CHARS


def test_content_is_truncated(store) -> None:
    entry = kb.upsert_entry({"title": "t", "content": "c" * (kb.MAX_CONTENT_CHARS + 1000)})
    assert len(entry["content"]) == kb.MAX_CONTENT_CHARS


def test_entry_count_limit(store) -> None:
    kb.save_entries([{"title": f"t{i}", "content": "c"} for i in range(kb.MAX_ENTRIES + 50)])
    assert len(kb.load_entries()) == kb.MAX_ENTRIES


def test_public_entries_includes_char_count(store) -> None:
    kb.upsert_entry({"title": "t", "content": "hello"})
    assert kb.public_entries()[0]["chars"] == 5