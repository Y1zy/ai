"""设置页/手机页前端静态契约测试。

这里不启动浏览器，只对 HTML 里的关键结构与脚本做契约断言——覆盖的是
「改一处忘了改另一处」这类容易静默失效的问题（字段缺失导致页面留白、
新增控件没接上保存/加载、脚本语法错误导致整页不执行）。
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SETTINGS = _ROOT / "system_audio_asr" / "web" / "settings.html"
_PHONE = _ROOT / "system_audio_asr" / "web" / "phone.html"


def _html(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _markup(path: Path) -> str:
    text = _html(path)
    return text[: text.index("<script")]


def _script(path: Path) -> str:
    text = _html(path)
    match = re.search(r"<script[^>]*>(.*?)</script>", text, re.S)
    assert match, f"{path.name} 里没有内联脚本"
    return match.group(1)


def test_settings_script_parses_as_javascript() -> None:
    """内联脚本语法错误会让整页脚本不执行——所有输入框保持空白。"""
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        import pytest

        pytest.skip("未安装 node，跳过 JS 语法检查")
    with tempfile.TemporaryDirectory() as work:
        target = Path(work) / "check.js"
        target.write_text(_script(_SETTINGS), encoding="utf-8")
        done = subprocess.run(
            [node, "--check", str(target)],
            capture_output=True,
            shell=False,
            check=False,
        )
        assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")


def test_every_load_id_exists_in_markup() -> None:
    """load() 遍历的每个 id 都必须存在于 HTML，否则 el.type 取值会抛错并中断加载。"""
    markup = _markup(_SETTINGS)
    defined = set(re.findall(r'id="([A-Za-z0-9_]+)"', markup))
    ids_match = re.search(r"const ids=\[([^\]]+)\]", _script(_SETTINGS))
    assert ids_match, "未找到设置页的 ids 列表"
    ids = re.findall(r"'([A-Za-z0-9_]+)'", ids_match.group(1))
    assert ids, "ids 列表解析为空"
    missing = [item for item in ids if item not in defined]
    assert not missing, f"这些 id 在 HTML 里不存在，load() 会中断: {missing}"


def test_every_dollar_id_exists_in_markup() -> None:
    """脚本里 $('...') 引用的 id 都必须存在：任一为 null 会中断后续所有初始化。"""
    markup = _markup(_SETTINGS)
    defined = set(re.findall(r'id="([A-Za-z0-9_]+)"', markup))
    referenced = set(re.findall(r"\$\('([A-Za-z0-9_]+)'\)", _script(_SETTINGS)))
    missing = sorted(referenced - defined)
    assert not missing, f"脚本引用了不存在的元素 id: {missing}"


def test_settings_warns_when_config_keys_missing() -> None:
    """配置键缺失时必须给出可见告警，而不是静默留空（用户会以为资料丢了）。"""
    script = _script(_SETTINGS)
    assert "renderMissingKeys" in script, "缺少「配置键缺失」的可见告警逻辑"
    assert "missingKeysWarn" in _html(_SETTINGS), "缺少告警容器元素"
    assert "LONG_TERM_KEYS" in script, "未区分长期资料字段，告警无法提示风险"


def test_phone_actionbar_is_fixed_at_bottom() -> None:
    """手机操作栏必须吸底固定：面试中单手反复触发的三个动作要始终可点。"""
    text = _html(_PHONE)
    assert 'class="actionbar"' in text, "操作栏未改为 .actionbar"
    style = re.search(r"\.actionbar\{([^}]*)\}", text)
    assert style, "未找到 .actionbar 样式"
    body = style.group(1)
    assert "position:fixed" in body and "bottom:0" in body
    assert "safe-area-inset-bottom" in body, "未适配 iPhone 底部安全区"
    # 操作栏必须在 <main> 之外，否则会跟着内容滚动
    main_start = text.index("<main")
    main_end = text.index("</main>")
    assert text.index('class="actionbar"') > main_end, "操作栏仍在 <main> 内，会随滚动跑掉"


def test_phone_clipboard_card_is_collapsed_by_default() -> None:
    """剪贴板卡片默认折叠，且不得在收到电脑剪贴板内容时强制展开。"""
    text = _html(_PHONE)
    assert '<details id="clipboard-card"' in text
    assert re.search(r'<details id="clipboard-card"[^>]*\bopen\b', text) is None, (
        "剪贴板卡片带了 open 属性，默认就是展开的"
    )
    script = _script(_PHONE)
    assert "card.open = true" not in script and "card.open=true" not in script, (
        "仍在收到剪贴板内容时强制展开，会反复顶动页面打断阅读"
    )


def test_phone_has_debug_panel() -> None:
    """手机端必须有诊断面板，且诊断消息只用于订阅、不能写配置。"""
    text = _html(_PHONE)
    assert 'id="debug-card"' in text, "缺少诊断面板"
    script = _script(_PHONE)
    assert 'type:"debug"' in script or "type:'debug'" in script, "诊断面板未发订阅消息"


def test_phone_has_answer_mode_toggle() -> None:
    """手机端必须能切换作答模式（核心代码 / ACM）。"""
    text = _html(_PHONE)
    script = _script(_PHONE)
    assert "mode-toggle" in text, "手机端缺少作答模式切换按钮"
    assert "vision_mode" in script, "作答模式切换未走 relay 消息"


def test_phone_has_thinking_mode_toggle() -> None:
    """手机端必须能切换解题思考模式，且走 relay。"""
    text = _html(_PHONE)
    script = _script(_PHONE)
    assert 'id="think-toggle"' in text, "手机端缺少思考模式切换按钮"
    assert "vision_thinking" in script, "思考模式切换未走 relay 消息"
    # 三态循环必须都能到达，否则某个状态无法选择
    for state in ('"off"', '"auto"'):
        assert state in script, f"思考模式缺少状态 {state}"


def test_phone_thinking_cycle_covers_all_tiers() -> None:
    """四挡循环必须真的能到达推理档（medium/high），且用环形取模而不是硬编码。

    硬编码三元表达式每加一档都要改写，容易漏掉新档 —— 表现是点了按钮却
    只在旧档之间打转，永远切不到新加的档位。
    """
    text = _html(_PHONE)
    script = _script(_PHONE)
    for tier in ("medium", "high"):
        assert f'"{tier}"' in script, f"手机端缺少思考档位 {tier}"
        assert f'"{tier}"' in text, f"缺少 {tier} 档的界面文案"
    assert "THINKING_ORDER" in script, "未用统一档位序列表驱动循环"
    # 循环要能回到起点，否则会卡在最后一档
    order = re.search(r"THINKING_ORDER\s*=\s*\[([^\]]*)\]", script)
    assert order, "未找到 THINKING_ORDER"
    tiers = re.findall(r'"([^"]*)"', order.group(1))
    assert tiers == ["", "off", "auto", "medium", "high"], f"档位顺序异常: {tiers}"
    # 与后端白名单同步
    from system_audio_asr.ai_stream import THINKING_MODES

    assert tiers[1:] == list(THINKING_MODES), "手机档位与后端白名单不一致"


def test_phone_reasoning_tiers_warn_about_slowness() -> None:
    """推理档实测 20-35 秒，界面上必须让用户知道会变慢，且用更醒目的配色。"""
    script = _script(_PHONE)
    assert "THINKING_SLOW" in script, "推理档未做「会变慢」的视觉区分"
    assert "23" in script or "秒" in script, "推理档提示未说明耗时"


def test_phone_has_find_bug_button() -> None:
    """手机端必须有独立的「找 Bug」按钮，走 solve_bug 消息。"""
    text = _html(_PHONE)
    script = _script(_PHONE)
    assert 'id="bug-btn"' in text, "手机端缺少找 Bug 按钮"
    assert "solve_bug" in script, "找 Bug 未走独立的 relay 消息"


def test_phone_renders_code_blocks_in_bubble() -> None:
    """代码块要在气泡内渲染成独立样式，而不是让 stripMarkdown 把围栏删掉。

    此前 stripMarkdown 会删掉 ``` 与反引号，代码与正文混在一起、缩进丢失。
    """
    text = _html(_PHONE)
    script = _script(_PHONE)
    assert "renderChatBody" in script, "缺少代码块渲染函数"
    assert "code-block" in text, "缺少代码块样式"
    style = re.search(r"\.code-block\{([^}]*)\}", text)
    assert style, "未找到 .code-block 样式定义"
    body = style.group(1)
    assert "monospace" in body, "代码块未使用等宽字体"
    assert "overflow-x:auto" in body, "长代码无法横向滚动（会被挤到折行抄错）"
    # 流式期间不能渲染：围栏只闭合到一半会把正文误判成代码
    assert "setChatContent" in script, "缺少气泡内容写入函数"
    assert "streaming" in script


def test_phone_code_blocks_render_through_production_path() -> None:
    """代码块必须经**生产路径**（stripMarkdown → renderChatBody）真实渲染出来。

    这条用例的价值在于纠正一类错误测法：单独调用 renderChatBody 传入带围栏的原文
    会通过，但真实链路是 handleText → stripMarkdown → appendChat → setChatContent，
    stripMarkdown 曾把围栏行删成空行、把反引号全删掉，导致代码块永远渲染不出来。
    因此这里调用 tools/check_phone_render.py：它从 phone.html 抽出真实函数体，
    在 node 里按生产顺序组合执行并断言产出。
    """
    import shutil
    import subprocess

    if shutil.which("node") is None:
        import pytest

        pytest.skip("未安装 node，跳过手机端渲染链路检查")
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [str(root / ".venv" / "Scripts" / "python.exe"), str(root / "tools" / "check_phone_render.py")]
        if (root / ".venv" / "Scripts" / "python.exe").exists()
        else ["python", str(root / "tools" / "check_phone_render.py")],
        capture_output=True,
        shell=False,
        check=False,
    )
    output = done.stdout.decode("utf-8", errors="replace") + done.stderr.decode(
        "utf-8", errors="replace"
    )
    assert done.returncode == 0, "生产路径下的代码块渲染失败：\n" + output


def test_strip_markdown_keeps_fences() -> None:
    """stripMarkdown 必须保留围栏与行内反引号 —— 它们是下游渲染的唯一依据。

    只做源码契约断言（真实行为由上面的生产路径检查覆盖）。
    """
    script = _script(_PHONE)
    start = script.index("function stripMarkdown")
    body = script[start : start + 1400]
    assert "inCode" in body, "stripMarkdown 未区分代码段与正文段"
    # 围栏行必须原样 push（不能再 return ""）
    assert "out.push(line)" in body, "stripMarkdown 仍在丢弃围栏行"
    assert 'replace(/`/g,"")' not in body, "stripMarkdown 仍在删行内反引号"


