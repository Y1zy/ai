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


# ---------------------------------------------------------------- 流式期间插入字幕
# 真实场景：AI 回答流式输出时面试官继续说话，会插入一条 final 字幕。
# 早期实现 _last_open 只检查最后一条，导致同一次回答被拆成两段、
# 未封口的前半段还会在落盘时被误标成「截图解题」。


def test_ai_stream_survives_interleaved_final() -> None:
    recorder = SessionRecorder()
    recorder.add_ai("前半段", False)
    recorder.on_event({"type": "final", "text": "面试官继续提问"})
    recorder.add_ai("前半段完整答案", True)

    stats = recorder.stats()
    assert stats["answers"] == 1, "回答被拆成了多条"
    assert stats["finals"] == 1
    entries = recorder._entries
    ai_entries = [e for e in entries if e["kind"] in ("ai", "ai_open")]
    assert len(ai_entries) == 1, "流式期间插入字幕导致回答被拆段"
    assert ai_entries[0]["text"] == "前半段完整答案", "快照未覆盖到同一条"


def test_solve_stream_survives_interleaved_final() -> None:
    recorder = SessionRecorder()
    recorder.on_event({"type": "solve_answer", "text": "答", "done": False})
    recorder.on_event({"type": "final", "text": "面试官说话"})
    recorder.on_event({"type": "solve_answer", "text": "答案全文", "done": True})

    stats = recorder.stats()
    assert stats["solves"] == 1
    assert stats["finals"] == 1
    solves = [e for e in recorder._entries if e["kind"] in ("solve", "solve_open")]
    assert len(solves) == 1


def test_unclosed_stream_renders_with_its_own_type(tmp_path) -> None:
    """未封口的流式条目要按来源渲染，不能掉进 else 被标成「截图解题」。"""
    recorder = SessionRecorder()
    recorder.add_ai("未封口的回答", False)          # 一直没有 done
    recorder.on_event({"type": "final", "text": "字幕"})

    recorder.save_to(tmp_path)
    saved = next(tmp_path.glob("*/transcript.md")).read_text(encoding="utf-8")
    timeline = saved.split("## 时间线")[1]
    assert "AI 回答" in timeline, "未封口的 AI 条目丢失或类型错误"
    assert "截图解题" not in timeline, "AI 条目被误标成截图解题"


def test_open_entry_expires_after_ttl(monkeypatch) -> None:
    """被取消的流式条目超过 TTL 后不再吸附后续内容。"""
    import system_audio_asr.recorder as rec

    recorder = SessionRecorder()
    recorder.add_ai("被取消的旧回答", False)
    # 把旧条目时间拨到 TTL 之外
    recorder._entries[0]["ts"] -= rec.OPEN_ENTRY_TTL_SECONDS + 10

    recorder.add_ai("新的回答", True)
    ai_entries = [e for e in recorder._entries if e["kind"] in ("ai", "ai_open")]
    assert len(ai_entries) == 2, "过期条目仍被复用"
    assert ai_entries[-1]["text"] == "新的回答"


def test_stats_count_unclosed_stream_entries() -> None:
    """未封口的条目也要计入统计，否则设置页数字与实际内容不符。"""
    recorder = SessionRecorder()
    recorder.add_ai("流式中", False)
    recorder.on_event({"type": "solve_answer", "text": "解题中", "done": False})
    stats = recorder.stats()
    assert stats["answers"] == 1
    assert stats["solves"] == 1
