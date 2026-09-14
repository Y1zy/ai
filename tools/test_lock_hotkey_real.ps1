# Verify the lock hotkey with real input, checking SendInput's return value.
# Previous attempt used a possibly mis-sized INPUT struct and never checked the
# return value, so its failures were inconclusive.
# ASCII-only: Windows PowerShell 5.1 reads .ps1 as ANSI.
$ErrorActionPreference = "Stop"

$sig = @'
using System;
using System.Runtime.InteropServices;
public class RealKey {
    [DllImport("user32.dll", SetLastError=true)]
    static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
    [DllImport("user32.dll")]
    static extern IntPtr GetWindowLongPtr(IntPtr h, int index);
    [DllImport("user32.dll")]
    static extern bool EnumWindows(EnumProc cb, IntPtr lp);
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr h);
    public delegate bool EnumProc(IntPtr h, IntPtr lp);

    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT {
        public ushort wVk; public ushort wScan; public uint dwFlags;
        public uint time; public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT {
        public uint type; public KEYBDINPUT ki;
        public long pad;   // make struct 40 bytes on x64, matching native INPUT
    }

    public static bool Key(ushort vk, bool up) {
        INPUT[] inp = new INPUT[1];
        inp[0].type = 1;
        inp[0].ki.wVk = vk;
        inp[0].ki.dwFlags = up ? 2u : 0u;
        uint sent = SendInput(1, inp, Marshal.SizeOf(typeof(INPUT)));
        return sent == 1;
    }

    public static bool Chord(ushort m1, ushort m2, ushort key) {
        bool ok = Key(m1,false) && Key(m2,false) && Key(key,false);
        System.Threading.Thread.Sleep(60);
        Key(key,true); Key(m2,true); Key(m1,true);
        System.Threading.Thread.Sleep(60);
        return ok;
    }

    public static long GetExStyle(IntPtr h) {
        return IntPtr.Size == 8
            ? GetWindowLongPtr(h, -20).ToInt64()
            : GetWindowLongPtr(h, -20).ToInt32();
    }

    public static IntPtr FindTopWindow(uint targetPid) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr h, IntPtr lp) {
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (pid == targetPid && IsWindowVisible(h)) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@
Add-Type -TypeDefinition $sig -Language CSharp | Out-Null

$proc = Get-Process -Name SystemAudioOverlay -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { Write-Host "Overlay not running"; exit 1 }
$hwnd = [RealKey]::FindTopWindow([uint32]$proc.Id)
if ($hwnd -eq [IntPtr]::Zero) { Write-Host "overlay window not found"; exit 1 }

$VK_CONTROL=0x11; $VK_MENU=0x12; $VK_SHIFT=0x10
$VK_L=0x4C; $VK_H=0x48
$WS_EX_TRANSPARENT = 0x20
function IsCT { param([IntPtr]$h) return (([RealKey]::GetExStyle($h)) -band $WS_EX_TRANSPARENT) -ne 0 }

Write-Host ("baseline: click_through={0}" -f (IsCT $hwnd))

# Sanity check the harness itself against a hotkey known to be registered:
# Ctrl+Alt+H (boss key) toggles window opacity, not exstyle, so we watch the log instead.
# Here we only need SendInput to succeed.
$sent = [RealKey]::Chord($VK_CONTROL, $VK_MENU, $VK_H)
Write-Host ("SendInput(Ctrl+Alt+H) delivered = {0}" -f $sent)
Start-Sleep -Milliseconds 500

$before = IsCT $hwnd
$sentL = [RealKey]::Chord($VK_CONTROL, $VK_SHIFT, $VK_L)
Write-Host ("SendInput(Ctrl+Shift+L) delivered = {0}" -f $sentL)
Start-Sleep -Milliseconds 700
$after = IsCT $hwnd
Write-Host ("click_through: {0} -> {1}" -f $before, $after)

if ($after -ne $before) {
    # restore
    [RealKey]::Chord($VK_CONTROL, $VK_SHIFT, $VK_L) | Out-Null
    Start-Sleep -Milliseconds 700
    Write-Host "RESULT: Ctrl+Shift+L works (state restored)" -ForegroundColor Green
} else {
    Write-Host "RESULT: Ctrl+Shift+L did not change state" -ForegroundColor Yellow
}
