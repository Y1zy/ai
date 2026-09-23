"""多图截图解题与题图保留上限的行为测试。

场景来源：算法题的题干、约束、样例常分散在多屏，单张截图会漏掉条件，
模型按错误的题意作答。手机端因此支持「➕ 加一图」攒图 + 一次性提交。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from system_audio_asr import phone_share, recorder
from system_audio_asr.phone_share import (
    DEFAULT_MAX_SOLVE_IMAGES,
    MAX_SOLVE_IMAGES_LIMIT,
)
from system_audio_asr.settings import (
    DEFAULTS,
    RECORD_IMAGE_CAP_LEVELS,
    normalize_record_image_cap,
    normalize_settings,
)


class _StopAutoLoop(Exception):
    """测试专用：让 auto_loop 在第一轮等待处退出，不必真的睡满间隔。"""


class TestMaxSolveImagesConfig:
    def test_default_is_three(self) -> None:
        assert DEFAULTS["visionMaxImages"] == 3
        assert DEFAULT_MAX_SOLVE_IMAGES == 3

    def test_normalizes_to_supported_range(self) -> None:
        assert normalize_settings({"visionMaxImages": 0})["visionMaxImages"] == 1
        assert normalize_settings({"visionMaxImages": 99})["visionMaxImages"] == MAX_SOLVE_IMAGES_LIMIT
        assert normalize_settings({"visionMaxImages": 4})["visionMaxImages"] == 4
        assert normalize_settings({"visionMaxImages": "x"})["visionMaxImages"] == 3

    def test_load_vision_config_exposes_limit(self, tmp_path: Path, monkeypatch) -> None:
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"visionMaxImages": 5}), encoding="utf-8")
        monkeypatch.setattr(phone_share, "CONFIG_PATH", config)
        assert phone_share.load_vision_config()["maxImages"] == 5

    def test_helper_clamps(self) -> None:
        assert phone_share.normalize_max_solve_images(None) == DEFAULT_MAX_SOLVE_IMAGES
        assert phone_share.normalize_max_solve_images(10) == MAX_SOLVE_IMAGES_LIMIT
        assert phone_share.normalize_max_solve_images(-3) == 1


class TestFindBugTask:
    """找 Bug 与解题是两件事：走同一套截图/流式通道，但用不同的内置提示词。"""

    def test_normalize_solve_task(self) -> None:
        assert phone_share.normalize_solve_task("bug") == "bug"
        assert phone_share.normalize_solve_task("BUG") == "bug"
        for bad in ("", None, "solve", "review", "找bug"):
            assert phone_share.normalize_solve_task(bad) in ("solve", "bug"), bad
        # 非 bug 一律回退解题（行为零变化）
        assert phone_share.normalize_solve_task("nonsense") == "solve"

    def test_bug_prompt_asks_for_diagnosis_and_fix(self) -> None:
        prompt = phone_share.BUG_PROMPT
        for marker in ("问题", "修复", "```"):
            assert marker in prompt, f"找 Bug 提示词缺少关键要求: {marker}"
        assert "```" in prompt, "必须允许代码围栏，否则两端的代码块渲染没有输入"

    def test_bug_task_ignores_answer_mode(self) -> None:
        """找 Bug 不随作答模式变化（一个是查错、一个是求解）。"""
        bug = phone_share.build_solve_prompt_for_task("acm", "bug")
        assert bug == phone_share.BUG_PROMPT
        # 解题任务仍按模式走
        assert phone_share.build_solve_prompt_for_task("acm", "solve") == phone_share.build_solve_prompt("acm")

    def test_custom_solve_prompt_does_not_hijack_bug_task(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """自定义解题提示词不得影响找 Bug。

        否则用户自定义了「解题」模板后，点「找 Bug」会去做解题，而手机上
        只有气泡内容不同、没有任何提示。
        """
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "visionEnabled": True,
                    "visionBaseUrl": "https://vision.example/v1",
                    "visionModel": "vl-1",
                    "solvePrompt": "只输出答案字母",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(phone_share, "CONFIG_PATH", config)
        monkeypatch.setattr(phone_share, "load_vision_key", lambda: "sk-test")
        monkeypatch.setattr(
            "system_audio_asr.settings.validate_public_http_url", lambda url: url
        )
        captured: dict = {}

        def fake_stream(**kwargs):
            captured.update(kwargs)
            return "答案"

        monkeypatch.setattr("system_audio_asr.ai_stream.stream_chat_completion", fake_stream)
        engine = phone_share.SolveEngine()
        engine._run_stream([b"\xff\xd8one"], "bug")
        text = [p for p in captured["messages"][0]["content"] if p["type"] == "text"][0]["text"]
        assert "只输出答案字母" not in text, "自定义解题提示词劫持了找 Bug"
        assert "修复" in text

    def test_bug_payload_uses_bug_prompt_for_multi_image(self, monkeypatch, tmp_path: Path) -> None:
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "visionEnabled": True,
                    "visionBaseUrl": "https://vision.example/v1",
                    "visionModel": "vl-1",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(phone_share, "CONFIG_PATH", config)
        monkeypatch.setattr(phone_share, "load_vision_key", lambda: "sk-test")
        monkeypatch.setattr(
            "system_audio_asr.settings.validate_public_http_url", lambda url: url
        )
        captured: dict = {}

        def fake_stream(**kwargs):
            captured.update(kwargs)
            return "答案"

        monkeypatch.setattr("system_audio_asr.ai_stream.stream_chat_completion", fake_stream)
        engine = phone_share.SolveEngine()
        engine._run_stream([b"\xff\xd8a", b"\xff\xd8b"], "bug")
        content = captured["messages"][0]["content"]
        assert len([p for p in content if p["type"] == "image_url"]) == 2
        text = [p for p in content if p["type"] == "text"][0]["text"]
        assert "2 张截图" in text and "合起来理解" in text
        # 多图说明要贴合找 Bug 场景，而不是说「这道题」
        assert "这道题" not in text


class TestAutoSubmitInterval:
    """自动提交问题：间隔必须是受支持的档位，且忙碌时跳过而不是排队。"""

    def test_normalize_interval_snaps_to_levels(self) -> None:
        # 毫秒输入 → 吸附到最近档，返回毫秒
        assert phone_share.normalize_auto_interval_ms(3000) == 3000
        assert phone_share.normalize_auto_interval_ms(5000) == 5000
        assert phone_share.normalize_auto_interval_ms(4000) == 3000
        assert phone_share.normalize_auto_interval_ms(12000) == 10000
        assert phone_share.normalize_auto_interval_ms(60000) == 60000

    def test_normalize_interval_rejects_absurd_values(self) -> None:
        """极小值必须被抬到最小档：自动提交会真花钱，不能让 0.1 秒刷满额度。"""
        assert phone_share.normalize_auto_interval_ms(100) == 3000
        assert phone_share.normalize_auto_interval_ms(0) == 3000
        assert phone_share.normalize_auto_interval_ms(-9999) == 3000
        # 超大值吸附到最大档
        assert phone_share.normalize_auto_interval_ms(10 ** 9) == 60000

    def test_normalize_interval_handles_garbage(self) -> None:
        default = phone_share.DEFAULT_AUTO_INTERVAL_SECONDS * 1000
        for bad in (None, "abc", "", [], {}):
            assert phone_share.normalize_auto_interval_ms(bad) == default, bad

    def test_levels_are_sorted_and_reachable(self) -> None:
        """档位表要覆盖手机下拉里的每个值（3/5/10/15/30/60 秒）。"""
        assert phone_share.AUTO_INTERVAL_LEVELS == (3, 5, 10, 15, 30, 60)
        for seconds in phone_share.AUTO_INTERVAL_LEVELS:
            assert phone_share.normalize_auto_interval_ms(seconds * 1000) == seconds * 1000

    def test_set_auto_records_state_for_hello(self, monkeypatch) -> None:
        """勾选状态必须记下来，供手机刷新后回填。"""
        import asyncio

        relay = phone_share.PhoneRelay()
        monkeypatch.setattr(relay, "_spawn", lambda coro: coro.close())
        monkeypatch.setattr(
            relay, "_running_loop", lambda: asyncio.new_event_loop()
        )
        relay._set_auto(True, 10000, solve=True)
        assert relay._auto_solve is True
        assert relay._auto_interval_ms == 10000

    def test_auto_loop_sleeps_in_seconds_not_milliseconds(self, monkeypatch) -> None:
        """循环的等待时间必须换算成秒再交给 asyncio.sleep。

        这是 P0 回归：asyncio.sleep 收的是秒，而状态字段是毫秒。曾经把
        normalize_auto_interval_ms 的返回值（毫秒）直接喂进去，于是「每 5 秒」
        实际睡 5000 秒 = 83 分钟 —— 用户勾选后只在第一轮立即截屏一次，
        之后再无动静，看起来就是「定时功能不好使」。
        """
        import asyncio

        waits: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waits.append(seconds)
            raise _StopAutoLoop  # 记下这一轮的等待时长后立刻退出，测试不真等

        monkeypatch.setattr(phone_share.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(
            phone_share.PhoneRelay,
            "request_solve",
            lambda self, task=None, quiet_busy=False: True,
        )

        async def scenario() -> None:
            relay = phone_share.PhoneRelay()
            relay._set_auto(True, 5000, solve=True)
            task = next(iter(relay._tasks), None)
            assert task is not None, "auto_loop 没有被创建"
            with pytest.raises(_StopAutoLoop):
                await task

        asyncio.run(scenario())
        assert waits == [5.0], (
            f"传给 asyncio.sleep 的是 {waits}，应为 [5.0] 秒；"
            "若是 [5000.0] 说明又漏了 /1000（每 5 秒会变成 83 分钟）"
        )

    def test_auto_loop_repeats_at_the_configured_cadence(self, monkeypatch) -> None:
        """真实时钟端到端：3 秒档在 6 秒内必须发生第 2 次提交。

        单位错误只有在真实等待里才暴露：把 3000 毫秒当秒睡，
        第二次提交要等 50 分钟，本用例会直接超时失败。
        """
        import threading
        import time

        calls: list[float] = []
        ready = threading.Event()
        loop = asyncio.new_event_loop()

        def record(self, task=None, quiet_busy=False) -> bool:
            calls.append(time.monotonic())
            if len(calls) >= 2:
                ready.set()
            return True

        monkeypatch.setattr(phone_share.PhoneRelay, "request_solve", record)
        relay = phone_share.PhoneRelay()

        def run_loop() -> None:
            asyncio.set_event_loop(loop)
            relay._loop = loop  # 等价于生产里的 bind_loop()，此处线程里没有运行中的循环
            relay._set_auto(True, 3000, solve=True)
            loop.run_forever()

        worker = threading.Thread(target=run_loop, daemon=True)
        worker.start()
        try:
            assert ready.wait(timeout=8.0), (
                f"3 秒档在 8 秒内只提交了 {len(calls)} 次；"
                "若为 1 次说明间隔被当成了秒（3000 秒）"
            )
            # 两次提交的间隔应接近 3 秒，而不是 0（立即连发）
            assert calls[1] - calls[0] >= 2.5, f"间隔过短: {calls[1] - calls[0]:.2f}s"
        finally:
            relay.stop_auto_capture()
            loop.call_soon_threadsafe(loop.stop)
            worker.join(timeout=3.0)


    def test_stop_auto_resets_solve_flag(self, monkeypatch) -> None:
        """停掉自动循环后必须复位标志，否则 hello 会让手机显示成仍在自动提交。"""
        import asyncio

        relay = phone_share.PhoneRelay()
        monkeypatch.setattr(relay, "_spawn", lambda coro: coro.close())
        monkeypatch.setattr(relay, "_running_loop", lambda: asyncio.new_event_loop())
        relay._set_auto(True, 5000, solve=True)
        assert relay._auto_solve is True
        relay.stop_auto_capture()
        assert relay._auto_solve is False

    def test_auto_solve_skips_when_busy_without_noise(self, monkeypatch) -> None:
        """引擎忙碌时自动提交应安静跳过：不刷「请求还在进行中」的提示。

        自动模式下用户没点任何按钮，每隔几秒弹一条提示只是噪音；
        而且解题常比间隔慢，若不跳过会堆积请求。
        """
        relay = phone_share.PhoneRelay()
        relay.solve_engine._busy.acquire()
        prompts: list[str] = []
        monkeypatch.setattr(
            relay, "_on_solve_delta", lambda text, done: prompts.append(text)
        )
        try:
            assert relay.request_solve(phone_share.SOLVE_TASK_SOLVE, True) is False
        finally:
            relay.solve_engine._busy.release()
        assert prompts == [], f"自动模式不该推送忙碌提示，却推了: {prompts}"

    def test_manual_solve_still_warns_when_busy(self) -> None:
        """手动点击仍要给提示，否则用户点的气泡永远不封口。"""
        relay = phone_share.PhoneRelay()
        relay.solve_engine._busy.acquire()
        prompts: list[str] = []
        original = relay._on_solve_delta
        relay._on_solve_delta = lambda text, text_done: prompts.append(text)
        try:
            assert relay.request_solve() is False
        finally:
            relay._on_solve_delta = original
            relay.solve_engine._busy.release()
        assert prompts and "进行中" in prompts[-1]


class TestAutoSubmitUsesCurrentThinkingTier:
    """自动提交必须带上当前设置的思考档位，且每次都重读配置。

    担心的是：自动循环若缓存了配置，用户切档后自动提交仍在用旧档位——
    界面显示「深度思考」，实际按默认档跑，且完全看不出来。
    """

    def _capture_outgoing(self, monkeypatch, tmp_path: Path, tier: str) -> dict:
        """跑一次真实的 _run_stream，捕获它实际发出的请求参数。"""
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "visionEnabled": True,
                    "visionBaseUrl": "https://vision.example/v1",
                    "visionModel": "vl-1",
                    "visionThinkingMode": tier,
                    "visionMaxTokens": 4096,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(phone_share, "CONFIG_PATH", config)
        monkeypatch.setattr(phone_share, "load_vision_key", lambda: "sk-test")
        monkeypatch.setattr(
            "system_audio_asr.settings.validate_public_http_url", lambda url: url
        )
        captured: dict = {}

        def fake_stream(**kwargs):
            captured.update(kwargs)
            return "答案"

        monkeypatch.setattr("system_audio_asr.ai_stream.stream_chat_completion", fake_stream)
        phone_share.SolveEngine()._run_stream([b"\xff\xd8one"], phone_share.SOLVE_TASK_SOLVE)
        return captured

    def test_reasoning_tier_reaches_the_request(self, monkeypatch, tmp_path: Path) -> None:
        """切到深度思考后，解题链路发出的请求要带 reasoning_effort。

        注意分层：max_tokens 的抬高发生在 stream_chat_completion 内部
        （它调用 apply_thinking_mode），所以这里只能断言传进去的 thinking_mode；
        额度抬高由 test_ai_stream.test_reasoning_modes_raise_token_budget 覆盖。
        """
        sent = self._capture_outgoing(monkeypatch, tmp_path, "high")
        assert sent.get("thinking_mode") == "high", (
            f"解题链路读到的档位是 {sent.get('thinking_mode')}，不是 high"
        )

    def test_reasoning_tier_payload_is_actually_built_with_effort(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """端到端确认：解题链路最终发出的**真实请求体**带 reasoning_effort 且额度已抬高。

        上一条只看到传给 stream_chat_completion 的参数；这里拦截 httpx 层，
        观察它构造并实际送出的 payload —— 避免「档位传了但没落到请求体」这类断链。
        """
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "visionEnabled": True,
                    "visionBaseUrl": "https://vision.example/v1",
                    "visionModel": "vl-1",
                    "visionThinkingMode": "high",
                    "visionMaxTokens": 4096,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(phone_share, "CONFIG_PATH", config)
        monkeypatch.setattr(phone_share, "load_vision_key", lambda: "sk-test")
        monkeypatch.setattr(
            "system_audio_asr.settings.validate_public_http_url", lambda url: url
        )

        sent_body: dict = {}

        class FakeResponse:
            status_code = 200

            def iter_lines(self):
                return iter(['data: {"choices":[{"delta":{"content":"答案"}}]}', "data: [DONE]"])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream(self, method, url, json=None, headers=None):
                sent_body.update(json or {})
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        phone_share.SolveEngine()._run_stream([b"\xff\xd8one"], phone_share.SOLVE_TASK_SOLVE)

        assert sent_body.get("reasoning_effort") == "high", (
            f"真实请求体没有带 reasoning_effort：{sorted(sent_body)}"
        )
        assert sent_body.get("max_tokens", 0) >= 8192, (
            f"推理档没有抬高额度（{sent_body.get('max_tokens')}），正文会返回空"
        )

    def test_tier_is_reread_each_call(self, monkeypatch, tmp_path: Path) -> None:
        """每次调用都要重读配置：切档后无需重启服务，下一次提交即生效。"""
        first = self._capture_outgoing(monkeypatch, tmp_path, "off")
        assert first.get("thinking_mode") == "off"
        # 同一进程内改成 high，再跑一次（模拟用户切换档位）
        second = self._capture_outgoing(monkeypatch, tmp_path, "high")
        assert second.get("thinking_mode") == "high", (
            "档位被缓存了：切换后提交仍用旧档位"
        )

    def test_auto_and_manual_share_the_same_path(self, monkeypatch, tmp_path: Path) -> None:
        """自动提交走的 request_solve 与手动完全同一条链路，故档位一致。"""
        sent: dict = {}
        monkeypatch.setattr(
            phone_share, "load_vision_config",
            lambda: {"enabled": True, "baseUrl": "https://x/v1", "model": "m",
                     "thinkingMode": "medium", "maxTokens": 4096, "prompt": "p",
                     "maxImages": 3, "answerMode": "core_code"},
        )
        monkeypatch.setattr(phone_share, "capture_screen_jpeg", lambda: b"\xff\xd8x")
        monkeypatch.setattr(phone_share, "load_vision_key", lambda: "k")
        monkeypatch.setattr(
            "system_audio_asr.settings.validate_public_http_url", lambda url: url
        )

        def fake_stream(**kwargs):
            sent.update(kwargs)
            return "答案"

        monkeypatch.setattr("system_audio_asr.ai_stream.stream_chat_completion", fake_stream)
        monkeypatch.setattr(
            "system_audio_asr.recorder.session_recorder.add_solve_images", lambda images: None
        )
        relay = phone_share.PhoneRelay()
        monkeypatch.setattr(relay, "_drain_bytes_threadsafe", lambda data: None)
        relay.request_solve(phone_share.SOLVE_TASK_SOLVE, True)  # 自动模式调用
        for _ in range(100):
            if sent:
                break
            import time

            time.sleep(0.02)
        assert sent.get("thinking_mode") == "medium", "自动提交没有带上当前思考档位"


class TestAutoSubmitFailureNoise:
    """自动提交连续失败时的降噪与自停。

    背景：模型未启用 / 网关不通 / Key 失效时，每次间隔都会推一条同样的失败消息。
    聊天气泡上限是 50 条，按 3 秒间隔算约 2.5 分钟就把上限填满，之前真正的
    解题回答与追问会被整屏重复错误挤掉。

    这些用例刻意走生产入口 `_on_solve_delta`（引擎回调）而不是直接调
    `_note_auto_failure`：失败统计点曾经错放在 auto_loop 的提交处，直接调叶子
    函数测不出「真实链路压根统计不到」这个 bug。断言也只看真正推给手机的消息
    （schedule_json 的载荷），不看内部字段。
    """

    def _relay(self, monkeypatch) -> tuple[phone_share.PhoneRelay, list[dict]]:
        """建一个处在自动提交状态的中继，并捕获所有下发给手机的载荷。"""
        relay = phone_share.PhoneRelay()
        sent: list[dict] = []
        monkeypatch.setattr(relay, "schedule_json", lambda payload: sent.append(payload))
        relay._auto_solve = True
        relay._auto_generation = 7
        return relay, sent

    @staticmethod
    def _round_failed(relay: phone_share.PhoneRelay, reason: str) -> None:
        """模拟自动提交的一轮真实结束：引擎完成一次提交后回调失败。"""
        relay._auto_submitted = True
        relay._on_solve_delta(f"{phone_share._SOLVE_FAILURE_PREFIX}{reason}", True)

    def test_first_failure_is_reported(self, monkeypatch) -> None:
        """第一次失败要如实告诉用户原因（不能静默），后续重复的同一失败不再推送。"""
        relay, sent = self._relay(monkeypatch)
        # 只跑 2 轮（未到自停上限），单看降噪：同一文案只提示第一次
        for _ in range(2):
            self._round_failed(relay, "视觉模型未启用")
        texts = [p["text"] for p in sent if p.get("type") == "ai"]
        assert len(texts) == 1, f"同一失败应只提示一次，实际 {len(texts)} 次: {texts}"
        assert "视觉模型未启用" in texts[0]

    def test_repeated_failure_stops_loop(self, monkeypatch) -> None:
        """连续失败到上限要停止循环，并推一条停止通知。"""
        relay, sent = self._relay(monkeypatch)
        for _ in range(phone_share.AUTO_FAILURE_LIMIT + 2):
            self._round_failed(relay, "电脑端屏幕采集失败")

        texts = [p["text"] for p in sent if p.get("type") == "ai"]
        # 首次失败 + 停止通知 = 2 条；中间的重复失败与停后的在途失败都不再刷屏
        assert len(texts) == 2, f"应只推 2 条（首次 + 停止），实际 {len(texts)}: {texts}"
        assert "已停止" in texts[-1]
        assert relay._auto_generation != 7, "未停止自动循环（generation 未递增）"
        assert relay._auto_solve is False, "未复位自动提交标志"
        # 自停必须复位手机上的勾选：auto_state 是唯一能让界面同步的帧
        states = [p for p in sent if p.get("type") == "auto_state"]
        assert states and states[-1]["on"] is False, "未推 auto_state 复位帧"

    def test_capture_failure_counts_toward_self_stop(self, monkeypatch) -> None:
        """抓屏失败必须走同一条统计：曾经每轮推一条气泡却永不计数。

        request_solve 在抓屏失败时推「解题失败：电脑端屏幕采集失败」并
        return False；若统计只写在提交成功后的分支（if submitted），
        这类失败每轮刷一条气泡、连续失败计数却永远是 0，永远不会自停。
        """
        relay, sent = self._relay(monkeypatch)
        monkeypatch.setattr(
            phone_share, "capture_screen_jpeg",
            lambda: (_ for _ in ()).throw(RuntimeError("采集失败")),
        )
        for _ in range(phone_share.AUTO_FAILURE_LIMIT + 1):
            relay.request_solve(phone_share.SOLVE_TASK_SOLVE, True)
            relay._solve_generation = relay._session_generation  # 场次一致，增量不过期

        texts = [p["text"] for p in sent if p.get("type") == "ai"]
        assert len(texts) == 2, f"抓屏失败的降噪失效，推出 {len(texts)} 条: {texts}"
        assert "已停止" in texts[-1], "抓屏失败没有触发连续失败自停"
        assert relay._auto_solve is False

    def test_success_resets_failure_streak(self, monkeypatch) -> None:
        """中途成功要清零计数，否则偶发失败累积几次就被误停。"""
        relay, sent = self._relay(monkeypatch)

        self._round_failed(relay, "失败 A")
        relay._auto_submitted = True
        relay._on_solve_delta("正常答案", True)  # 本轮成功
        self._round_failed(relay, "失败 B")

        assert relay._auto_failures == 1, "成功后未清零失败计数"
        assert relay._auto_generation == 7, "不应停止"
        texts = [p["text"] for p in sent if p.get("type") == "ai"]
        # 失败 A、正常答案、失败 B 各一条；两次失败文案不同，各提示一次
        assert len(texts) == 3, f"实际 {len(texts)}: {texts}"
        assert "正常答案" in texts[1]

    def test_different_failure_message_is_reported(self, monkeypatch) -> None:
        """失败原因变化时要再提示一次，否则用户看不到新原因。"""
        relay, sent = self._relay(monkeypatch)
        self._round_failed(relay, "视觉模型未启用")
        self._round_failed(relay, "电脑端屏幕采集失败")
        texts = [p["text"] for p in sent if p.get("type") == "ai"]
        assert len(texts) == 2, "失败原因变化后未再提示"

    def test_reenabled_after_self_stop_gets_full_quota(self, monkeypatch) -> None:
        """自停后重新开启要清零计数，否则第一次失败就立刻又停。"""
        relay = phone_share.PhoneRelay()
        sent: list[dict] = []
        monkeypatch.setattr(relay, "schedule_json", lambda payload: sent.append(payload))
        monkeypatch.setattr(relay, "_spawn", lambda coro: coro.close())
        monkeypatch.setattr(relay, "_running_loop", lambda: asyncio.new_event_loop())

        relay._set_auto(True, 3000, solve=True)
        assert relay._auto_failures == 0
        for _ in range(phone_share.AUTO_FAILURE_LIMIT):
            self._round_failed(relay, "视觉模型未启用")
        assert relay._auto_solve is False, "连续失败未自停"

        # 用户重新勾选：计数必须归零，否则下一次失败立刻又停
        relay._set_auto(True, 3000, solve=True)
        assert relay._auto_failures == 0, "重新开启未清零失败计数"
        assert relay._auto_solve is True
        self._round_failed(relay, "视觉模型未启用")
        assert relay._auto_solve is True, "重新开启后第一次失败就停了（计数未清零）"

    def test_manual_failure_still_reports_raw_text(self, monkeypatch) -> None:
        """手动点「截题+回答」失败时必须推原文封口气泡，不能走自动降噪。"""
        relay = phone_share.PhoneRelay()
        sent: list[dict] = []
        monkeypatch.setattr(relay, "schedule_json", lambda payload: sent.append(payload))
        relay._auto_solve = True
        relay._auto_submitted = False  # 这一轮是用户手点的
        relay._on_solve_delta(f"{phone_share._SOLVE_FAILURE_PREFIX}视觉模型未启用", True)
        texts = [p["text"] for p in sent if p.get("type") == "ai"]
        assert texts == [f"{phone_share._SOLVE_FAILURE_PREFIX}视觉模型未启用"]
        assert relay._auto_failures == 0, "手动失败被算进了自动提交的失败计数"

    def test_busy_skip_is_not_a_failure(self, monkeypatch) -> None:
        """间隔比解题快时的「还在进行中」不是失败，不能累积成自停。"""
        relay, _ = self._relay(monkeypatch)
        relay.solve_engine._busy.acquire()
        try:
            for _ in range(phone_share.AUTO_FAILURE_LIMIT + 2):
                relay.request_solve(phone_share.SOLVE_TASK_SOLVE, True)
        finally:
            relay.solve_engine._busy.release()
        assert relay._auto_failures == 0, "忙碌跳过被误计为失败"
        assert relay._auto_solve is True, "忙碌跳过把自动循环停掉了"
        assert relay._auto_submitted is False, "忙碌跳过后仍带着来源标记（下一帧会被错记）"

    def test_manual_click_while_auto_round_running_still_reports(self, monkeypatch) -> None:
        """自动轮在途时用户手点「截题+回答」：手动的忙碌提示必须照常推出并封口气泡。

        且这条提示不能被当成在途自动轮的结果 —— 否则它的 done 会被
        _on_solve_delta 记成「本轮成功」，把已经累积的失败计数清零，
        连续失败自停就再也数不满（用户手点一下就能「续命」）。
        """
        relay, sent = self._relay(monkeypatch)
        relay._auto_failures = 2  # 自动提交已经失败过两次
        relay._auto_last_failure = "视觉模型未启用"
        relay._auto_submitted = True  # 在途的自动轮还没返回
        relay.solve_engine._busy.acquire()
        try:
            assert relay.request_solve() is False
        finally:
            relay.solve_engine._busy.release()
        texts = [p["text"] for p in sent if p.get("type") == "ai"]
        assert texts and "进行中" in texts[-1], f"手动忙碌提示没有推出: {texts}"
        assert relay._auto_failures == 2, "手动的忙碌提示把自动提交的失败计数清零了"


class TestRecordImageCapConfig:
    def test_default_is_two_hundred(self) -> None:
        assert DEFAULTS["recordImageCap"] == 200

    def test_snaps_to_levels(self) -> None:
        # 与长度档位同一套规则：吸附到最近档（250 离 200 比离 300 近）。
        assert normalize_record_image_cap(3) == 100
        assert normalize_record_image_cap(250) == 200
        assert normalize_record_image_cap(280) == 300
        assert normalize_record_image_cap(100000) == 500
        assert normalize_record_image_cap("bad") == 200
        assert normalize_record_image_cap(None) == 200

    def test_recorder_reads_cap_from_config(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "system_audio_asr.settings.load_settings", lambda *a, **k: {"recordImageCap": 100}
        )
        assert recorder.image_cap() == 100

    def test_recorder_cap_falls_back_when_config_broken(self, monkeypatch) -> None:
        """配置读不出来时记录功能仍要可用（回退模块默认值，不抛异常）。"""

        def boom(*args, **kwargs):
            raise RuntimeError("config exploded")

        monkeypatch.setattr("system_audio_asr.settings.load_settings", boom)
        assert recorder.image_cap() == recorder.MAX_IMAGES

    def test_ring_honours_config_cap(self, monkeypatch) -> None:
        monkeypatch.setattr(recorder, "image_cap", lambda: 3)
        rec = recorder.SessionRecorder()
        for index in range(5):
            rec.add_solve_images([bytes([index])])
        images = rec._images  # 直读内部状态：这里断言的就是环形裁剪本身
        assert len(images) == 3
        assert [img["data"] for img in images] == [b"\x02", b"\x03", b"\x04"]


class TestMultiImageSolve:
    def test_solve_accepts_single_bytes_for_back_compat(self, monkeypatch) -> None:
        """桌面热键仍传单张 bytes，不能因此报错。"""
        engine = phone_share.SolveEngine()
        seen: list[list[bytes]] = []
        monkeypatch.setattr(engine, "_run_stream", lambda images, task="solve": seen.append(images))
        engine.solve(b"\xff\xd8one")
        for _ in range(50):
            if seen:
                break
            import time

            time.sleep(0.02)
        assert seen and seen[0] == [b"\xff\xd8one"]

    def test_solve_accepts_list_and_drops_empty(self, monkeypatch) -> None:
        engine = phone_share.SolveEngine()
        seen: list[list[bytes]] = []
        monkeypatch.setattr(engine, "_run_stream", lambda images, task="solve": seen.append(images))
        engine.solve([b"\xff\xd8a", b"", b"\xff\xd8b"])
        for _ in range(50):
            if seen:
                break
            import time

            time.sleep(0.02)
        assert seen and seen[0] == [b"\xff\xd8a", b"\xff\xd8b"]

    def test_empty_batch_emits_message_without_calling_model(self, monkeypatch) -> None:
        engine = phone_share.SolveEngine()
        events: list[tuple[str, bool]] = []
        monkeypatch.setattr(engine, "_emit", lambda text, done: events.append((text, done)))
        engine.solve([])
        assert events and events[0][1] is True
        assert engine.busy is False, "空批次必须释放 busy，否则后续解题全被挡住"

    def test_multi_image_payload_has_all_images_and_ordering_hint(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """多张截图必须全部进入请求体，且说明它们属于同一道题。"""
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "visionEnabled": True,
                    "visionBaseUrl": "https://vision.example/v1",
                    "visionModel": "vl-1",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(phone_share, "CONFIG_PATH", config)
        monkeypatch.setattr(phone_share, "load_vision_key", lambda: "sk-test")
        monkeypatch.setattr(
            "system_audio_asr.settings.validate_public_http_url", lambda url: url
        )
        captured: dict = {}

        def fake_stream(**kwargs):
            captured.update(kwargs)
            return "答案"

        monkeypatch.setattr(
            "system_audio_asr.ai_stream.stream_chat_completion", fake_stream
        )
        engine = phone_share.SolveEngine()
        engine._run_stream([b"\xff\xd8one", b"\xff\xd8two", b"\xff\xd8three"])

        content = captured["messages"][0]["content"]
        images = [part for part in content if part["type"] == "image_url"]
        texts = [part for part in content if part["type"] == "text"]
        assert len(images) == 3, "三张截图没有全部进入请求体"
        assert len(texts) == 1
        assert "3 张截图" in texts[0]["text"] and "合起来理解" in texts[0]["text"], (
            "缺少「同题」说明，模型会当成多个独立问题"
        )
        assert all(
            part["image_url"]["url"].startswith("data:image/jpeg;base64,") for part in images
        )

    def test_single_image_payload_has_no_ordering_hint(self, monkeypatch, tmp_path: Path) -> None:
        """单张时不该加多余的「同一道题」说明（保持历史提示词逐字一致）。"""
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "visionEnabled": True,
                    "visionBaseUrl": "https://vision.example/v1",
                    "visionModel": "vl-1",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(phone_share, "CONFIG_PATH", config)
        monkeypatch.setattr(phone_share, "load_vision_key", lambda: "sk-test")
        monkeypatch.setattr(
            "system_audio_asr.settings.validate_public_http_url", lambda url: url
        )
        captured: dict = {}

        def fake_stream(**kwargs):
            captured.update(kwargs)
            return "答案"

        monkeypatch.setattr(
            "system_audio_asr.ai_stream.stream_chat_completion", fake_stream
        )
        engine = phone_share.SolveEngine()
        engine._run_stream([b"\xff\xd8one"])
        content = captured["messages"][0]["content"]
        texts = [part for part in content if part["type"] == "text"]
        assert "张截图" not in texts[0]["text"]


class TestPendingBuffer:
    def _relay(self, monkeypatch, tmp_path: Path, cap: int = 3) -> phone_share.PhoneRelay:
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"visionMaxImages": cap}), encoding="utf-8")
        monkeypatch.setattr(phone_share, "CONFIG_PATH", config)
        return phone_share.PhoneRelay()

    def test_add_appends_until_limit(self, monkeypatch, tmp_path: Path) -> None:
        relay = self._relay(monkeypatch, tmp_path, cap=2)
        monkeypatch.setattr(phone_share, "capture_screen_jpeg", lambda: b"\xff\xd8x")
        monkeypatch.setattr(relay, "_push_pending_state", lambda count: None)
        monkeypatch.setattr(relay, "_drain_bytes_threadsafe", lambda data: None)
        assert relay.add_pending_solve_image() == 1
        assert relay.add_pending_solve_image() == 2
        assert relay.add_pending_solve_image() == 0, "达到上限后应返回 0（前端据此禁用按钮）"

    def test_capture_failure_reports_minus_one(self, monkeypatch, tmp_path: Path) -> None:
        relay = self._relay(monkeypatch, tmp_path)

        def boom() -> bytes:
            raise RuntimeError("screen gone")

        monkeypatch.setattr(phone_share, "capture_screen_jpeg", boom)
        assert relay.add_pending_solve_image() == -1

    def test_clear_empties_buffer(self, monkeypatch, tmp_path: Path) -> None:
        relay = self._relay(monkeypatch, tmp_path)
        monkeypatch.setattr(phone_share, "capture_screen_jpeg", lambda: b"\xff\xd8x")
        monkeypatch.setattr(relay, "_push_pending_state", lambda count: None)
        monkeypatch.setattr(relay, "_drain_bytes_threadsafe", lambda data: None)
        relay.add_pending_solve_image()
        relay.clear_pending_solve_images()
        assert relay._pending_solve_images == []

    def test_new_session_clears_pending(self, monkeypatch, tmp_path: Path) -> None:
        """上一场攒的图不能带进新一场，否则新题会混入旧题截图。"""
        relay = self._relay(monkeypatch, tmp_path)
        monkeypatch.setattr(phone_share, "capture_screen_jpeg", lambda: b"\xff\xd8x")
        monkeypatch.setattr(relay, "_push_pending_state", lambda count: None)
        monkeypatch.setattr(relay, "_drain_bytes_threadsafe", lambda data: None)
        monkeypatch.setattr(relay, "stop_auto_capture", lambda: None)
        relay.add_pending_solve_image()
        relay.begin_new_session()
        assert relay._pending_solve_images == []

    def test_request_solve_uses_pending_batch(self, monkeypatch, tmp_path: Path) -> None:
        """待解缓冲非空时提交缓冲里的全部截图，而不是另截一张。"""
        relay = self._relay(monkeypatch, tmp_path)
        monkeypatch.setattr(relay, "_push_pending_state", lambda count: None)
        monkeypatch.setattr(relay, "_drain_bytes_threadsafe", lambda data: None)
        monkeypatch.setattr(relay, "_push_pending_state", lambda count: None)
        relay._pending_solve_images = [b"\xff\xd8a", b"\xff\xd8b"]
        submitted: list = []
        monkeypatch.setattr(
            relay.solve_engine, "solve", lambda images, task="solve": submitted.append(images)
        )
        monkeypatch.setattr(
            "system_audio_asr.recorder.session_recorder.add_solve_images", lambda images: None
        )
        assert relay.request_solve() is True
        assert submitted == [[b"\xff\xd8a", b"\xff\xd8b"]]
        assert relay._pending_solve_images == [], "提交后缓冲必须清空"

    def test_request_solve_captures_when_no_pending(self, monkeypatch, tmp_path: Path) -> None:
        """缓冲为空时行为与历史一致：现场截一张立即提交。"""
        relay = self._relay(monkeypatch, tmp_path)
        monkeypatch.setattr(phone_share, "capture_screen_jpeg", lambda: b"\xff\xd8shot")
        monkeypatch.setattr(relay, "_drain_bytes_threadsafe", lambda data: None)
        submitted: list = []
        monkeypatch.setattr(
            relay.solve_engine, "solve", lambda images, task="solve": submitted.append(images)
        )
        monkeypatch.setattr(
            "system_audio_asr.recorder.session_recorder.add_solve_images", lambda images: None
        )
        assert relay.request_solve() is True
        assert submitted == [[b"\xff\xd8shot"]]


class TestMultiImageRecord:
    def test_batch_groups_images_with_one_solve(self) -> None:
        """一次解题的多张图必须整批归到同一条记录，不能只落地一张。"""
        rec = recorder.SessionRecorder()
        rec.add_ai("答案", True)
        rec.add_solve_images([b"a", b"b", b"c"])
        rec._add_solve("解法", True)
        markdown = rec._markdown()
        assert "![题目](images/" in markdown
        assert markdown.count("![题目](images/") == 3, "多图解题只落地了部分题图"
        assert markdown.count("· 截图解题**") == 1

    def test_separate_solves_keep_their_own_images(self) -> None:
        """两次相邻解题各自的图不能串到对方记录里。"""
        rec = recorder.SessionRecorder()
        rec.add_solve_images([b"first"])
        rec._add_solve("第一题答案", True)
        rec.add_solve_images([b"second"])
        rec._add_solve("第二题答案", True)
        markdown = rec._markdown()
        first_at = markdown.index("第一题答案")
        second_at = markdown.index("第二题答案")
        between = markdown[first_at:second_at]
        assert between.count("![题目](images/") <= 1, "第二题的图串进了第一题"
        assert markdown.count("![题目](images/") == 2

    def test_single_image_path_still_works(self) -> None:
        rec = recorder.SessionRecorder()
        rec.add_solve_image(b"only")
        rec._add_solve("解法", True)
        assert rec._markdown().count("![题目](images/") == 1

    def test_missing_image_notice_names_configurable_cap(self, monkeypatch) -> None:
        """缺图提示要说明当前上限可调，否则用户不知道能改。"""
        monkeypatch.setattr(recorder, "image_cap", lambda: 100)
        rec = recorder.SessionRecorder()
        rec._add_solve("没有配图的解题", True)
        markdown = rec._markdown()
        assert "未附带题图" in markdown
        assert "100" in markdown
        assert "题图保留张数" in markdown
