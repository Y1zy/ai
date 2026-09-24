"""字幕 AI「手动提交」与「自动/手动开关」的行为测试。

背景（用户报告的痛点）：静音 0.6 秒就自动把字幕发给 AI，面试官话说到一半
（停顿、换气、想措辞）只说了半句就被提交，答案自然不对。
现新增：
  · 悬浮窗悬停按钮排第一位的「问 AI」→ C# SubmitAiNow()
  · 手机端吸底栏「⚡ 问 AI」→ relay ask_now → C# 同一条路径
  · 配置项 aiAutoSubmit（默认 true = 历史行为）；关掉后只累积、不自动提交

字幕文本只存在于 C# 进程（Python 服务端只做识别与转发、不保存转写），
所以这里分两部分验证：
  A. C# 侧行为（编译真实源码片段，走真实判定逻辑）
  B. Python 侧转发与配置读写
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from system_audio_asr import phone_share

_ROOT = Path(__file__).resolve().parents[1]
_OVERLAY_CS = _ROOT / "overlay_cs" / "OverlayApp.cs"
_PHONE_HTML = _ROOT / "system_audio_asr" / "web" / "phone.html"
_SETTINGS_HTML = _ROOT / "system_audio_asr" / "web" / "settings.html"


def _overlay_source() -> str:
    return _OVERLAY_CS.read_text(encoding="utf-8-sig")


def _method_body(signature: str) -> str:
    """按大括号配平抽取 C# 方法体（不依赖固定长度）。"""
    text = _overlay_source()
    start = text.index(signature)
    index = text.index("{", start)
    depth = 0
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
        index += 1
    return text[start : index + 1]


class TestManualSubmitSourceContract:
    """C# 手动画提交必须与自动路径共用一条链路，并在各种边界给出反馈。"""

    def test_submit_ai_now_exists_and_reuses_start_ai_request(self) -> None:
        body = _method_body("internal void SubmitAiNow()")
        assert "StartAiRequest(true)" in body, (
            "手动提交没有以「手动」身份复用 StartAiRequest：要么绕过了共用链路"
            "（会出现两套提示词/档位），要么会被模式守卫挡下（按钮失效）"
        )

    def test_manual_submit_reports_when_nothing_to_send(self) -> None:
        """没有新内容时必须给出可见反馈（否则按钮看起来像坏了）。"""
        body = _method_body("internal void SubmitAiNow()")
        assert "没有待提交的新内容" in body, "空队列时静默失败，用户不知道按钮是否生效"
        assert "ShowToast" in body, "缺少可见反馈（悬浮窗处于穿透状态，提示条是唯一反馈）"

    def test_manual_submit_guards_busy_state(self) -> None:
        """正在回答时不排队：积压的请求会在面试结束后还在跑。"""
        body = _method_body("internal void SubmitAiNow()")
        assert "aiBusy" in body and "还在进行中" in body, "未处理「上一请求进行中」"

    def test_manual_submit_stops_pending_auto_timer(self) -> None:
        """手动提交要取消已排队的静音自动提交，避免发两遍。"""
        body = _method_body("internal void SubmitAiNow()")
        assert "aiTimer.Stop()" in body, (
            "手动提交前没停 aiTimer：自动计时器到点会再发一次同样的内容"
        )

    def test_manual_submit_requires_api_key(self) -> None:
        body = _method_body("internal void SubmitAiNow()")
        assert "HasApiKey" in body, "未检查 API Key：没配 Key 时点了没有任何解释"

    def test_collecting_batch_is_detached_before_submit(self) -> None:
        """提交前要断开收集指针。

        否则本次提交的内容在回答生成期间会继续被下一句 final 追加，
        用户看到的答案与提交时的内容不一致。
        """
        body = _method_body("internal void SubmitAiNow()")
        assert "collectingSpeechBatch = null" in body, (
            "未断开 collectingSpeechBatch：回答生成期间新字幕会并进已提交的那批"
        )


