"""走生产路径验证手机端代码块渲染：stripMarkdown → renderChatBody 组合。

为什么需要这个工具：单独测 renderChatBody 会漏掉「上游把输入吃掉」的问题。
真实链路是 handleText → stripMarkdown → appendChat → setChatContent → renderChatBody，
其中 stripMarkdown 曾把 ``` 围栏行删成空行、把反引号全部删掉，导致 renderChatBody
永远解析不到围栏 —— 代码块渲染在真实使用中完全不生效，而单独调用 renderChatBody
的测试却是通过的（这个错误测法真实发生过）。

因此这里从 phone.html **抽出真实函数体**，在 node 里按生产顺序组合执行，
而不是复刻一份等价逻辑（复刻会随源码漂移，测的就不是真实代码了）。

用法：python tools/check_phone_render.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHONE = ROOT / "system_audio_asr" / "web" / "phone.html"

# 需要抽出来一起跑的函数（顺序无关，均为 function 声明，靠 hoisting 互相可见）
WANTED = ("stripMarkdown", "renderChatBody", "setChatContent", "chatPrefix", "closeOpenBubble")

# 生产链路里各消息源都会先经过 stripMarkdown（见 phone.html 的 handleText）
SAMPLE = """先用前缀和把区间查询降到 O(1)：

```cpp
int main() {
    int n, q;
    cin >> n >> q;
    for (int i = 1; i <= n; ++i) {
        cin >> a[i];
        s[i] = s[i - 1] + a[i];
    }
    return 0;
}
```

**时间复杂度**是 `O(n + q)`，空间 `O(n)`。

# 补充说明
注意边界。
"""

EXPECTED_CODE = (
    "int main() {\n"
    "    int n, q;\n"
    "    cin >> n >> q;\n"
    "    for (int i = 1; i <= n; ++i) {\n"
    "        cin >> a[i];\n"
    "        s[i] = s[i - 1] + a[i];\n"
    "    }\n"
    "    return 0;\n"
    "}"
)

# 最小 DOM 桩：只覆盖被测函数用到的成员。作为独立模块被 require 进被测代码。
DOM_STUB = """'use strict';
function makeElement(tag){
  var el = {
    tagName: tag,
    className: '',
    _text: '',
    children: [],
    style: {},
    dataset: {},
    set textContent(value){
      this._text = String(value);
      this.children = [];
    },
    get textContent(){
      return this._text + this.children.map(function(c){ return c.textContent; }).join('');
    },
    appendChild(child){
      this.children.push(child);
      return child;
    },
    // closeOpenBubble 用 lastChild 找最后一条气泡，并在其上 querySelector 取正文
    get lastChild(){
      return this.children.length ? this.children[this.children.length - 1] : null;
    },
    querySelector(selector){
      // 只需支持 "span:last-child" 这一种（取最后一个 span 子节点）
      if (selector === 'span:last-child') {
        var spans = this.children.filter(function(c){ return c.tagName === 'span'; });
        return spans.length ? spans[spans.length - 1] : null;
      }
      return null;
    },
    querySelectorAll(selector){
      var byClass = selector.charAt(0) === '.';
      var want = byClass ? selector.slice(1) : selector;
      var out = [];
      (function walk(node){
        (node.children || []).forEach(function(child){
          var hit = byClass
            ? String(child.className || '').split(/\\s+/).indexOf(want) >= 0
            : child.tagName === want;
          if (hit) out.push(child);
          walk(child);
        });
      })(this);
      return out;
    }
  };
  return el;
}
globalThis.makeElement = makeElement;
globalThis.document = {
  createElement: makeElement,
  createTextNode: function(text){
    return { tagName: '', textContent: String(text), children: [], className: '', style: {} };
  }
};
// closeOpenBubble 依赖全局 $chatFlow（真实页面里是 getElementById 的结果）
globalThis.$chatFlow = makeElement('div');
"""

# 以模块方式加载被测函数：把抽取出的函数体写成模块，导出后由 runner 调用。
# 不使用 eval / new Function —— 被测内容是仓库源码，但仍按普通模块加载。
MODULE_FOOTER = """
module.exports = { stripMarkdown, renderChatBody, setChatContent, chatPrefix, closeOpenBubble };
"""

RUNNER = """'use strict';
require('./dom_stub.js');
const { stripMarkdown, setChatContent, chatPrefix, closeOpenBubble } = require('./under_test.js');
// 必须 JSON.parse：argv 传进来的换行是字面量 \\n 转义，
// 直接当字符串用会让整段样本变成「一行」，围栏检测与逐行清理全部失效。
const sample = JSON.parse(process.argv[2]);

// 生产顺序：stripMarkdown 先跑，再进气泡渲染
const stripped = stripMarkdown(sample);
const bubble = makeElement('div');
setChatContent(bubble, '\\u{1F4F8} ', stripped, false);

const codeBlocks = bubble.querySelectorAll('.code-block');
const inlineCode = bubble.querySelectorAll('.inline-code');

