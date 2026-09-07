"""面试记录器：字幕 final / AI 回答 / 解题回答与题图的内存暂存与落盘。

设计：面试期间零磁盘写入（每条一次 list.append，对识别管线无感）；
设置页点「保存到本地」时一次性写入 records/<时间戳>/ 文件夹。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

MAX_ENTRIES = 3000
MAX_IMAGES = 50


class SessionRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 条目 kind：final（字幕定稿）/ ai_open→ai（流式期间为 open）/ solve_open→solve
        self._entries: list[dict] = []
        self._images: list[dict] = []  # {ts, name, data}

    # ------------------------------------------------------------------ 捕获
    def on_event(self, event: dict) -> None:
        """EventHub 监听器：捕获字幕 final 与截图解题回答（增量流）。"""
        kind = str(event.get("type", ""))
        if kind == "final":
            text = str(event.get("text", "")).strip()
            if text:
                self._add("final", text)
        elif kind == "solve_answer":
            self._add_solve(str(event.get("text", "")), bool(event.get("done")))

    def add_ai(self, text: str, done: bool) -> None:
        """C# 每次推送的是累计全量文本；done=True 封口。"""
        with self._lock:
            entry = self._last_open("ai_open")
            if entry is None:
                entry = {"ts": time.time(), "kind": "ai_open", "text": ""}
                self._entries.append(entry)
            entry["text"] = text
            if done:
                entry["kind"] = "ai"
            self._trim_locked()

    def add_solve_image(self, jpeg: bytes) -> None:
        """截图解题触发时存题图（内存，环形上限）。"""
        if not jpeg:
            return
        with self._lock:
            stamp = datetime.now().strftime("%H%M%S")
            name, n = f"{stamp}.jpg", 1
            while any(img["name"] == name for img in self._images):
                name = f"{stamp}_{n}.jpg"
                n += 1
            self._images.append({"ts": time.time(), "name": name, "data": jpeg})
            while len(self._images) > MAX_IMAGES:
                self._images.pop(0)

    # ------------------------------------------------------------------ 状态
    def stats(self) -> dict:
        with self._lock:
            finals = sum(1 for e in self._entries if e["kind"] == "final")
            answers = sum(1 for e in self._entries if e["kind"] == "ai")
            solves = sum(1 for e in self._entries if e["kind"] == "solve")
            oldest = self._entries[0]["ts"] if self._entries else None
        return {
            "finals": finals,
            "answers": answers,
            "solves": solves,
            "images": len(self._images),
            "has_content": bool(self._entries or self._images),
            "since": datetime.fromtimestamp(oldest).strftime("%H:%M:%S") if oldest else None,
        }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._images.clear()

    # ------------------------------------------------------------------ 落盘
    def save_to(self, root: Path) -> dict:
        md = self._markdown()
        with self._lock:
            images = [(img["name"], img["data"]) for img in self._images]
        folder = root / datetime.now().strftime("%Y%m%d-%H%M%S")
        (folder / "images").mkdir(parents=True, exist_ok=True)
        (folder / "transcript.md").write_text(md, encoding="utf-8")
        for name, data in images:
            (folder / "images" / name).write_bytes(data)
        return {"folder": str(folder), "images": len(images)}

    def _markdown(self) -> str:
        with self._lock:
            entries = list(self._entries)
            images = list(self._images)

        blocks: list[dict] = []
        for entry in entries:
            kind = entry["kind"]
            if kind == "final" and blocks and blocks[-1]["kind"] == "final":
                blocks[-1]["text"] += "\n" + entry["text"]
            else:
                blocks.append({"ts": entry["ts"], "kind": kind, "text": entry["text"]})

        used_images: set[str] = set()
        lines = [
            f"# VoxRibbon 面试记录 · {datetime.now().strftime('%Y-%m-%d')}",
            "",
            f"> 保存时间 {datetime.now().strftime('%H:%M:%S')} · "
            f"字幕 {sum(1 for b in blocks if b['kind'] == 'final')} 段 · "
            f"AI 回答 {sum(1 for b in blocks if b['kind'] == 'ai')} 条 · "
            f"截图解题 {sum(1 for b in blocks if b['kind'] == 'solve')} 次",
            "",
            "## 时间线",
            "",
        ]
        for block in blocks:
            clock = datetime.fromtimestamp(block["ts"]).strftime("%H:%M:%S")
            if block["kind"] == "final":
                lines.append(f"**{clock}**")
                lines.append("")
                lines.append(block["text"])
            elif block["kind"] == "ai":
                lines.append(f"**{clock} · AI 回答**")
                lines.append("")
                lines.append(block["text"])
            else:
                lines.append(f"**{clock} · 截图解题**")
                lines.append("")
                image = self._match_image(block["ts"], images, used_images)
                if image is not None:
                    lines.append(f"![题目](images/{image})")
                    lines.append("")
                lines.append(block["text"])
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _match_image(ts: float, images: list[dict], used: set[str]) -> str | None:
        for image in images:
            if image["name"] in used:
                continue
            if image["ts"] <= ts + 10:
                used.add(image["name"])
                return image["name"]
            break
        return None

    # ------------------------------------------------------------------ 内部
    def _add(self, kind: str, text: str) -> None:
        with self._lock:
            self._entries.append({"ts": time.time(), "kind": kind, "text": text})
            self._trim_locked()

    def _add_solve(self, text: str, done: bool) -> None:
        with self._lock:
            entry = self._last_open("solve_open")
            if entry is None:
                if not text.strip():
                    return
                if done:
                    # 无前续增量的完整消息（如「解题失败：…」）
                    self._entries.append({"ts": time.time(), "kind": "solve", "text": text})
                    self._trim_locked()
                    return
                entry = {"ts": time.time(), "kind": "solve_open", "text": ""}
                self._entries.append(entry)
            if done:
                if text.strip():
                    entry["text"] = text  # 流结束时推送全文
                entry["kind"] = "solve"
            else:
                entry["text"] += text
            self._trim_locked()

    def _last_open(self, kind: str) -> dict | None:
        last = self._entries[-1] if self._entries else None
        return last if (last is not None and last["kind"] == kind) else None

    def _trim_locked(self) -> None:
        while len(self._entries) > MAX_ENTRIES:
            self._entries.pop(0)


session_recorder = SessionRecorder()
