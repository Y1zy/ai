# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

- 新增手机投屏（扫码配对）：设置页二维码扫码后手机直连电脑（局域网），可查看并保存电脑屏幕截图、查看实时字幕与 AI 流式回答；支持手机↔电脑剪贴板双向同步；新增 `start.ps1 -SharePhone`、`--share-lan` 启动开关与 `segno`/`pillow` 依赖。
- 字幕悬浮窗默认对屏幕共享 / 录屏 / 截图工具不可见（`WDA_EXCLUDEFROMCAPTURE`），覆盖主字幕窗、设置窗与位置锁提示窗；设置页与 `start_overlay.ps1 -AllowCapture`（录制演示模式）可关闭。
- 增加不依赖 DeepSeek 的本地英文 → 中文 partial/final 实时翻译。
- 增加 Faster-Whisper 英文专用 ASR，并支持在设置页动态切换中英文识别引擎。
- 修复英文流式识别结果在单词边界处被错误拼接的问题。
- 增加字幕外框显示模式、颜色和透明度设置。
- 增加一键启动、安装自检和完整 Windows 部署文档。
- 固定兼容的 PyTorch/TorchAudio 版本，并对原生命令执行结果进行检查。
- 增加 Windows GitHub Actions、贡献指南、安全策略和 Issue 模板。

## [0.1.0] - 2026-08-28

- WASAPI Loopback 系统音频采集与 16 kHz 重采样。
- Paraformer Streaming 中文 `partial` / `final` 识别及 WebSocket 服务。
- WPF 桌面字幕、锁定/拖动/缩放、老板键与本地设置页面。
- DeepSeek SSE 流式回答、连续上下文和语音片段合并。