def test_phone_code_rendering_avoids_innerhtml_for_model_text() -> None:
    """回答内容来自模型（可能受截图影响），渲染它时不得拼 innerHTML。

    页面其它地方（如 setErr 拼固定结构的错误条）用 innerHTML 与本风险无关，
    因此这里只锁定回答渲染函数本身。
    """
    script = _script(_PHONE)
    for fn in ("renderChatBody", "setChatContent"):
        start = script.index("function " + fn)
        body = script[start : start + 2200]
        for sink in ("innerHTML", "insertAdjacentHTML", "document.write"):
            assert sink not in body, f"{fn} 用了 {sink}：模型输出拼 HTML 会引入注入面"
    # 反过来确认真的走了安全的 DOM 构建
    assert "createTextNode" in script or "textContent" in script


def test_phone_has_multi_image_controls() -> None:
    """多图提交的三个控件都要在：加一图 / 清空 / 提交计数。

    缺「清空」时用户攒满上限就卡住了，只能先提交一次才能重截。
    """
    text = _html(_PHONE)
    script = _script(_PHONE)
    for control in ("add-img-btn", "clear-img-btn", "solve-btn"):
        assert f'id="{control}"' in text, f"手机端缺少控件 {control}"
    assert "solve_add" in script, "加一图未走 relay 消息"
    assert "solve_clear" in script, "清空未走 relay 消息"
    assert "applySolvePending" in script, "未处理服务端的待解张数广播"
    assert "solve_pending" in script, "未订阅待解张数变化"


