"""把 C# OverlayConfig 抽出来编译成独立探针，用于真实行为验证。

为什么不直接对 OverlayApp.cs 做正则断言：配置读写的关键风险是「一个坏字段会不会
殃及其它字段」这类运行时行为，只有真正跑一遍 Load()/Save() 才能验证。

探针是纯配置类（不依赖 WPF 窗口），可以脱离整个 OverlayApp 单独编译运行。
唯一改动：把配置根目录从「系统 LocalApplicationData」换成探针环境变量
PROBE_CONFIG_ROOT，让测试写到临时目录、绝不碰用户真实配置；被测的
Load()/Save() 逻辑逐字保留。

注：这里调用的两个外部程序都是固定的系统编译器与本地探针，参数在调用点写成
字面量列表、不使用 shell，路径全部由本模块自己生成。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_OVERLAY_CS = _ROOT / "overlay_cs" / "OverlayApp.cs"

_CONFIG_PATH_HELPER = (
    "string root = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);"
)
_PROBE_PATH_HELPER = 'string root = Environment.GetEnvironmentVariable("PROBE_CONFIG_ROOT");'

_CSC_CANDIDATES = (
    Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
)

_EXE_CACHE: Path | None = None


def _csc_path() -> Path | None:
    for candidate in _CSC_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def available() -> bool:
    return _csc_path() is not None


def _overlay_config_source() -> str:
    source = _OVERLAY_CS.read_text(encoding="utf-8-sig")
    start = source.index("internal sealed class OverlayConfig")
    end = source.index("\n    }\n", start) + len("\n    }\n")
    body = source[start:end]
    if _CONFIG_PATH_HELPER not in body:
        raise AssertionError("OverlayConfig 的配置路径实现已变化，请同步更新探针")
    return body.replace(_CONFIG_PATH_HELPER, _PROBE_PATH_HELPER)


def _probe_source() -> str:
    return (
        textwrap.dedent(
            """\
            using System;
            using System.Collections.Generic;
            using System.IO;
            using System.Text;
            using System.Web.Script.Serialization;

            namespace WasapiParaformerOverlay
            {
            """
        )
        + _overlay_config_source()
        + textwrap.dedent(
            """

                internal static class Probe
                {
                    internal static void Main(string[] args)
                    {
                        if (args.Length > 0 && args[0] == "load-ok")
                        {
                            // 暴露「磁盘状态是否已知」（Load(out ok)）：调用方据此
                            // 决定能不能拿它当合并基线，读失败时必须中止本次保存。
                            bool known;
                            OverlayConfig probe = OverlayConfig.Load(out known);
                            Console.Out.Write(known ? "1" : "0");
                            return;
                        }
                        OverlayConfig cfg = OverlayConfig.Load();
                        if (args.Length > 0 && args[0] == "save")
                        {
                            cfg.Save();
                            return;
                        }
                        Dictionary<string, object> result = new Dictionary<string, object>();
                        result["resumeContext"] = cfg.ResumeContext;
                        result["jdContext"] = cfg.JdContext;
                        result["targetCompany"] = cfg.TargetCompany;
                        result["extraContext"] = cfg.ExtraContext;
                        result["aiSystemPrompt"] = cfg.AiSystemPrompt;
                        result["aiOverridePrompt"] = cfg.AiOverridePrompt;
                        result["hotwordExtra"] = cfg.HotwordExtra;
                        result["visionAnswerMode"] = cfg.VisionAnswerMode;
                        result["visionThinkingMode"] = cfg.VisionThinkingMode;
                        result["visionMaxTokens"] = cfg.VisionMaxTokens;
                        result["aiMaxTokens"] = cfg.AiMaxTokens;
                        result["width"] = cfg.Width;
                        // NaN 场景要能观察到：用字符串输出而不是数值，
                        // 否则 JavaScriptSerializer 会写成非法 JSON `NaN`，再也解析不回来。
                        result["opacityRaw"] = cfg.Opacity.ToString("R", System.Globalization.CultureInfo.InvariantCulture);
                        result["opacityIsNaN"] = double.IsNaN(cfg.Opacity);
                        result["silenceRaw"] = cfg.AiSilenceSeconds.ToString("R", System.Globalization.CultureInfo.InvariantCulture);
                        Console.Out.Write(new JavaScriptSerializer().Serialize(result));
                    }
                }
            }
            """
        )
    )


def _build() -> Path:
    """用系统 .NET 编译器把探针源编译成 exe（参数为固定字面量，不经 shell）。"""
    global _EXE_CACHE
    if _EXE_CACHE is not None and _EXE_CACHE.exists():
        return _EXE_CACHE
    compiler = _csc_path()
    if compiler is None:
        raise RuntimeError(".NET Framework C# 编译器不存在")

    work = Path(tempfile.mkdtemp(prefix="configprobe-"))
    source_file = work / "Probe.cs"
    source_file.write_text(_probe_source(), encoding="utf-8")
    exe = work / "Probe.exe"

    build = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            "/out:" + str(exe),
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
    _EXE_CACHE = exe
    return exe


def config_file(config_root: Path) -> Path:
    target = config_root / "WasapiParaformerOverlay" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def write_config(config_root: Path, payload: dict) -> Path:
    target = config_file(config_root)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def _run(config_root: Path, mode: str) -> str:
    exe = _build()
    env = dict(os.environ)
    env["PROBE_CONFIG_ROOT"] = str(config_root)
    if mode == "load":
        argv = [str(exe)]
    elif mode == "load-ok":
        argv = [str(exe), "load-ok"]
    else:
        argv = [str(exe), "save"]
    # 探针用 Console.Out.Write 输出 JSON（含中文），Windows 下 .NET 控制台默认
    # 代码页不是 UTF-8，因此按字节读回再显式解码，避免 UnicodeDecodeError。
    done = subprocess.run(
        argv,
        capture_output=True,
        env=env,
        shell=False,
        check=False,
    )
    if done.returncode != 0:
        stderr = done.stderr.decode("utf-8", errors="replace")
        raise RuntimeError("探针运行失败:\n" + stderr)
    for encoding in ("utf-8", "gbk", "utf-16-le"):
        try:
            return done.stdout.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return done.stdout.decode("utf-8", errors="replace").strip()


def load(config_root: Path) -> dict:
    return json.loads(_run(config_root, "load"))


def load_disk_known(config_root: Path) -> bool:
    """磁盘状态是否已知：文件不存在或读成功都算已知，读失败为 False。

    调用方（C# 的三方合并 / 配置热重载）据此决定能否拿 Load 的结果当基线。
    """
    return _run(config_root, "load-ok") == "1"


def save(config_root: Path) -> None:
    _run(config_root, "save")


def real_config_path() -> pathlib.Path:
    """用户真实配置路径（仅用于自检「测试没碰真实配置」）。"""
    root = os.environ.get("LOCALAPPDATA", "")
    return Path(root) / "WasapiParaformerOverlay" / "config.json"


if __name__ == "__main__":
    print("csc:", _csc_path())
    print("available:", available())
    sys.exit(0 if available() else 1)
