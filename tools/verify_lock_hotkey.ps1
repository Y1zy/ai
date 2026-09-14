# Verify Ctrl+Alt+L lock hotkey: send WM_HOTKEY(0xA58) to the Overlay window,
# then read GWL_EXSTYLE and check the WS_EX_TRANSPARENT(0x20) bit toggles.
# That is hard evidence that click-through actually takes effect.
# NOTE: keep this file ASCII-only -- Windows PowerShell 5.1 reads .ps1 as ANSI,
# so non-ASCII comments get mangled and break parsing.
$ErrorActionPreference = "Stop"

$sig = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinCheck {
    [DllImport("user32.dll", SetLastError=true)]
    public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
    [DllImport("user32.dll")]
    public static extern IntPtr GetWindowLongPtr(IntPtr h, int index);
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumProc cb, IntPtr lp);
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr h);
    public delegate bool EnumProc(IntPtr h, IntPtr lp);
    public static long GetExStyle(IntPtr h) {
        return IntPtr.Size == 8
            ? GetWindowLongPtr(h, -20).ToInt64()
            : GetWindowLongPtr(h, -20).ToInt32();
    }
    // Overlay is a borderless WS_EX_TOOLWINDOW, so Process.MainWindowHandle is 0.
    // Enumerate top-level windows and pick the first visible one owned by the pid.
    public static IntPtr FindTopWindow(uint targetPid) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr h, IntPtr lp) {
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            if (pid == targetPid && IsWindowVisible(h)) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@
Add-Type -TypeDefinition $sig -Language CSharp | Out-Null

$proc = Get-Process -Name SystemAudioOverlay -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { Write-Host "Overlay is not running" -ForegroundColor Red; exit 1 }
$hwnd = [WinCheck]::FindTopWindow([uint32]$proc.Id)
if ($hwnd -eq [IntPtr]::Zero) { Write-Host "No visible top-level window for pid $($proc.Id)" -ForegroundColor Red; exit 1 }
Write-Host ("target window handle: 0x{0:X}" -f $hwnd.ToInt64())

$WS_EX_TRANSPARENT = 0x20
function IsClickThrough { param([IntPtr]$h)
    return (([WinCheck]::GetExStyle($h)) -band $WS_EX_TRANSPARENT) -ne 0
}

$before = IsClickThrough $hwnd
Write-Host ("before     : click_through={0}  (exstyle=0x{1:X})" -f $before, [WinCheck]::GetExStyle($hwnd))

[WinCheck]::SendMessage($hwnd, 0x0312, [IntPtr]0xA58, [IntPtr]::Zero) | Out-Null
Start-Sleep -Milliseconds 600
$afterFirst = IsClickThrough $hwnd
Write-Host ("after 1st  : click_through={0}  (exstyle=0x{1:X})" -f $afterFirst, [WinCheck]::GetExStyle($hwnd))

[WinCheck]::SendMessage($hwnd, 0x0312, [IntPtr]0xA58, [IntPtr]::Zero) | Out-Null
Start-Sleep -Milliseconds 600
$afterSecond = IsClickThrough $hwnd
Write-Host ("after 2nd  : click_through={0}  (exstyle=0x{1:X})" -f $afterSecond, [WinCheck]::GetExStyle($hwnd))

Write-Host ""
if ((-not $before) -and $afterFirst -and (-not $afterSecond)) {
    Write-Host "PASS: hotkey toggles click-through (off -> on -> off)" -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL: click-through state did not toggle as expected" -ForegroundColor Red
    exit 2
}
