# Send a real Ctrl+Alt+L chord and report whether the toast fired.
# Verifies both the hotkey path and the on-screen feedback (log line "toast=").
# ASCII-only: Windows PowerShell 5.1 reads .ps1 as ANSI.
$ErrorActionPreference = "Stop"

$sig = @'
using System;
using System.Runtime.InteropServices;
public class Chord {
    [DllImport("user32.dll", SetLastError=true)]
    static extern uint SendInput(uint n, INPUT[] p, int cb);
    [StructLayout(LayoutKind.Sequential)]
    public struct KI { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr extra; }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public KI ki; public long pad; }
    static void Key(ushort vk, bool up) {
        INPUT[] i = new INPUT[1];
        i[0].type = 1; i[0].ki.wVk = vk; i[0].ki.dwFlags = up ? 2u : 0u;
        SendInput(1, i, Marshal.SizeOf(typeof(INPUT)));
    }
    public static void Send(ushort m1, ushort m2, ushort k) {
        Key(m1,false); Key(m2,false); Key(k,false);
        System.Threading.Thread.Sleep(60);
        Key(k,true); Key(m2,true); Key(m1,true);
        System.Threading.Thread.Sleep(120);
    }
}
'@
Add-Type -TypeDefinition $sig -Language CSharp | Out-Null

$log = Join-Path $PSScriptRoot "..\overlay_cs\bin\overlay.runtime.log"
$before = (Get-Content $log -ErrorAction SilentlyContinue | Measure-Object -Line).Lines

$VK_CONTROL=0x11; $VK_MENU=0x12; $VK_L=0x4C
Write-Host "sending real Ctrl+Alt+L ..."
[Chord]::Send($VK_CONTROL, $VK_MENU, $VK_L)
Start-Sleep -Milliseconds 900

$new = Get-Content $log | Select-Object -Skip $before
$lockLine = $new | Select-String "position_locked" | Select-Object -First 1
$toastLine = $new | Select-String "toast=" | Select-Object -First 1

Write-Host ""
if ($lockLine) { Write-Host "lock event : $($lockLine.Line.Trim())" } else { Write-Host "lock event : (none)" -ForegroundColor Red }
if ($toastLine) { Write-Host "toast      : $($toastLine.Line.Trim())" } else { Write-Host "toast      : (none)" -ForegroundColor Red }

# restore state
[Chord]::Send($VK_CONTROL, $VK_MENU, $VK_L)
Start-Sleep -Milliseconds 700
Write-Host ""
if ($lockLine -and $toastLine) {
    Write-Host "PASS: hotkey fired and toast shown" -ForegroundColor Green
} else {
    Write-Host "FAIL: missing lock event or toast" -ForegroundColor Red
    exit 2
}