def test_phone_screenshot_sits_at_top() -> None:
    """截图在最上面：打开页面先看到「电脑现在是什么画面」，这是投屏最主要的信息。

    其次才是 AI 对话（看完画面看回答），设置类再往下。
    """
    markup = _markup(_PHONE)
    img_at = markup.index('id="img-wrap"')
    chat_at = markup.index('id="chat-card"')
    settings_at = markup.index('id="shot-settings"')
    assert img_at < chat_at, "截图没有排在 AI 对话之前"
    assert chat_at < settings_at, "AI 对话没有排在截图设置之前"
    # 保存按钮紧扣它的图
    assert markup.index('id="img-actions"') < markup.index('id="chat-card"'), (
        "保存图片按钮没有紧跟截图"
    )


def test_phone_chat_header_buttons_fit_narrow_screen() -> None:
    """聊天卡片头部按钮数量要克制，并能换行。

    头部曾放 4 个按钮（作答模式/思考模式/放大/清空），375px 窄屏下总宽超出卡片，
    「清空」被挤出屏幕右侧、点不到。解题相关的两个开关因此移到截图区的
    #shot-settings；头部只留放大/清空，并允许 flex-wrap 兜底。
    """
    markup = _markup(_PHONE)
    header_start = markup.index('id="chat-card"')
    header_end = markup.index('id="chat-flow"')
    header = markup[header_start:header_end]
    for moved in ("mode-toggle", "think-toggle"):
        assert moved not in header, f"{moved} 又回到拥挤的聊天头部了"
    assert 'id="chat-expand"' in header and 'id="chat-clear"' in header
    # 头部必须允许换行，避免以后再加按钮时又被挤出屏幕
    style = re.search(r'id="chat-card"[\s\S]{0,240}?style="([^"]*)"', markup)
    assert style and "flex-wrap" in style.group(1), "聊天头部未允许换行，窄屏下会溢出"


