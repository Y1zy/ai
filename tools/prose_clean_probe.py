"""把 C# 的「正文/代码分段」与「上下文截断」逻辑编译成探针，做真实行为验证。

验证三件事（前两件在字幕窗渲染链路，第三件在面试上下文拼接链路）：
  1. 正文段的 Markdown 标记（**加粗**、# 标题、行内反引号）要被清理，
     否则会原样显示给用户 —— 这是改用 AppendAiText 后引入的显示回归。
  2. 代码段必须逐字保留：缩进、空行、`#include` 的井号都不能动。
     `#include <iostream>` 若被当成标题前缀清掉，代码就废了。
  3. JoinContextSections 的截断语义必须与 Python settings.overlay_context_block
     一致：放不下的块按剩余额度截断加省略号，剩余太小才整块丢弃。

把方法抽出来单独编译（都是 internal static、不依赖实例状态），
测的是仓库里的真实代码，不是复刻的等价实现。

用法：python tools/prose_clean_probe.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY_CS = ROOT / "overlay_cs" / "OverlayApp.cs"

# 需要抽取的方法（internal static，自包含）
WANTED = ("CleanAiProse", "SplitCodeFences", "JoinContextSections")

# 需要一并搬运的常量（不是方法，单独按名字找）
WANTED_CONSTS = ("ContextTruncateMinRoom",)

_CSC_CANDIDATES = (
    Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
)


def _csc_path() -> Path | None:
    for candidate in _CSC_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def available() -> bool:
    return _csc_path() is not None


def _extract_methods() -> str:
    """按大括号配平抽取方法体，避免依赖行号。"""
    text = OVERLAY_CS.read_text(encoding="utf-8-sig")
    chunks: list[str] = []
    for name in WANTED:
        match = re.search(
            r"internal static [\w<>,\s\[\]]+\s+" + re.escape(name) + r"\s*\(", text
        )
        if not match:
            raise SystemExit(f"未在 OverlayApp.cs 里找到方法 {name}")
        index = text.index("{", match.end())
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
        chunks.append(text[match.start() : index + 1])
    # 常量字段：后续代码依赖它们，缺了编译不过
    for name in WANTED_CONSTS:
        match = re.search(
            r"internal const int " + re.escape(name) + r"\s*=\s*(\d+)\s*;", text
        )
        if not match:
            raise SystemExit(f"未在 OverlayApp.cs 里找到常量 {name}")
        chunks.append(f"internal const int {name} = {match.group(1)};")
    return "\n\n".join(chunks)


_PROBE_MAIN = textwrap.dedent(
    """
        internal static class Probe
        {
            internal static void Main()
            {
                // 覆盖真实场景：标题、加粗、行内反引号、C++ 代码（含 #include 与缩进）
                string sample = string.Join("\\n", new string[] {
                    "# 解题思路",
                    "",
                    "用**前缀和**把查询降到 `O(1)`：",
                    "",
                    "```cpp",
                    "#include <iostream>",
                    "int main() {",
                    "    int n, q;",
                    "    return 0;",
                    "}",
                    "```",
                    "",
                    "**复杂度** O(n)。"
                });
                System.Collections.Generic.List<
                    System.Collections.Generic.KeyValuePair<bool, string>> parts =
                    RenderHelpers.SplitCodeFences(sample);
                System.Collections.Generic.List<string> code =
                    new System.Collections.Generic.List<string>();
                System.Collections.Generic.List<string> prose =
                    new System.Collections.Generic.List<string>();
                foreach (var part in parts)
                {
                    if (part.Key) code.Add(part.Value); else prose.Add(part.Value);
                }
                var payload = new System.Collections.Generic.Dictionary<string, object>();
                payload["codeCount"] = code.Count;
                payload["codeText"] = code.Count > 0 ? code[0] : "";
                payload["proseText"] = string.Join("\\n", prose.ToArray());

                // 上下文截断：用与 Python 侧相同的输入，便于逐字对比
                // ① 第一块就超限 → 应按剩余额度截断并加省略号
                var one = new System.Collections.Generic.List<string>();
                one.Add(new string('A', 100) + "尾部");
                payload["truncateSingle"] = RenderHelpers.JoinContextSections(one, 40);
                // ② 第一块刚好放下、第二块放不下 → 截断第二块
                var two = new System.Collections.Generic.List<string>();
                two.Add(new string('B', 30));
                two.Add(new string('C', 100));
                payload["truncateSecond"] = RenderHelpers.JoinContextSections(two, 50);
                // ③ 剩余额度太小 → 整块丢弃
                var tight = new System.Collections.Generic.List<string>();
                tight.Add(new string('D', 45));
                tight.Add(new string('E', 100));
                payload["truncateTooTight"] = RenderHelpers.JoinContextSections(tight, 50);
                // ④ 全部放得下 → 不截断
                var roomy = new System.Collections.Generic.List<string>();
                roomy.Add("short");
                payload["truncateNoCut"] = RenderHelpers.JoinContextSections(roomy, 50);

                Console.Out.Write(new System.Web.Script.Serialization.JavaScriptSerializer()
                    .Serialize(payload));
            }
        }
    }
    """
)


def _build_source() -> str:
    return (
        textwrap.dedent(
            """\
            using System;
            using System.Collections.Generic;
            using System.Text;

            namespace WasapiParaformerOverlay
            {
            """
        )
        # 抽出的方法要包在类里：它们原属 OverlayWindow，静态且自包含，
        # 放到一个最小壳类里即可编译（不引入窗口/WPF 依赖）。
        + "    internal static class RenderHelpers\n    {\n"
        + textwrap.indent(_extract_methods(), "        ")
        + "\n    }\n"
        # _PROBE_MAIN 自带结束大括号，把剩下的命名空间收掉
        + _PROBE_MAIN
    )


def run() -> dict:
    """编译并运行探针，返回结果。"""
    compiler = _csc_path()
    if compiler is None:
        raise RuntimeError(".NET Framework C# 编译器不存在")

    work = Path(tempfile.mkdtemp(prefix="proseprobe-"))
    source_file = work / "Probe.cs"
    source_file.write_text(_build_source(), encoding="utf-8")
    exe = work / "Probe.exe"

    # 用本机 .NET 编译器编译刚生成的探针（固定参数，不经 shell）
    build = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            "/out:" + str(exe),
            "/reference:System.Web.Extensions.dll",
            str(source_file),
        ],
        capture_output=True,
        shell=False,
        check=False,
    )
    if build.returncode != 0:
        detail = build.stdout.decode("utf-8", errors="replace") + build.stderr.decode(
            "utf-8", errors="replace"
        )
        raise RuntimeError("探针编译失败:\n" + detail)

    # 执行刚编译出的探针（无参数）
    done = subprocess.run(
        [str(exe)],
        capture_output=True,
        shell=False,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", errors="replace"))
    for encoding in ("utf-8", "utf-16-le", "gbk"):
        try:
            return json.loads(done.stdout.decode(encoding).strip())
        except (UnicodeDecodeError, ValueError):
            continue
    raise RuntimeError("无法解析探针输出")


EXPECTED_CODE = (
    "#include <iostream>\n"
    "int main() {\n"
    "    int n, q;\n"
    "    return 0;\n"
    "}"
)


def _python_truncate(sections: list[str], limit: int) -> str:
    """Python 侧的等价截断（与 settings.overlay_context_block 同一套规则）。

    直接复用线上常量与判定条件，避免这里写字面量后与源码漂移。
    """
    from system_audio_asr.settings import _CONTEXT_TRUNCATE_MIN_ROOM

    kept: list[str] = []
    used = 0
    for section in sections:
        if not section:
            continue
        if used + len(section) > limit:
            remaining = limit - used
            if remaining >= _CONTEXT_TRUNCATE_MIN_ROOM:
                kept.append(section[:remaining].rstrip() + "…")
            break
        kept.append(section)
        used += len(section)
    if not kept:
        return ""
    return "\n\n".join(kept) + "\n\n"


def main() -> int:
    if not available():
        print("未找到 .NET Framework C# 编译器，跳过")
        return 0

    result = run()
    problems: list[str] = []

    if result.get("codeCount") != 1:
        problems.append(f"代码段数量是 {result.get('codeCount')}，应为 1")
    if result.get("codeText") != EXPECTED_CODE:
        problems.append(
            "代码段未逐字保留（#include 的 # 可能被当成标题清掉）：\n  实际="
            + repr(result.get("codeText"))
        )

    prose = result.get("proseText") or ""
    if "**" in prose:
        problems.append("正文里仍残留 ** 加粗标记：" + repr(prose[:120]))
    if "`" in prose:
        problems.append("正文里仍残留行内反引号：" + repr(prose[:120]))
    if "# 解题思路" in prose:
        problems.append("正文里仍残留 # 标题前缀：" + repr(prose[:120]))
    if "解题思路" not in prose:
        problems.append("标题文字被整体删掉（应只去掉 # 前缀）：" + repr(prose[:120]))

    # 跨端截断语义必须逐个场景一致
    cases = [
        ("truncateSingle", [("A" * 100) + "尾部"], 40),
        ("truncateSecond", ["B" * 30, "C" * 100], 50),
        ("truncateTooTight", ["D" * 45, "E" * 100], 50),
        ("truncateNoCut", ["short"], 50),
    ]
    for field, sections, limit in cases:
        csharp = result.get(field)
        python = _python_truncate(sections, limit)
        if csharp != python:
            problems.append(
                f"{field} 两端截断结果不一致：\n  C#={csharp!r}\n  Py={python!r}"
            )

    if problems:
        print("C# 渲染/截断检查未通过：")
        for item in problems:
            print("  -", item)
        return 1

    print("OK    C# 正文清理与代码段保留（SplitCodeFences）")
    print("      代码段逐字保留（含 #include/缩进）· 正文清理 ** / # / 反引号")
    print("OK    C# 上下文截断与 Python 一致（4 个场景逐字比对）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