// 空文本 done 帧的封口：模拟一条正在流式的桌面回答气泡，再调用 closeOpenBubble
const flow = globalThis.$chatFlow;
const liveBubble = makeElement('div');
liveBubble.dataset.kind = 'chat-desktop';
liveBubble.dataset.open = '1';
liveBubble.dataset.turn = '7';
setChatContent(liveBubble, chatPrefix('desktop'), '正在生成的回答', true);
flow.appendChild(liveBubble);
const beforeClose = { open: liveBubble.dataset.open, hasCursor: liveBubble.textContent.indexOf('\\u258D') >= 0 };
closeOpenBubble();
const afterClose = {
  open: liveBubble.dataset.open,
  hasCursor: liveBubble.textContent.indexOf('\\u258D') >= 0,
  text: liveBubble.textContent
};

process.stdout.write(JSON.stringify({
  codeBlockCount: codeBlocks.length,
  inlineCodeCount: inlineCode.length,
  codeText: codeBlocks.length ? codeBlocks[0].textContent : null,
  proseHasBoldMarkers: bubble.textContent.indexOf('**') >= 0,
  proseHasHeadingMarkers: bubble.textContent.indexOf('# 补充说明') >= 0,
  beforeClose: beforeClose,
  afterClose: afterClose
}));
"""


def extract_functions(html: str) -> str:
    """按大括号配平抽取函数声明，避免依赖行号（加注释就会失准）。"""
    chunks: list[str] = []
    for name in WANTED:
        match = re.search(r"^function\s+" + re.escape(name) + r"\s*\(", html, re.M)
        if not match:
            raise SystemExit(f"未在 phone.html 里找到函数 {name}")
        start = match.start()
        # 从函数体首个 { 开始配平（跳过参数表里的括号）
        index = html.index("{", match.end())
        depth = 0
        while index < len(html):
            char = html[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        chunks.append(html[start : index + 1])
    return "\n".join(chunks) + MODULE_FOOTER


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("未安装 node，跳过手机端渲染检查")
        return 0

    html = PHONE.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as work:
        work_dir = Path(work)
        (work_dir / "dom_stub.js").write_text(DOM_STUB, encoding="utf-8")
        (work_dir / "under_test.js").write_text(
            extract_functions(html), encoding="utf-8"
        )
        (work_dir / "runner.js").write_text(RUNNER, encoding="utf-8")
        done = subprocess.run(
            [node, str(work_dir / "runner.js"), json.dumps(SAMPLE)],
            capture_output=True,
            shell=False,
            check=False,
        )

    if done.returncode != 0:
        print("渲染检查脚本执行失败：")
        print(done.stderr.decode("utf-8", errors="replace"))
        return 1

    try:
        result = json.loads(done.stdout.decode("utf-8").strip())
    except ValueError:
        print("无法解析检查结果：", done.stdout.decode("utf-8", errors="replace"))
        return 1

    problems: list[str] = []

    # ① 围栏必须真的渲染成代码块（这正是此前失效的点）
    if result["codeBlockCount"] != 1:
        problems.append(
            f"代码块数量是 {result['codeBlockCount']}，应为 1 —— "
            "围栏多半在 stripMarkdown 阶段被删掉了"
        )
    # ② 代码内容与缩进必须逐字保留
    if result["codeText"] != EXPECTED_CODE:
        problems.append("代码内容与缩进未逐字保留：\n  实际=" + repr(result["codeText"]))
    # ③ 行内代码（O(n + q) / O(n)）
    if result["inlineCodeCount"] != 2:
        problems.append(
            f"行内代码数量是 {result['inlineCodeCount']}，应为 2 —— "
            "反引号可能被 stripMarkdown 删掉了"
        )
    # ④ 正文的加粗/标题标记仍要清理（stripMarkdown 的原有职责不能丢）
    if result["proseHasBoldMarkers"]:
        problems.append("正文里仍残留 ** 加粗标记")
    if result["proseHasHeadingMarkers"]:
        problems.append("正文里仍残留 # 标题标记")
    # ⑤ 空文本 done 帧必须把在流式的那条气泡封口（去掉光标、置 open=0）。
    #    桌面取消请求/重置会话时发的就是 {text:"", done:true}；不封口的话
    #    气泡会永远停在「正在生成」，后续同轮帧还会继续往这条旧气泡里合并。
    if not result["beforeClose"]["hasCursor"]:
        problems.append("前置条件不成立：流式气泡里没找到光标字符（样本或桩有问题）")
    if result["afterClose"]["open"] != "0":
        problems.append(
            f"空 done 帧后气泡仍为 open={result['afterClose']['open']!r}，"
            "封口没有生效（气泡会永远停在流式状态）"
        )
    if result["afterClose"]["hasCursor"]:
        problems.append("空 done 帧后光标 ▍ 仍在，气泡看起来还在生成")
    if "正在生成的回答" not in result["afterClose"]["text"]:
        problems.append("封口时把已有正文弄丢了：" + repr(result["afterClose"]["text"]))

    if problems:
        print("手机端代码块渲染检查未通过：")
        for item in problems:
            print("  -", item)
        return 1

    print("OK    手机端代码块渲染（stripMarkdown → renderChatBody 生产路径）")
    print(f"      代码块 1 个 · 行内代码 {result['inlineCodeCount']} 个 · 缩进逐字保留")
    print("OK    空 done 帧封口（closeOpenBubble 去掉光标并保留正文）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
