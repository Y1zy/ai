"""面试记录器：跨场次残留与空推送的回归测试。

背景：C# Overlay 的 ResetConversation() 会调用 PhoneAiFeed.Post("", true) 作为
“取消/封口”信号。该空文本会经 /api/phone/ai 进入 recorder.add_ai()，若不加过滤
就会在内存记录里留下一条空回答，导致设置页「开始新一场」的防呆提示在每次
重置后仍然显示「本场已积累 1 条 AI 回答」。
"""

from __future__ import annotations

from system_audio_asr.recorder import SessionRecorder


def test_empty_ai_push_does_not_create_entry() -> None:
    """空文本（重置信号）不得新建记录条目。"""
    recorder = SessionRecorder()
    recorder.add_ai("", True)
    stats = recorder.stats()
    assert stats["answers"] == 0
    assert stats["has_content"] is False


def test_empty_ai_push_does_not_close_open_entry() -> None:
    """流式进行中收到空文本（取消）后，原条目保持打开，后续文本继续写入同一条。"""
    recorder = SessionRecorder()
    recorder.add_ai("第一段", False)
    recorder.add_ai("", True)          # 取消信号：忽略
    recorder.add_ai("第一段完整内容", True)
    stats = recorder.stats()
    assert stats["answers"] == 1, "空推送不应另起一条或提前封口"


def test_ai_push_accumulates_into_single_entry() -> None:
    """C# 推的是累计全量文本，多次推送应合并为一条。"""
    recorder = SessionRecorder()
    for chunk in ("你", "你可以", "你可以介绍一下项目"):
        recorder.add_ai(chunk, False)
    recorder.add_ai("你可以介绍一下项目", True)
    stats = recorder.stats()
    assert stats["answers"] == 1


def test_clear_empties_entries_and_images() -> None:
    """开始新一场后记录必须完全干净（has_content=False）。"""
    recorder = SessionRecorder()
    recorder.add_ai("一条回答", True)
    recorder.add_solve_image(b"\xff\xd8\xff\xe0fake-jpeg")
    assert recorder.stats()["has_content"] is True
    recorder.clear()
    stats = recorder.stats()
    assert stats["has_content"] is False
    assert stats["answers"] == 0 and stats["images"] == 0


def test_final_events_are_recorded_and_blank_ignored() -> None:
    recorder = SessionRecorder()
    recorder.on_event({"type": "final", "text": "面试官提问"})
    recorder.on_event({"type": "final", "text": "   "})
    assert recorder.stats()["finals"] == 1


def test_solve_answer_streaming_merges_into_one() -> None:
    recorder = SessionRecorder()
    recorder.on_event({"type": "solve_answer", "text": "答案", "done": False})
    recorder.on_event({"type": "solve_answer", "text": "答案全文", "done": True})
    assert recorder.stats()["solves"] == 1


def test_solve_snapshots_are_replaced_not_concatenated(tmp_path) -> None:
    """截图解题推的是累计全文快照，记录必须整体覆盖。

    若按增量累加，落盘内容会变成「答案答案全文」这类重复文本。
    """
    recorder = SessionRecorder()
    for snapshot in ("答", "答案", "答案全文"):
        recorder.on_event({"type": "solve_answer", "text": snapshot, "done": False})
    recorder.on_event({"type": "solve_answer", "text": "答案全文", "done": True})

    recorder.save_to(tmp_path)
    saved = next(tmp_path.glob("*/transcript.md")).read_text(encoding="utf-8")
    assert saved.count("答案全文") == 1
    assert "答案答案全文" not in saved, "快照被当成增量拼接了"


def test_solve_done_without_deltas_is_kept_whole() -> None:
    """没有流式增量的整条消息（如错误提示）应原样记录。"""
    recorder = SessionRecorder()
    recorder.on_event({"type": "solve_answer", "text": "解题失败：未启用", "done": True})
    assert recorder.stats()["solves"] == 1


def test_reset_then_new_ai_does_not_merge_into_old_entry() -> None:
    """重置后新一轮回答应另起一条，不能续写到上一场那条上。"""
    recorder = SessionRecorder()
    recorder.add_ai("上一场的回答", True)
    recorder.clear()
    recorder.add_ai("新一场的回答", True)
    stats = recorder.stats()
    assert stats["answers"] == 1