def test_phone_solve_options_grouped_in_shot_settings() -> None:
    """作答模式、思考模式、自动刷新三个控件要同属 #shot-settings 一个区块。

    它们都是截图相关的设置，成组出现才好找；散落各处会让人不知道去哪改。
    （区块在页面中的位置可以调整，但三个控件必须始终在同一个容器里。）
    """
    markup = _markup(_PHONE)
    start = markup.index('id="shot-settings"')
    end = markup.index('id="partial"')
    block = markup[start:end]
    for control in ("mode-toggle", "think-toggle", 'id="auto"'):
        assert control in block, f"{control} 不在截图设置区里"


def test_phone_shot_settings_hidden_in_expand_mode() -> None:
    """展开模式要收起整个截图设置区。

    设置区里的自动刷新开关原来是一个独立的 .switchline 直接子节点，移动后
    包进了 #shot-settings；若隐藏列表仍写着 .switchline，展开模式下这一整块
    会残留在聊天区上方。
    """
    text = _html(_PHONE)
    hide_list = re.search(
        r"body\.chat-expanded main > \.switchline,(.*?)\{display:none\}", text, re.S
    )
    assert hide_list, "未找到展开模式的隐藏列表"
    assert "#shot-settings" in hide_list.group(1), "展开模式未收起截图设置区"