class TestAutoSubmitSwitch:
    """aiAutoSubmit 关闭后不得自动提交，但内容仍要累积。"""

    def test_both_trigger_sites_respect_the_switch(self) -> None:
        """两处启动 aiTimer 的地方都要判断开关。

        漏掉任何一处，关了自动仍会被静默提交：一处是收到 final 后的去抖启动，
        另一处是请求结束的 finally 里「还有排队批次就继续」。
        """
        source = _overlay_source()
        sites = [
            line
            for line in source.splitlines()
            if "aiTimer.Start()" in line
        ]
        assert sites, "未找到 aiTimer.Start() 调用点"
        guarded = [
            line
            for line in source.splitlines()
            if "config.AiAutoSubmit" in line
        ]
        assert len(guarded) >= 2, (
            f"只有 {len(guarded)} 处判断了 aiAutoSubmit（应 ≥2：final 去抖 + 请求结束续发）"
        )

    def test_manual_mode_still_queues_content(self) -> None:
        """关自动后内容仍要入队 —— 否则点「问 AI」时队列为空，提交不了任何东西。

        直接检查关闭分支所在的那段代码：入队发生在它上面的 newFinal 块里，
        而关闭分支必须只跳过「启动计时器」这一件事。
        """
        source = _overlay_source()
        start = source.index("if (useAi && !aiBusy)")
        block = source[start : start + 1500]
        assert "if (config.AiAutoSubmit)" in block, "关闭分支不在这里"
        manual = block[block.index("else") :]
        assert "aiTimer.Start()" not in manual, (
            "手动模式的 else 分支里仍在启动计时器（关了自动还会自动发）"
        )
        assert "manual_mode_pending" in manual, "手动模式没有留下待提交记录"
        # 入队必须在同一段 newFinal 逻辑内（在 if (useAi && !aiBusy) 之前）
        head = source[source.index("if (newFinal)") : start]
        assert "aiQueue.AddLast" in head, "关闭自动时内容没有入队，点「问 AI」会没东西可发"


