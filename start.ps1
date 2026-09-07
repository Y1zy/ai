param(
    [string]$Speaker = "",
    [string]$Device = "auto",
    [string]$Hub = "",
    [double]$SilenceDb = -42.0,
    [switch]$SharePhone
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "尚未安装。请先运行 .\install.ps1 -Cuda，或 CPU 版 .\install.ps1。"
}
if ($SharePhone) {
    & $Python -c "from system_audio_asr.phone_share import set_enabled; set_enabled(True)"
    Write-Host "手机投屏已启用：服务将以局域网模式启动，二维码见 http://127.0.0.1:8765/settings" -ForegroundColor Green
}
$Arguments = @("-m","system_audio_asr","--device",$Device,"--silence-db",$SilenceDb)
if ($Speaker) { $Arguments += @("--speaker",$Speaker) }
if ($Hub) { $Arguments += @("--hub",$Hub) }
& $Python @Arguments