def test_phone_save_button_hidden_in_expand_mode() -> None:
    """展开模式要连保存按钮一起收起。

    它现在贴在截图下方，而截图本身在展开模式下是隐藏的；只隐藏截图会让
    保存按钮孤零零留在页面上（点它还能存到上一次的截图，容易误操作）。
    """
    text = _html(_PHONE)
    hide_list = re.search(r"body\.chat-expanded main > \.switchline,(.*?)\{display:none\}", text, re.S)
    assert hide_list, "未找到展开模式的隐藏列表"
    body = hide_list.group(1)
    assert "#img-wrap" in body, "展开模式未隐藏截图"
    assert "#img-actions" in body, "展开模式未隐藏保存按钮（会与隐藏的截图脱节）"


def test_hotword_cleanup_requires_confirmation() -> None:
    """热词清理必须让用户确认后才删。

    长度区分不了碎片与合法术语（「消息队列中间件」与「像素数据解码与」都是 7 字），
    而删掉再保存会永久覆盖手动热词，因此必须把将删的词列给用户确认。
    """
    script = _script(_SETTINGS)
    start = script.index("function cleanHotwordFragments")
    body = script[start : start + 2200]
    assert "window.confirm" in body, "热词清理没有确认步骤（会静默删掉合法长词）"
    # 取消必须直接返回，不改动输入框
    assert "已取消" in body, "缺少取消分支的反馈"
    # 取消分支要在赋值之前返回
    cancel_at = body.index("if(!confirmed)")
    assign_at = body.index("$('hotwordExtra').value=final.join")
    assert cancel_at < assign_at, "取消判断放在了赋值之后（取消也会删）"
    # 提示里要列出将删的词
    assert "removed.slice(0,10)" in body or "preview" in body, "未列出将删除的词"


def test_hotword_cleanup_button_label_is_honest() -> None:
    """按钮文案要如实说明按字数删，而不是含糊的「清理碎片」。"""
    text = _html(_SETTINGS)
    assert "移除超 6 字的中文词" in text, "按钮文案未说明实际判定规则"
    hint = re.search(r'id="hotwordCleanHint"[^>]*>([^<]*)<', text)
    assert hint and "合法术语" in hint.group(1), "提示未说明可能误删合法术语"


def test_phone_actionbar_wraps_on_narrow_screens() -> None:
    """吸底栏按钮可折行：窄屏下不能把按钮挤到点不准或看不见。"""
    text = _html(_PHONE)
    style = re.search(r"\.actionbar\{([^}]*)\}", text)
    assert style and "flex-wrap:wrap" in style.group(1), "吸底栏未允许换行"
    button = re.search(r"\.actionbar \.btn\{([^}]*)\}", text)
    assert button and "white-space:normal" in button.group(1), "按钮文字未允许折行"
    # 内容底部留白要盖住吸底栏，否则最后一条回答会被挡
    main_style = re.search(r"main\{padding-bottom:(\d+)px\}", text)
    assert main_style and int(main_style.group(1)) >= 100, "底部留白不足以避开吸底栏"


def test_settings_has_vision_answer_mode_controls() -> None:
    """设置页三个解题独立控件都必须存在，并接入加载与保存。"""
    text = _html(_SETTINGS)
    script = _script(_SETTINGS)
    for control in ("visionThinkingMode", "visionAnswerMode", "visionMaxTokens"):
        assert f'id="{control}"' in text, f"设置页缺少控件 {control}"
        assert f"$('{control}')" in script, f"设置页脚本未引用 {control}"
    # 保存时三个键都要提交，否则改动不会落盘
    assert "visionAnswerMode:" in script


def test_context_total_limit_matches_python() -> None:
    """截图解题不再附带简历/JD：解题链路的请求体里不得出现面试上下文。"""
    from system_audio_asr import phone_share

    vision = phone_share.load_vision_config()
    assert "resume" not in vision and "jd" not in vision, (
        "解题链路又带上了简历/JD：题干在截图里已完整，带上只会拖慢首字"
    )