class TestPhoneManualSubmitWiring:
    """手机端按钮 → relay → 桌面，链路不能断。"""

    def test_phone_has_ask_now_button(self) -> None:
        text = _PHONE_HTML.read_text(encoding="utf-8")
        assert 'id="ask-now-btn"' in text, "手机端缺少「问 AI」按钮"
        assert 'type:"ask_now"' in text or "type: \"ask_now\"" in text, "按钮未发送 ask_now"

    def test_phone_ask_now_button_is_in_actionbar(self) -> None:
        """吸底栏是面试中单手操作最顺的位置，不要放进折叠区。"""
        text = _PHONE_HTML.read_text(encoding="utf-8")
        actionbar = text[text.index('class="actionbar"') : text.index("</div>", text.index('class="actionbar"'))]
        assert "ask-now-btn" in actionbar, "「问 AI」按钮不在吸底栏里"

    def test_relay_forwards_ask_now_to_desktop(self) -> None:
        source = Path(phone_share.__file__).read_text(encoding="utf-8")
        assert 'kind == "ask_now"' in source, "relay 未处理 ask_now"
        assert "_request_manual_ai_submit" in source, "缺少转发到桌面的实现"
        # 必须走 desktop_publisher（= hub.publish，广播给含 C# 的所有 WS 客户端），
        # 不能用 schedule_json（只推给手机）—— 否则消息回到手机、桌面收不到，
        # 表现为「点了按钮没反应」。
        start = source.index("def _request_manual_ai_submit")
        end = source.index("\n    async def ", start)
        body = source[start:end]
        # 去掉三引号内的说明文字再判断：注释里提到 schedule_json 是解释「为什么不用它」，
        # 不是真的调用（第一版断言就是这样被自己的文档误伤）。
        code = re.sub(r'"""(?:.|\n)*?"""', "", body)
        assert "publisher({" in code, "转发实现里没有调用 desktop_publisher"
        # 转发本身必须走 publisher；schedule_json 只允许用于「回一条无法提交的提示」
        # （离线分支），不能用来转发 ask_now —— 那样桌面收不到、按钮没反应。
        forward = re.search(r'publisher\(\{"type": "ask_now"\}\)', code)
        assert forward, "没有经 desktop_publisher 转发 ask_now"
        for call in re.finditer(r"self\.schedule_json\(\{([^}]*)\}", code):
            assert "ask_now" not in call.group(1), (
                "把 ask_now 用 schedule_json 发回手机了：桌面收不到，按钮会没反应"
            )

    def test_ask_now_reports_when_no_desktop_connected(self) -> None:
        """桌面不在线时要如实告诉手机，而不是回一句「已请求」却什么也没发生。

        字幕文本只存在于 C# 进程，桌面没连上时这条 ask_now 必然无人处理。
        若手机端照样提示「已请求电脑提交」，用户会反复点按钮并以为功能坏了。
        """
        source = Path(phone_share.__file__).read_text(encoding="utf-8")
        assert "manual_ai_unavailable" in source, (
            "缺少「桌面不在线」的反馈消息类型"
        )
        # 判断在线状态的辅助方法：经 debug_snapshot_provider 的 wsClients
        helper_start = source.index("def _desktop_online")
        helper_end = source.index("\n    def ", helper_start)
        helper = source[helper_start:helper_end]
        assert "wsClients" in helper and "debug_snapshot_provider" in helper, (
            "没有判断桌面是否在线的依据，无法给出如实反馈"
        )
        # 转发实现要真的用上它，并在离线时回一条提示
        start = source.index("def _request_manual_ai_submit")
        end = source.index("\n    async def ", start)
        body = source[start:end]
        code = re.sub(r'"""(?:.|\n)*?"""', "", body)
        assert "_desktop_online()" in code, "转发时没有判断桌面是否在线"
        # 离线分支必须真的推消息，而不是静默 return。
        # 用「从 if 到第一个同级语句」的方式切分支，不能用贪婪的行匹配
        # （第一版全文搜索、第二版 {8,} 贪婪，都会把后面的 publisher is None
        # 分支一起吞进来，于是把离线分支改回静默 return 也照样通过）。
        offline_at = code.index("if not self._desktop_online():")
        tail = code[offline_at:].split("\n")
        branch_lines: list[str] = []
        for line in tail[1:]:
            if line.strip() and not line.startswith("            "):
                break  # 缩进回到 if 同级 → 分支结束
            branch_lines.append(line)
        branch = "\n".join(branch_lines)
        assert "manual_ai_unavailable" in branch, (
            "桌面离线时静默返回：手机照样显示「已请求」，用户被误导"
        )
        assert "return" in branch, "离线分支没有提前返回"
        # 手机端要处理这条消息并给用户提示
        phone = _PHONE_HTML.read_text(encoding="utf-8")
        assert 'case "manual_ai_unavailable"' in phone, (
            "手机端没有处理「桌面不在线」，用户看到的是误导性的「已请求」"
        )

    def test_phone_auto_submit_toggle_wired(self) -> None:
        text = _PHONE_HTML.read_text(encoding="utf-8")
        assert 'id="ai-auto-submit"' in text, "手机端缺少「字幕 AI 自动提交」开关"
        assert "applyAiAutoSubmit" in text, "开关没有回填逻辑（刷新后状态会显示错）"
        assert 'case "auto_submit"' in text, "没有处理服务端的回执帧"

    def test_settings_page_has_the_toggle(self) -> None:
        text = _SETTINGS_HTML.read_text(encoding="utf-8")
        assert 'id="aiAutoSubmit"' in text, "设置页缺少开关"
        script = text[text.index("<script") :]
        assert "aiAutoSubmit" in script, "设置页脚本未接入该控件"
        ids_match = re.search(r"const ids=\[([^\]]+)\]", script)
        assert ids_match and "'aiAutoSubmit'" in ids_match.group(1), (
            "aiAutoSubmit 不在回填列表里（勾选框永远显示默认状态）"
        )


