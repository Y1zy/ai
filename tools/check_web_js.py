"""检查网页内联脚本的 JS 语法。

内联脚本一旦有语法错误，整段脚本都不会执行——页面所有输入框保持空白、
按钮全部失效，而且没有任何报错提示（这类故障排查起来很费时）。
这里把 <script> 内容抽出来交给 node --check 做纯语法校验。

用法：python tools/check_web_js.py
退出码 0 表示全部通过。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "system_audio_asr" / "web" / "settings.html",
    ROOT / "system_audio_asr" / "web" / "phone.html",
)


def extract_script(path: Path) -> str:
    html = path.read_text(encoding="utf-8")
    match = re.search(r"<script[^>]*>(.*?)</script>", html, re.S)
    if not match:
        raise RuntimeError(f"{path.name} 里没有内联脚本")
    return match.group(1)


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("未安装 node，跳过 JS 语法检查")
        return 0

    failures = 0
    with tempfile.TemporaryDirectory() as work:
        for target in TARGETS:
            script = extract_script(target)
            probe = Path(work) / (target.stem + ".js")
            probe.write_text(script, encoding="utf-8")
            done = subprocess.run(
                [node, "--check", str(probe)],
                capture_output=True,
                shell=False,
                check=False,
            )
            if done.returncode == 0:
                print(f"OK    {target.name}  ({len(script)} chars)")
            else:
                failures += 1
                detail = done.stderr.decode("utf-8", errors="replace")
                print(f"FAIL  {target.name}")
                print(detail)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
