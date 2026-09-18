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
# 题图环形缓冲的默认张数（实际值由配置 recordImageCap 决定，见 image_cap()）。
# 单张实测约 137 KB（1600px / 质量 75，见 phone_share.MAX_IMAGE_WIDTH/JPEG_QUALITY），
# 200 张约 27 MB，对面试机的内存占用可接受；早期硬编码 50 张在一场长面试里
# （尤其算法题连续截图）会明显不够。
MAX_IMAGES = 200
# 未封口的流式条目（ai_open / solve_open）超过这个时长就视为已失效，
# 不再接收新快照：否则被取消的请求会把后续无关内容吸附进同一条。
OPEN_ENTRY_TTL_SECONDS = 300


def image_cap() -> int:
    """当前生效的题图保留张数上限。

    读配置而不是用模块常量：用户可以在设置页调（100/200/300/500）。
    读配置失败时回退默认值——记录功能绝不能因为配置坏掉而不可用。
    """
    try:
        from .settings import load_settings

        value = int(load_settings().get("recordImageCap") or MAX_IMAGES)
    except Exception:
        return MAX_IMAGES
    return max(1, value)


class SessionRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 条目 kind：final（字幕定稿）/ ai_open→ai（流式期间为 open）/ solve_open→solve
        self._entries: list[dict] = []
        self._images: list[dict] = []  # {ts, name, data, batch}
        # 题图批次号：一次解题（可能多张）共用一个，落盘时据此归组。
        self._image_batch = 0

    # ------------------------------------------------------------------ 捕获
    def on_event(self, event: dict) -> None:
        """EventHub 监听器：捕获字幕 final 与截图解题回答（流式快照）。"""
        kind = str(event.get("type", ""))
        if kind == "final":
            text = str(event.get("text", "")).strip()
            if text:
                self._add("final", text)
        elif kind == "solve_answer":
            self._add_solve(str(event.get("text", "")), bool(event.get("done")))

    def add_ai(self, text: str, done: bool) -> None:
        """C# 每次推送的是累计全量文本；done=True 封口。

        文本为空即视为"取消/重置"信号（C# ResetConversation 会 Post("", true)）：
        此时不能新建条目，否则每次重置都会在记录里留下一条空回答。
        """
        if not text.strip():
            return
        with self._lock:
            entry = self._find_open("ai_open")
            if entry is None:
                entry = {"ts": time.time(), "kind": "ai_open", "text": ""}
                self._entries.append(entry)
            entry["text"] = text
            if done:
                entry["kind"] = "ai"
            self._trim_locked()

    def add_solve_image(self, jpeg: bytes) -> None:
        """单张题图（桌面热键路径）：自成一批。"""
        self.add_solve_images([jpeg])

    def add_solve_images(self, images: list[bytes]) -> None:
        """一次解题提交的多张题图：共用同一个批次号。

        批次号是落盘时把「多张图归到同一条解题记录」的依据。不能只靠时间窗：
        两次解题挨得近时（用户连点两题），后一题的图也落在前一题的时间窗内，
        会被错配到前一题。
        """
        payload = [image for image in images if image]
        if not payload:
            return
        cap = image_cap()
        with self._lock:
            self._image_batch += 1
            batch = self._image_batch
            for jpeg in payload:
                stamp = datetime.now().strftime("%H%M%S")
                name, n = f"{stamp}.jpg", 1
                while any(img["name"] == name for img in self._images):
                    name = f"{stamp}_{n}.jpg"
                    n += 1
                self._images.append(
                    {"ts": time.time(), "name": name, "data": jpeg, "batch": batch}
                )
            while len(self._images) > cap:
                self._images.pop(0)

    # ------------------------------------------------------------------ 状态
    def stats(self) -> dict:
        with self._lock:
            finals = sum(1 for e in self._entries if e["kind"] == "final")
            answers = sum(1 for e in self._entries if _base_kind(e["kind"]) == "ai")
            solves = sum(1 for e in self._entries if _base_kind(e["kind"]) == "solve")
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
        # 未封口的流式条目按其来源归位（AI / 解题），而不是掉进 else 被标成「截图解题」。
        for block in blocks:
            block["kind"] = _base_kind(block["kind"])

        used_images: set[str] = set()
        solve_total = sum(1 for b in blocks if b["kind"] == "solve")
        solve_with_image = 0
        body: list[str] = []
        for block in blocks:
            clock = datetime.fromtimestamp(block["ts"]).strftime("%H:%M:%S")
            if block["kind"] == "final":
                body.append(f"**{clock}**")
                body.append("")
                body.append(block["text"])
            elif block["kind"] == "ai":
                body.append(f"**{clock} · AI 回答**")
                body.append("")
                body.append(block["text"])
            else:
                body.append(f"**{clock} · 截图解题**")
                body.append("")
                matched = self._match_images(block["ts"], images, used_images)
                if matched:
                    solve_with_image += 1
                    for name in matched:
                        body.append(f"![题目](images/{name})")
                    body.append("")
                body.append(block["text"])
            body.append("")

        header = [
            f"# VoxRibbon 面试记录 · {datetime.now().strftime('%Y-%m-%d')}",
            "",
            f"> 保存时间 {datetime.now().strftime('%H:%M:%S')} · "
            f"字幕 {sum(1 for b in blocks if b['kind'] == 'final')} 段 · "
            f"AI 回答 {sum(1 for b in blocks if b['kind'] == 'ai')} 条 · "
            f"截图解题 {solve_total} 次",
            "",
        ]
        # 题图配不上时（超出环形缓冲上限、或时间窗内没有对应截图）明写出来：
        # 否则用户保存后只看到某条解题没图，不知道是被上限挤掉的。
        missing = solve_total - solve_with_image
        if missing > 0:
            header.append(
                f"> ⚠ 本次有 {missing} 次解题未附带题图"
                f"（题图仅保留最近 {image_cap()} 张，更早的已滚出缓冲；"
                "可在设置页调大「题图保留张数」）"
            )
            header.append("")
        lines = header + ["## 时间线", ""] + body
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _match_images(ts: float, images: list[dict], used: set[str]) -> list[str]:
        """取出一条解题记录对应的题图（可能多张，按批次归组）。

        先找时间窗内最早那张未用过的图，再把它所属批次的图整批取走——
        多图一题时几张图属于同一次解题，必须一起落地，不能只放一张。
        批次缺失（旧数据/单张路径）时退化为只取这一张。
        """
        head: dict | None = None
        for image in images:
            if image["name"] in used:
                continue
            if image["ts"] <= ts + 10:
                head = image
            break
        if head is None:
            return []

        batch = head.get("batch")
        if batch is None:
            used.add(head["name"])
            return [head["name"]]
        matched: list[str] = []
        for image in images:
            if image.get("batch") != batch or image["name"] in used:
                continue
            used.add(image["name"])
            matched.append(image["name"])
        return matched

    # ------------------------------------------------------------------ 内部
    def _add(self, kind: str, text: str) -> None:
        with self._lock:
            self._entries.append({"ts": time.time(), "kind": kind, "text": text})
            self._trim_locked()

    def _add_solve(self, text: str, done: bool) -> None:
        """截图解题流：text 始终是累计全文快照（与桌面/手机气泡的替换式渲染一致），
        因此这里必须整体覆盖，不能累加——否则会把每次快照重复拼接。"""
        with self._lock:
            entry = self._find_open("solve_open")
            if entry is None:
                if not text.strip():
                    return
                if done:
                    # 无前续快照的完整消息（如「解题失败：…」）
                    self._entries.append({"ts": time.time(), "kind": "solve", "text": text})
                    self._trim_locked()
                    return
                entry = {"ts": time.time(), "kind": "solve_open", "text": ""}
                self._entries.append(entry)
            if text.strip():
                entry["text"] = text
            if done:
                entry["kind"] = "solve"
            self._trim_locked()

    def _find_open(self, kind: str) -> dict | None:
        """从尾部向前找最近的未封口条目。

        不能只看最后一条：AI/解题流式期间常有字幕 final 插进来，那时最后一条
        是字幕，只看最后一条会重新开一条，导致同一次回答被拆成两段、
        未封口条目还会在落盘时被误标成「截图解题」。
        超过 TTL 的陈旧 open 条目视为已失效，避免被取消的请求吸附后续内容。
        """
        cutoff = time.time() - OPEN_ENTRY_TTL_SECONDS
        for entry in reversed(self._entries):
            if entry["kind"] == kind:
                return entry if entry["ts"] >= cutoff else None
        return None

    def _trim_locked(self) -> None:
        while len(self._entries) > MAX_ENTRIES:
            self._entries.pop(0)


def _base_kind(kind: str) -> str:
    """把流式期间的临时类型归到最终类型，供统计与渲染统一处理。"""
    if kind in ("ai", "ai_open"):
        return "ai"
    if kind in ("solve", "solve_open"):
        return "solve"
    return kind


session_recorder = SessionRecorder()