class TestAutoSubmitConfigPlumbing:
    """aiAutoSubmit 的读写与两端一致性。"""

    @pytest.fixture()
    def isolated_config(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "config.json"
        target.write_text(json.dumps({"aiAutoSubmit": True}), encoding="utf-8")
        monkeypatch.setattr(phone_share, "CONFIG_PATH", target)
        return target

    def test_default_is_auto_on(self) -> None:
        from system_audio_asr.settings import DEFAULTS

        assert DEFAULTS["aiAutoSubmit"] is True, "默认值必须是 True（历史行为）"

    def test_reads_current_value(self, isolated_config) -> None:
        assert phone_share.load_auto_submit() is True
        isolated_config.write_text(json.dumps({"aiAutoSubmit": False}), encoding="utf-8")
        assert phone_share.load_auto_submit() is False

    def test_missing_key_defaults_to_auto_on(self, isolated_config) -> None:
        """配置里没有这个键时必须按「自动」处理。

        回 False 会让老配置升级后静默变成手动模式：用户不知道要按按钮，
        表现成「AI 突然不回答了」。
        """
        isolated_config.write_text(json.dumps({}), encoding="utf-8")
        assert phone_share.load_auto_submit() is True

    def test_broken_config_defaults_to_auto_on(self, isolated_config) -> None:
        isolated_config.write_text("[1,2,3]", encoding="utf-8")
        assert phone_share.load_auto_submit() is True

    def test_set_writes_only_that_key(self, isolated_config) -> None:
        """只写 aiAutoSubmit 一个键，其余字段（含未知键）原样保留。"""
        isolated_config.write_text(
            json.dumps({"aiAutoSubmit": True, "resumeContext": "项目一", "futureKey": "x"}),
            encoding="utf-8",
        )
        assert phone_share.set_auto_submit(False) is False
        on_disk = json.loads(isolated_config.read_text(encoding="utf-8"))
        assert on_disk["aiAutoSubmit"] is False
        assert on_disk["resumeContext"] == "项目一", "改开关时动了其他配置"
        assert on_disk["futureKey"] == "x", "改开关时丢了未知键"

    def test_set_normalizes_truthy_values(self, isolated_config) -> None:
        assert phone_share.set_auto_submit("") is False
        assert phone_share.set_auto_submit(1) is True
        assert phone_share.set_auto_submit(None) is False


class TestHoverButtonAutoSubmitToggle:
    """悬浮窗悬停按钮排里的「自动/手动」切换按钮。

    需求原话：「按钮加到立刻发送给 ai 旁边」——指的是悬浮窗那排悬停图标按钮
    （清空上下文/暂停/锁定/隐藏），要在「问 AI」右侧加一个模式切换。

    这排按钮是**纯图标**的，所以这个开关必须靠图标+颜色表达当前状态
    （像「锁定」按钮那样），否则用户看不出现在处于哪种模式。
    """

    def test_button_exists_and_sits_next_to_ask(self) -> None:
        source = _overlay_source()
        assert "private readonly Border autoSubmitControl;" in source, "未声明按钮字段"
        assert "autoSubmitControl = MakeIconControl(" in source, "未构造按钮"
        # 顺序：问 AI 之后紧跟它（Children.Add 的先后即左右顺序）
        ask_at = source.index("controls.Children.Add(askControl);")
        auto_at = source.index("controls.Children.Add(autoSubmitControl);")
        reset_at = source.index("controls.Children.Add(resetControl);")
        assert ask_at < auto_at < reset_at, (
            "按钮不在「问 AI」右侧：顺序应为 ask → autoSubmit → reset → …"
        )

    def test_control_count_matches_button_list(self) -> None:
        """ControlCount 必须等于实际按钮数。

        命中检测用 (x / ControlSize) 算下标、宽度用 ControlCount * ControlSize，
        数量对不上会导致点不准或末尾按钮点不到（加第 5 个按钮时就踩过这个坑）。
        """
        body = _method_body("internal LockIndicatorWindow(OverlayWindow overlay)")
        added = body.count("controls.Children.Add(")
        match = re.search(r"private const int ControlCount = (\d+);", _overlay_source())
        assert match, "未找到 ControlCount"
        assert int(match.group(1)) == added, (
            f"ControlCount={match.group(1)} 但实际加了 {added} 个按钮"
        )

    def test_activate_routes_to_toggle(self) -> None:
        body = _method_body("internal void ActivateControl(int index)")
        assert "overlay.ToggleAutoSubmit()" in body, "点击未触发切换"
        assert "control_auto_submit_click" in body, "缺少点击日志（排查时看不到操作）"

    def test_state_is_visible_via_icon_and_color(self) -> None:
        """状态必须体现在图标与颜色上（纯图标按钮唯一能表达状态的方式）。"""
        body = _method_body("internal void UpdateAutoSubmitState(bool auto)")
        assert 'auto ? "\\uE916" : "\\uE815"' in body, (
            "图标未随状态切换：用户无法从按钮看出当前是自动还是手动"
        )
        assert "stateBrush" in body, "颜色未随状态切换"
        assert "ToolTip" in body, "未更新悬停提示"

    def test_toggle_persists_and_cancels_pending_auto(self) -> None:
        """切换要落盘，并在切到手动时取消已排队的自动提交。

        不落盘 → 重启后又是自动；不取消计时器 → 刚关掉又被自动发一次。
        """
        body = _method_body("internal void SetAutoSubmit(bool auto)")
        assert "config.AiAutoSubmit = auto;" in body, "未写入配置"
        assert "SaveConfig()" in body, "未落盘：重启后被重置"
        assert "aiTimer.Stop()" in body, "切到手动时没取消已排队的自动提交"
        assert "ShowToast" in body, "缺少可见反馈"

    def test_button_state_refreshes_on_all_config_entries(self) -> None:
        """四个入口（启动/热重载/设置窗/切换本身）都要刷新按钮状态。

        漏掉热重载，桌面按钮会与手机端/网页端改过的值不一致。
        """
        source = _overlay_source()
        calls = source.count("lockIndicator.UpdateAutoSubmitState(")
        assert calls >= 3, (
            f"只有 {calls} 处刷新按钮状态（应 ≥3：切换本身 + ApplyAiSettings + 配置热重载）"
        )
        assert "RefreshAutoSubmitButton" in source, "设置窗路径缺少转发方法"

    def test_initial_state_refreshed_on_startup(self) -> None:
        """启动时就要按配置刷新一次，否则图标显示与实际模式相反。

        踩过的坑：构造函数把初始字形写死为「手动」态，而 Loaded 里只设了
        configLastWrite = 文件时间 —— 热重载的第一道判断是「文件时间没变就返回」，
        所以启动后**不会**有任何刷新路径校正它。配置是自动却显示手动图标，
        只有用户点一下或从别处改配置才会变对。
        """
        source = _overlay_source()
        # Loaded 回调里必须有初始刷新
        loaded_start = source.index("Loaded += delegate")
        loaded_end = source.index("};", loaded_start)
        block = source[loaded_start:loaded_end]
        assert "UpdateAutoSubmitState" in block, (
            "启动（Loaded）时没有刷新按钮状态：图标会停在构造时的默认值"
        )

    def test_hover_path_also_refreshes_button(self) -> None:
        """悬停显示那排按钮时也要刷新。

        「锁定」按钮就是在悬停分支里刷新的（lockIndicator.UpdateState）；
        新按钮若漏在这处，悬停也纠正不了错误的初始状态。
        """
        body = _method_body("private void PollLockHover()")
        assert "lockIndicator.UpdateState(config.Locked);" in body, "锁定按钮的刷新点不见了"
        assert "UpdateAutoSubmitState" in body, (
            "悬停分支没有刷新自动/手动按钮：显示时图标可能是过期的"
        )

    def test_manual_state_icon_is_not_a_power_symbol(self) -> None:
        """手动态图标不能是电源符号（E7E8）。

        E7E8 在 Segoe MDL2 里是电源开关，用户看到会以为「AI 被关掉了」，
        而实际只是「改成手动提交」。用 E815（手指点击）表达「需要你手动点」。
        """
        body = _method_body("internal void UpdateAutoSubmitState(bool auto)")
        assert "\\uE7E8" not in body, (
            "手动态仍在用电源图标（E7E8）：语义是「关机」而不是「手动」"
        )
        assert "\\uE815" in body, "手动态未改用手指图标（E815）"
        # 自动态用秒表（表示"等一会儿自动发"）
        assert "\\uE916" in body, "自动态图标不是秒表（E916）"
        # 构造函数里的初始字形也要一致（不能残留电源图标）
        ctor = _method_body("internal LockIndicatorWindow(OverlayWindow overlay)")
        assert "\\uE7E8" not in ctor, "构造函数里仍写着电源图标作为初始字形"

    def test_start_ai_request_guards_on_auto_submit(self) -> None:
        """自动提交的最终出口必须自己判断模式，而不是依赖调用方先停掉计时器。

        这是一个真实的竞态：静音计时器（默认 0.6 秒）已经启动后，用户从
        **设置窗**或**手机/网页**切到手动 —— 那两条路径只在「AI 整体被关闭」
        时才 Stop 计时器（`if (!enabled)` / `if (!config.AiEnabled)`），
        切模式并不会停它。于是一个已经在途的自动提交照样会把内容发出去，
        用户刚关掉自动却又被自动提交了一次（正是这个功能想解决的痛点）。
        只有悬浮窗按钮那条路径（SetAutoSubmit）主动停了计时器。

        把守卫放在 StartAiRequest（而不是逐个调用点补 Stop）才是可靠的：
        计时器经过这里，将来任何新调用点也都会经过。
        """
        body = _method_body("private async void StartAiRequest(")
        head = body[:500]
        assert "AiAutoSubmit" in head, (
            "StartAiRequest 未判断 AiAutoSubmit：从设置窗/手机切到手动后，"
            "已启动的静音计时器到点仍会把内容自动发出去"
        )

    def test_manual_submit_bypasses_the_guard(self) -> None:
        """守卫不能把手动提交也挡掉 —— 手动模式正是「问 AI」按钮要工作的场景。"""
        body = _method_body("private async void StartAiRequest(")
        head = body[:500]
        # 守卫必须是「自动调用才拦」的形式：计时器走默认参数（自动），
        # SubmitAiNow 显式传 manual=true
        assert "manual" in head, (
            "守卫没有区分调用来源：加判断后会连手动提交一起挡掉（按钮失效）"
        )
        submit = _method_body("internal void SubmitAiNow()")
        assert "StartAiRequest(" in submit and "true" in submit, (
            "SubmitAiNow 没有以「手动」身份调用 StartAiRequest：会被模式守卫拦下"
        )
        # 计时器那条路径必须是自动身份（不传 manual 或显式 false）
        source = _overlay_source()
        tick_start = source.index("aiTimer.Tick +=")
        tick_block = source[tick_start : tick_start + 260]
        assert "StartAiRequest()" in tick_block, (
            "计时器调用方式变了：必须走默认（自动）身份，否则守卫形同虚设"
        )

    def test_manual_submit_refusals_reach_the_phone(self) -> None:
        """手机点「问 AI」被桌面拒绝时，必须把原因告诉手机。

        真实场景：手机上点按钮，而电脑端「没有待提交的新内容 / 未配 Key /
        上一个请求正在进行中 / AI 未启用」—— 这些分支此前只调 ShowToast，
        提示条在电脑屏幕上，用户在看手机，于是**完全看不到任何反馈**，
        只会以为按钮坏了并反复点。

        修法：这些分支统一经 RefuseManualSubmit 处理，它同时发桌面提示条
        与手机提示（ManualSubmitFeed → /api/phone/notice → 手机 notice 帧）。
        """
        body = _method_body("internal void SubmitAiNow()")
        assert "RefuseManualSubmit" in body, (
            "拒绝分支只写了桌面提示条：手机点按钮被拒时收不到任何反馈"
        )
        # 四条拒绝理由都要走同一个出口（不能只给其中一两条加）
        refusals = [
            "AI 未启用",
            "未配置 API Key",
            "还在进行中",
            "没有待提交的新内容",
        ]
        for reason in refusals:
            assert reason in body, f"缺少拒绝理由「{reason}」"
        # 出口本身要真的发给手机
        refuse_body = _method_body("private void RefuseManualSubmit(string reason)")
        assert "ShowToast(reason)" in refuse_body, "桌面提示条丢了"
        assert "ManualSubmitFeed.Post(reason)" in refuse_body, (
            "没有把拒绝原因发给手机"
        )

    def test_notice_channel_is_separate_from_answer_stream(self) -> None:
        """提示要走独立端点，不能混进流式回答通道。

        PhoneAiFeed 是节流合并 + 带 done/封口语义的通道（手机据此维护气泡状态），
        把提示塞进去会干扰状态机（例如空文本被当成「封口」而什么都不显示）。
        """
        source = _overlay_source()
        feed_start = source.index("internal static class ManualSubmitFeed")
        feed_end = source.index("\n    internal ", feed_start + 10)
        feed = source[feed_start:feed_end]
        assert "/api/phone/notice" in feed, "提示端点不是独立的"
        assert "PhoneAiFeed" not in feed, "提示复用了流式回答通道"
        # 手机端要处理该帧
        phone = _PHONE_HTML.read_text(encoding="utf-8")
        assert 'case "notice"' in phone, "手机端没有处理 notice 帧"
        # 服务端要有对应路由，且只允许本机调用
        server = (_ROOT / "system_audio_asr" / "server.py").read_text(encoding="utf-8")
        assert '"/api/phone/notice"' in server, "服务端缺少该路由"
        route_at = server.index('"/api/phone/notice"')
        route_block = server[route_at : route_at + 400]
        assert "require_local(request)" in route_block, "该路由未限本机访问"


class TestDesktopSettingsWindowWiring:
    """C# 桌面设置窗（Ctrl+Alt+O）的自动/手动开关必须两头接线。

    为什么专门测这个：设置窗关闭时 ApplyAllSettings() → ApplyAiSettings(...)
    会把 20 多个字段**整体写回配置**。新控件若不参与这条链路，用户每次开关
    设置窗都会把别处设的值覆盖掉 —— 与「自定义模型名被重置成预设值」是同一个坑。
    """

    def test_control_is_declared_and_constructed(self) -> None:
        source = _overlay_source()
        assert "private readonly CheckBox aiAutoSubmitBox;" in source, "未声明字段"
        assert "aiAutoSubmitBox = new CheckBox();" in source, "未构造控件"
        assert "aiRoot.Children.Add(aiAutoSubmitBox);" in source, "控件未加入界面"

    def test_sync_reads_config_into_control(self) -> None:
        """配置 → 界面：不接这条，打开设置窗永远显示默认状态。"""
        body = _method_body("internal void Sync(OverlayConfig config)")
        assert "aiAutoSubmitBox.IsChecked = config.AiAutoSubmit;" in body, (
            "Sync 未回填 aiAutoSubmit：打开设置窗看到的勾选状态与实际不符"
        )

    def test_apply_writes_control_into_config(self) -> None:
        """界面 → 配置：不接这条，控件形同虚设（改了不保存）。"""
        signature = "internal void ApplyAiSettings("
        body = _method_body(signature)
        assert "bool autoSubmit" in body, "ApplyAiSettings 没有接收 autoSubmit 参数"
        assert "config.AiAutoSubmit = autoSubmit;" in body, (
            "ApplyAiSettings 没有把开关写进配置：设置窗改了不生效"
        )
        # 调用点必须把控件值传进去，否则参数永远收到默认 false
        source = _overlay_source()
        call = source.index("overlay.ApplyAiSettings(")
        call_block = source[call : call + 1400]
        assert "aiAutoSubmitBox.IsChecked == true" in call_block, (
            "ApplyAllSettings 调用时没有把控件状态传进去"
        )

    def test_toggle_applies_immediately(self) -> None:
        """点复选框要立即生效（与「停顿触发」滑块同一模式）。"""
        source = _overlay_source()
        start = source.index("aiAutoSubmitBox = new CheckBox();")
        block = source[start : start + 700]
        assert "ApplyAllSettings()" in block, "切换后未立即应用，要等关闭窗口才生效"

    def test_slider_is_disabled_when_auto_is_off(self) -> None:
        """关掉自动提交时，「停顿触发」滑块必须置灰并换说明。

        那个滑块只管等待多久后自动提交；关掉自动后它调了没有任何效果，
        不置灰会让用户以为「调了没生效 = 坏了」。
        """
        body = _method_body("private void UpdateAutoSubmitHint()")
        assert "aiDelaySlider.IsEnabled = auto" in body, "未联动置灰「停顿触发」滑块"
        # 两种模式的说明文字都要有
        assert "自动提问" in body and "手动提问" in body, "缺少模式说明文字"
        # Sync 与 ApplyAllSettings 都要刷新（两处入口都要对）
        source = _overlay_source()
        assert source.count("UpdateAutoSubmitHint();") >= 2, (
            "刷新只在其中一处调用：另一个入口下联动状态会不同步"
        )

    def test_hint_refresh_tolerates_construction_order(self) -> None:
        """刷新方法要容忍控件尚未构造（构造过程中会先于滑块被调用）。"""
        body = _method_body("private void UpdateAutoSubmitHint()")
        assert "aiDelaySlider != null" in body, "未判空：构造顺序变化会抛 NullReference"
        assert "aiAutoSubmitHint == null" in body, "未判空"


class TestHelloFrameCarriesAutoSubmit:
    """hello 帧必须带 aiAutoSubmit，否则刷新后界面与真实行为相反。"""

    def test_hello_includes_ai_auto_submit(self, tmp_path: Path, monkeypatch) -> None:
        source = Path(phone_share.__file__).read_text(encoding="utf-8")
        start = source.index('"type": "hello"')
        block = source[start : start + 1600]
        assert "aiAutoSubmit" in block, "hello 帧未下发 aiAutoSubmit"
