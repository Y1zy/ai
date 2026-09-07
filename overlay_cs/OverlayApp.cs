using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using Forms = System.Windows.Forms;
using Drawing = System.Drawing;

namespace WasapiParaformerOverlay
{
    internal static class AppLog
    {
        private static readonly object Sync = new object();
        internal static readonly string Path = System.IO.Path.Combine(
            AppDomain.CurrentDomain.BaseDirectory, "overlay.runtime.log");

        internal static void Write(string message)
        {
            lock (Sync)
            {
                File.AppendAllText(
                    Path,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " + message + Environment.NewLine,
                    Encoding.UTF8);
            }
        }
    }

    internal static class NativeMethods
    {
        internal const int GWL_EXSTYLE = -20;
        internal const long WS_EX_TRANSPARENT = 0x00000020L;
        internal const long WS_EX_TOOLWINDOW = 0x00000080L;
        internal const long WS_EX_LAYERED = 0x00080000L;
        internal const long WS_EX_NOACTIVATE = 0x08000000L;
        internal const uint WDA_NONE = 0x00000000u;
        internal const uint WDA_EXCLUDEFROMCAPTURE = 0x00000011u;
        internal const int WM_HOTKEY = 0x0312;
        internal const int WM_NCHITTEST = 0x0084;
        internal const int WM_NCLBUTTONDOWN = 0x00A1;
        internal const int HTLEFT = 10;
        internal const int HTRIGHT = 11;
        internal const int HTTOP = 12;
        internal const int HTTOPLEFT = 13;
        internal const int HTTOPRIGHT = 14;
        internal const int HTBOTTOM = 15;
        internal const int HTBOTTOMLEFT = 16;
        internal const int HTBOTTOMRIGHT = 17;
        internal const uint MOD_ALT = 0x0001;
        internal const uint MOD_CONTROL = 0x0002;
        internal const uint MOD_SHIFT = 0x0004;
        internal const uint MOD_NOREPEAT = 0x4000;
        internal const int HOTKEY_ID = 0xA51;
        internal const int BOSS_HOTKEY_ID = 0xA52;
        internal const int MOVE_UP_HOTKEY_ID = 0xA53;
        internal const int MOVE_DOWN_HOTKEY_ID = 0xA54;
        internal const int MOVE_LEFT_HOTKEY_ID = 0xA55;
        internal const int MOVE_RIGHT_HOTKEY_ID = 0xA56;
        internal const int SOLVE_HOTKEY_ID = 0xA57;

        [StructLayout(LayoutKind.Sequential)]
        internal struct AccentPolicy
        {
            internal int AccentState;
            internal int AccentFlags;
            internal uint GradientColor;
            internal int AnimationId;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct WindowCompositionAttributeData
        {
            internal int Attribute;
            internal IntPtr Data;
            internal int SizeOfData;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct NativePoint
        {
            internal int X;
            internal int Y;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct NativeRect
        {
            internal int Left;
            internal int Top;
            internal int Right;
            internal int Bottom;
        }

        [DllImport("user32.dll")]
        internal static extern bool SetProcessDpiAwarenessContext(IntPtr dpiContext);

        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
        internal static extern IntPtr GetWindowLongPtr64(IntPtr hwnd, int index);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")]
        internal static extern IntPtr SetWindowLongPtr64(IntPtr hwnd, int index, IntPtr value);

        [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
        internal static extern int GetWindowLong32(IntPtr hwnd, int index);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongW")]
        internal static extern int SetWindowLong32(IntPtr hwnd, int index, int value);

        [DllImport("user32.dll")]
        internal static extern bool SetWindowCompositionAttribute(
            IntPtr hwnd, ref WindowCompositionAttributeData data);

        [DllImport("dwmapi.dll")]
        internal static extern int DwmSetWindowAttribute(
            IntPtr hwnd, int attribute, ref int value, int valueSize);

        [DllImport("user32.dll")]
        internal static extern bool RegisterHotKey(IntPtr hwnd, int id, uint modifiers, uint key);

        [DllImport("user32.dll")]
        internal static extern bool UnregisterHotKey(IntPtr hwnd, int id);

        [DllImport("user32.dll")]
        internal static extern bool GetCursorPos(out NativePoint point);

        [DllImport("user32.dll")]
        internal static extern bool GetWindowRect(IntPtr hwnd, out NativeRect rect);

        [DllImport("user32.dll")]
        internal static extern bool ReleaseCapture();

        [DllImport("user32.dll")]
        internal static extern IntPtr SendMessage(
            IntPtr hwnd, int message, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll")]
        internal static extern short GetAsyncKeyState(int virtualKey);

        internal static long GetExtendedStyle(IntPtr hwnd)
        {
            return IntPtr.Size == 8
                ? GetWindowLongPtr64(hwnd, GWL_EXSTYLE).ToInt64()
                : GetWindowLong32(hwnd, GWL_EXSTYLE);
        }

        internal static void SetExtendedStyle(IntPtr hwnd, long style)
        {
            if (IntPtr.Size == 8)
                SetWindowLongPtr64(hwnd, GWL_EXSTYLE, new IntPtr(style));
            else
                SetWindowLong32(hwnd, GWL_EXSTYLE, unchecked((int)style));
        }

        internal static void SetInteractive(IntPtr hwnd, bool editable)
        {
            long style = GetExtendedStyle(hwnd) | WS_EX_TOOLWINDOW | WS_EX_LAYERED;
            if (editable)
                style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE);
            else
                style |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE;
            SetExtendedStyle(hwnd, style);
        }

        internal static void SetNormalInteraction(IntPtr hwnd, bool locked)
        {
            long style = GetExtendedStyle(hwnd) | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE;
            if (locked) style |= WS_EX_TRANSPARENT;
            else style &= ~WS_EX_TRANSPARENT;
            SetExtendedStyle(hwnd, style);
        }

        internal static bool CursorInside(IntPtr hwnd, int margin)
        {
            NativePoint point;
            NativeRect rect;
            if (hwnd == IntPtr.Zero || !GetCursorPos(out point) || !GetWindowRect(hwnd, out rect))
                return false;
            return point.X >= rect.Left - margin && point.X <= rect.Right + margin
                && point.Y >= rect.Top - margin && point.Y <= rect.Bottom + margin;
        }

        internal static int ResizeHitAtCursor(IntPtr hwnd, int grip)
        {
            NativePoint point;
            NativeRect rect;
            if (!GetCursorPos(out point) || !GetWindowRect(hwnd, out rect)) return 0;
            bool left = point.X >= rect.Left && point.X < rect.Left + grip;
            bool right = point.X <= rect.Right && point.X > rect.Right - grip;
            bool top = point.Y >= rect.Top && point.Y < rect.Top + grip;
            bool bottom = point.Y <= rect.Bottom && point.Y > rect.Bottom - grip;
            if (left && top) return HTTOPLEFT;
            if (right && top) return HTTOPRIGHT;
            if (left && bottom) return HTBOTTOMLEFT;
            if (right && bottom) return HTBOTTOMRIGHT;
            if (left) return HTLEFT;
            if (right) return HTRIGHT;
            if (top) return HTTOP;
            if (bottom) return HTBOTTOM;
            return 0;
        }

        internal static void BeginNativeResize(IntPtr hwnd, int hit)
        {
            ReleaseCapture();
            SendMessage(hwnd, WM_NCLBUTTONDOWN, new IntPtr(hit), IntPtr.Zero);
        }

        internal static bool EnableAcrylic(IntPtr hwnd)
        {
            AccentPolicy policy = new AccentPolicy();
            policy.AccentState = 4;
            policy.AccentFlags = 2;
            policy.GradientColor = 0xBE16120D;
            int size = Marshal.SizeOf(typeof(AccentPolicy));
            IntPtr pointer = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.StructureToPtr(policy, pointer, false);
                WindowCompositionAttributeData data = new WindowCompositionAttributeData();
                data.Attribute = 19;
                data.Data = pointer;
                data.SizeOfData = size;
                bool result = SetWindowCompositionAttribute(hwnd, ref data);
                int corner = 2;
                DwmSetWindowAttribute(hwnd, 33, ref corner, sizeof(int));
                return result;
            }
            finally
            {
                Marshal.FreeHGlobal(pointer);
            }
        }

        [DllImport("user32.dll")]
        internal static extern bool SetWindowDisplayAffinity(IntPtr hwnd, uint affinity);

        [DllImport("user32.dll")]
        internal static extern bool GetWindowDisplayAffinity(IntPtr hwnd, out uint affinity);

        // WDA_EXCLUDEFROMCAPTURE 需要 Windows 10 2004 (build 19041) 及以上：
        // 窗口会从所有屏幕采集路径（会议共享、录屏、截图工具）中消失，本机屏幕仍正常显示。
        internal static bool ApplyExcludeFromCapture(IntPtr hwnd, bool enabled)
        {
            if (hwnd == IntPtr.Zero) return false;
            try
            {
                uint target = enabled ? WDA_EXCLUDEFROMCAPTURE : WDA_NONE;
                if (!SetWindowDisplayAffinity(hwnd, target)) return false;
                uint actual;
                if (!GetWindowDisplayAffinity(hwnd, out actual)) return false;
                return actual == target;
            }
            catch { return false; }
        }
    }

    internal sealed class OverlayConfig
    {
        internal double Left = double.NaN;
        internal double Top = double.NaN;
        internal double Width = 980;
        internal double Height = 150;
        internal double FontSize = 36;
        internal int MaxLines = 3;
        internal double Opacity = 0.88;
        internal int FadeDelayMs = 1800;
        internal string FontFamilyName = "Microsoft YaHei UI";
        internal string TextColor = "#FFFFFF";
        internal string FrameMode = "hover";
        internal string FrameColor = "#7DBEFF";
        internal double FrameOpacity = 0.69;
        internal bool Locked = false;
        internal bool CaptureInvisible = true;
        internal string ScreenName = "";
        internal string WebSocketUrl = "ws://127.0.0.1:8765/ws";
        internal string AsrLanguage = "zh";
        internal bool LiveTranslateEnabled = false;
        internal bool AiEnabled = false;
        internal string AiModel = "deepseek-v4-flash";
        internal string AiMode = "auto";
        internal double AiSilenceSeconds = 0.6;
        internal string AiSystemPrompt = "";
        internal string AiBaseUrl = "https://api.deepseek.com";
        internal string AiOverridePrompt = "";
        // 自定义内置模板：非空时替换 PromptForMode 的硬编码模板（上下文与附加要求仍自动附加）。
        internal string AiBuiltInPrompt = "";
        // 简历上下文：面试回答时 AI 会参考这些真实经历来组织答案。
        internal string ResumeContext = "";
        internal string JdContext = "";
        internal string TargetCompany = "";
        internal string ExtraContext = "";
        // 截图解题：OpenAI 兼容视觉模型（与 DeepSeek 文字助手相互独立）。
        internal bool VisionEnabled = false;
        internal string VisionBaseUrl = "";
        internal string VisionModel = "";
        internal string SolvePrompt = "";

        internal static string ConfigPath
        {
            get
            {
                string root = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                return Path.Combine(root, "WasapiParaformerOverlay", "config.json");
            }
        }

        internal void Normalize()
        {
            Width = Math.Max(280, Math.Min(2200, Width));
            Height = Math.Max(72, Math.Min(800, Height));
            FontSize = Math.Max(12, Math.Min(96, FontSize));
            MaxLines = Math.Max(1, Math.Min(10, MaxLines));
            Opacity = Math.Max(0.45, Math.Min(0.98, Opacity));
            FrameOpacity = Math.Max(0, Math.Min(1, FrameOpacity));
            FadeDelayMs = Math.Max(1000, Math.Min(5000, FadeDelayMs));
            AiSilenceSeconds = Math.Max(0.5, Math.Min(8.0, AiSilenceSeconds));
            if (string.IsNullOrWhiteSpace(FontFamilyName)) FontFamilyName = "Microsoft YaHei UI";
            if (string.IsNullOrWhiteSpace(TextColor)) TextColor = "#FFFFFF";
            if (FrameMode != "always") FrameMode = "hover";
            if (string.IsNullOrWhiteSpace(FrameColor)) FrameColor = "#7DBEFF";
            if (string.IsNullOrWhiteSpace(AiModel)) AiModel = "deepseek-v4-flash";
            if (string.IsNullOrWhiteSpace(AiMode)) AiMode = "auto";
            if (string.IsNullOrWhiteSpace(AiBaseUrl)) AiBaseUrl = "https://api.deepseek.com";
            if (AsrLanguage != "en") AsrLanguage = "zh";
        }

        internal static OverlayConfig Load()
        {
            OverlayConfig result = new OverlayConfig();
            try
            {
                if (!File.Exists(ConfigPath)) return result;
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                Dictionary<string, object> data = serializer.Deserialize<Dictionary<string, object>>(
                    File.ReadAllText(ConfigPath, Encoding.UTF8));
                if (data.ContainsKey("left")) result.Left = Convert.ToDouble(data["left"]);
                if (data.ContainsKey("top")) result.Top = Convert.ToDouble(data["top"]);
                if (data.ContainsKey("width")) result.Width = Convert.ToDouble(data["width"]);
                if (data.ContainsKey("height")) result.Height = Convert.ToDouble(data["height"]);
                if (data.ContainsKey("fontSize")) result.FontSize = Convert.ToDouble(data["fontSize"]);
                if (data.ContainsKey("maxLines")) result.MaxLines = Convert.ToInt32(data["maxLines"]);
                if (data.ContainsKey("opacity")) result.Opacity = Convert.ToDouble(data["opacity"]);
                if (data.ContainsKey("fadeDelayMs")) result.FadeDelayMs = Convert.ToInt32(data["fadeDelayMs"]);
                if (data.ContainsKey("fontFamily")) result.FontFamilyName = Convert.ToString(data["fontFamily"]);
                if (data.ContainsKey("textColor")) result.TextColor = Convert.ToString(data["textColor"]);
                if (data.ContainsKey("frameMode")) result.FrameMode = Convert.ToString(data["frameMode"]);
                if (data.ContainsKey("frameColor")) result.FrameColor = Convert.ToString(data["frameColor"]);
                if (data.ContainsKey("frameOpacity")) result.FrameOpacity = Convert.ToDouble(data["frameOpacity"]);
                if (data.ContainsKey("locked")) result.Locked = Convert.ToBoolean(data["locked"]);
                if (data.ContainsKey("captureInvisible")) result.CaptureInvisible = Convert.ToBoolean(data["captureInvisible"]);
                if (data.ContainsKey("screenName")) result.ScreenName = Convert.ToString(data["screenName"]);
                if (data.ContainsKey("webSocketUrl")) result.WebSocketUrl = Convert.ToString(data["webSocketUrl"]);
                if (data.ContainsKey("asrLanguage")) result.AsrLanguage = Convert.ToString(data["asrLanguage"]);
                if (data.ContainsKey("liveTranslateEnabled")) result.LiveTranslateEnabled = Convert.ToBoolean(data["liveTranslateEnabled"]);
                if (data.ContainsKey("aiEnabled")) result.AiEnabled = Convert.ToBoolean(data["aiEnabled"]);
                if (data.ContainsKey("aiModel")) result.AiModel = Convert.ToString(data["aiModel"]);
                if (data.ContainsKey("aiMode")) result.AiMode = Convert.ToString(data["aiMode"]);
                if (data.ContainsKey("aiSilenceSeconds")) result.AiSilenceSeconds = Convert.ToDouble(data["aiSilenceSeconds"]);
                if (data.ContainsKey("aiSystemPrompt")) result.AiSystemPrompt = Convert.ToString(data["aiSystemPrompt"]);
                if (data.ContainsKey("aiBaseUrl")) result.AiBaseUrl = Convert.ToString(data["aiBaseUrl"]);
                if (data.ContainsKey("aiOverridePrompt")) result.AiOverridePrompt = Convert.ToString(data["aiOverridePrompt"]);
                if (data.ContainsKey("aiBuiltInPrompt")) result.AiBuiltInPrompt = Convert.ToString(data["aiBuiltInPrompt"]);
                if (data.ContainsKey("resumeContext")) result.ResumeContext = Convert.ToString(data["resumeContext"]);
                if (data.ContainsKey("jdContext")) result.JdContext = Convert.ToString(data["jdContext"]);
                if (data.ContainsKey("targetCompany")) result.TargetCompany = Convert.ToString(data["targetCompany"]);
                if (data.ContainsKey("extraContext")) result.ExtraContext = Convert.ToString(data["extraContext"]);
                if (data.ContainsKey("visionEnabled")) result.VisionEnabled = Convert.ToBoolean(data["visionEnabled"]);
                if (data.ContainsKey("visionBaseUrl")) result.VisionBaseUrl = Convert.ToString(data["visionBaseUrl"]);
                if (data.ContainsKey("visionModel")) result.VisionModel = Convert.ToString(data["visionModel"]);
                if (data.ContainsKey("solvePrompt")) result.SolvePrompt = Convert.ToString(data["solvePrompt"]);
            }
            catch { }
            result.Normalize();
            return result;
        }

        internal void Save()
        {
            Normalize();
            Dictionary<string, object> data = new Dictionary<string, object>();
            data["left"] = Left;
            data["top"] = Top;
            data["width"] = Width;
            data["height"] = Height;
            data["fontSize"] = FontSize;
            data["maxLines"] = MaxLines;
            data["opacity"] = Opacity;
            data["fadeDelayMs"] = FadeDelayMs;
            data["fontFamily"] = FontFamilyName;
            data["textColor"] = TextColor;
            data["frameMode"] = FrameMode;
            data["frameColor"] = FrameColor;
            data["frameOpacity"] = FrameOpacity;
            data["locked"] = Locked;
            data["captureInvisible"] = CaptureInvisible;
            data["screenName"] = ScreenName;
            data["webSocketUrl"] = WebSocketUrl;
            data["asrLanguage"] = AsrLanguage;
            data["liveTranslateEnabled"] = LiveTranslateEnabled;
            data["aiEnabled"] = AiEnabled;
            data["aiModel"] = AiModel;
            data["aiMode"] = AiMode;
            data["aiSilenceSeconds"] = AiSilenceSeconds;
            data["aiSystemPrompt"] = AiSystemPrompt;
            data["aiBaseUrl"] = AiBaseUrl;
            data["aiOverridePrompt"] = AiOverridePrompt;
            data["aiBuiltInPrompt"] = AiBuiltInPrompt;
            data["resumeContext"] = ResumeContext;
            data["jdContext"] = JdContext;
            data["targetCompany"] = TargetCompany;
            data["extraContext"] = ExtraContext;
            data["visionEnabled"] = VisionEnabled;
            data["visionBaseUrl"] = VisionBaseUrl;
            data["visionModel"] = VisionModel;
            data["solvePrompt"] = SolvePrompt;
            string directory = Path.GetDirectoryName(ConfigPath);
            Directory.CreateDirectory(directory);
            string temporary = ConfigPath + ".tmp";
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            File.WriteAllText(temporary, serializer.Serialize(data), Encoding.UTF8);
            if (File.Exists(ConfigPath)) File.Replace(temporary, ConfigPath, null);
            else File.Move(temporary, ConfigPath);
        }

        internal void ApplyFrom(OverlayConfig other)
        {
            Left = other.Left;
            Top = other.Top;
            Width = other.Width;
            Height = other.Height;
            FontSize = other.FontSize;
            MaxLines = other.MaxLines;
            Opacity = other.Opacity;
            FadeDelayMs = other.FadeDelayMs;
            FontFamilyName = other.FontFamilyName;
            TextColor = other.TextColor;
            FrameMode = other.FrameMode;
            FrameColor = other.FrameColor;
            FrameOpacity = other.FrameOpacity;
            Locked = other.Locked;
            CaptureInvisible = other.CaptureInvisible;
            ScreenName = other.ScreenName;
            WebSocketUrl = other.WebSocketUrl;
            AsrLanguage = other.AsrLanguage;
            LiveTranslateEnabled = other.LiveTranslateEnabled;
            AiEnabled = other.AiEnabled;
            AiModel = other.AiModel;
            AiMode = other.AiMode;
            AiSilenceSeconds = other.AiSilenceSeconds;
            AiSystemPrompt = other.AiSystemPrompt;
            AiBaseUrl = other.AiBaseUrl;
            AiOverridePrompt = other.AiOverridePrompt;
            AiBuiltInPrompt = other.AiBuiltInPrompt;
            ResumeContext = other.ResumeContext;
            JdContext = other.JdContext;
            TargetCompany = other.TargetCompany;
            ExtraContext = other.ExtraContext;
            VisionEnabled = other.VisionEnabled;
            VisionBaseUrl = other.VisionBaseUrl;
            VisionModel = other.VisionModel;
            SolvePrompt = other.SolvePrompt;
            Normalize();
        }
    }

    internal static class SecretStore
    {
        private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("WasapiParaformerOverlay.DeepSeek.v1");
        internal static string KeyPath
        {
            get
            {
                string directory = Path.GetDirectoryName(OverlayConfig.ConfigPath);
                return Path.Combine(directory, "deepseek.key");
            }
        }

        internal static void SaveApiKey(string apiKey)
        {
            string directory = Path.GetDirectoryName(KeyPath);
            Directory.CreateDirectory(directory);
            if (string.IsNullOrWhiteSpace(apiKey))
            {
                if (File.Exists(KeyPath)) File.Delete(KeyPath);
                return;
            }
            byte[] clear = Encoding.UTF8.GetBytes(apiKey.Trim());
            byte[] encrypted = ProtectedData.Protect(clear, Entropy, DataProtectionScope.CurrentUser);
            File.WriteAllBytes(KeyPath, encrypted);
            Array.Clear(clear, 0, clear.Length);
        }

        internal static string LoadApiKey()
        {
            try
            {
                if (!File.Exists(KeyPath)) return "";
                byte[] encrypted = File.ReadAllBytes(KeyPath);
                byte[] clear = ProtectedData.Unprotect(encrypted, Entropy, DataProtectionScope.CurrentUser);
                string value = Encoding.UTF8.GetString(clear);
                Array.Clear(clear, 0, clear.Length);
                return value;
            }
            catch { return ""; }
        }

        internal static bool HasApiKey { get { return LoadApiKey().Length > 0; } }
    }

    internal static class VisionSecretStore
    {
        private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("WasapiParaformerOverlay.Vision.v1");
        internal static string KeyPath
        {
            get
            {
                string directory = Path.GetDirectoryName(OverlayConfig.ConfigPath);
                return Path.Combine(directory, "vision.key");
            }
        }

        internal static void SaveApiKey(string apiKey)
        {
            string directory = Path.GetDirectoryName(KeyPath);
            Directory.CreateDirectory(directory);
            if (string.IsNullOrWhiteSpace(apiKey))
            {
                if (File.Exists(KeyPath)) File.Delete(KeyPath);
                return;
            }
            byte[] clear = Encoding.UTF8.GetBytes(apiKey.Trim());
            byte[] encrypted = ProtectedData.Protect(clear, Entropy, DataProtectionScope.CurrentUser);
            File.WriteAllBytes(KeyPath, encrypted);
            Array.Clear(clear, 0, clear.Length);
        }

        internal static string LoadApiKey()
        {
            try
            {
                if (!File.Exists(KeyPath)) return "";
                byte[] encrypted = File.ReadAllBytes(KeyPath);
                byte[] clear = ProtectedData.Unprotect(encrypted, Entropy, DataProtectionScope.CurrentUser);
                string value = Encoding.UTF8.GetString(clear);
                Array.Clear(clear, 0, clear.Length);
                return value;
            }
            catch { return ""; }
        }

        internal static bool HasApiKey { get { return LoadApiKey().Length > 0; } }
    }

    internal static class PhoneAiFeed
    {
        private static readonly object Sync = new object();
        private static System.Threading.Timer flushTimer;
        private static string endpoint = "";
        private static string pendingText = "";
        private static bool pendingDone;

        internal static void Configure(string webSocketUrl)
        {
            try
            {
                Uri uri = new Uri(webSocketUrl
                    .Replace("wss://", "https://")
                    .Replace("ws://", "http://"));
                endpoint = uri.GetLeftPart(UriPartial.Authority) + "/api/phone/ai";
            }
            catch { endpoint = ""; }
        }

        internal static void Post(string text, bool done)
        {
            if (endpoint.Length == 0) return;
            lock (Sync)
            {
                pendingText = text ?? "";
                pendingDone = done;
                if (flushTimer == null)
                    flushTimer = new System.Threading.Timer(
                        delegate { Flush(); }, null, 150, Timeout.Infinite);
                else
                    flushTimer.Change(150, Timeout.Infinite);
            }
        }

        private static void Flush()
        {
            string text;
            bool done;
            lock (Sync)
            {
                text = pendingText;
                done = pendingDone;
                pendingText = "";
                pendingDone = false;
            }
            if (text.Length == 0 && !done) return;
            try
            {
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                string body = serializer.Serialize(new Dictionary<string, object>
                {
                    { "text", text },
                    { "done", done }
                });
                byte[] bytes = Encoding.UTF8.GetBytes(body);
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(endpoint);
                request.Method = "POST";
                request.ContentType = "application/json";
                request.ContentLength = bytes.Length;
                request.Timeout = 1500;
                using (Stream stream = request.GetRequestStream()) stream.Write(bytes, 0, bytes.Length);
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse()) { }
            }
            catch { }
        }
    }

    // 每次 AI 请求把实际发送的系统提示词推给本机服务端，供 web 设置页查看。
    internal static class PromptFeed
    {
        private static readonly object Sync = new object();
        private static string endpoint = "";

        internal static void Configure(string webSocketUrl)
        {
            try
            {
                Uri uri = new Uri(webSocketUrl
                    .Replace("wss://", "https://")
                    .Replace("ws://", "http://"));
                endpoint = uri.GetLeftPart(UriPartial.Authority) + "/api/ai/prompt";
            }
            catch { endpoint = ""; }
        }

        internal static void Post(string prompt)
        {
            if (endpoint.Length == 0 || string.IsNullOrEmpty(prompt)) return;
            string body = new JavaScriptSerializer().Serialize(
                new Dictionary<string, object> { { "prompt", prompt } });
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    HttpWebRequest request = (HttpWebRequest)WebRequest.Create(endpoint);
                    request.Method = "POST";
                    request.ContentType = "application/json";
                    request.Timeout = 2000;
                    byte[] bytes = Encoding.UTF8.GetBytes(body);
                    request.ContentLength = bytes.Length;
                    using (Stream stream = request.GetRequestStream()) stream.Write(bytes, 0, bytes.Length);
                    using (HttpWebResponse response = (HttpWebResponse)request.GetResponse()) { }
                }
                catch { }
            });
        }
    }

    internal sealed class AiProbeResult
    {
        internal string Content;
        internal string Model;
        internal double Seconds;
    }

    internal static class DeepSeekClient
    {
        private static readonly HttpClient SharedClient = CreateHttpClient();

        private static HttpClient CreateHttpClient()
        {
            HttpClient client = new HttpClient();
            client.Timeout = TimeSpan.FromSeconds(60);
            // .NET HttpClient 默认不带 User-Agent，会被 Cloudflare 站点（如部分中转 API）按签名封禁（error code: 1010）
            client.DefaultRequestHeaders.TryAddWithoutValidation("User-Agent", "VoxRibbon/0.1");
            return client;
        }

        internal static string PromptForMode(OverlayConfig config)
        {
            // 面试上下文：把候选人真实简历 + 目标职位 JD + 公司 + 附加背景
            // 拼成参考上下文，让 AI 回答时紧贴候选人实际经历，而不是泛泛而谈。
            // 格式参考 HireMe：[Resume]\n<全文>\n[JD]\n<JD文本>，连同面试转录一起发给模型。
            List<string> contextSections = new List<string>();
            if (!string.IsNullOrWhiteSpace(config.ResumeContext))
                contextSections.Add("[Resume]\n" + config.ResumeContext.Trim());
            if (!string.IsNullOrWhiteSpace(config.JdContext))
                contextSections.Add("[JD]\n" + config.JdContext.Trim());
            if (!string.IsNullOrWhiteSpace(config.TargetCompany))
                contextSections.Add("[Target Company]\n" + config.TargetCompany.Trim());
            if (!string.IsNullOrWhiteSpace(config.ExtraContext))
                contextSections.Add("[Extra Context]\n" + config.ExtraContext.Trim());

            string contextBlock = contextSections.Count > 0
                ? string.Join("\n\n", contextSections) + "\n\n"
                : "";

            string persona;
            if (contextBlock.Length > 0)
            {
                persona = "你是面试辅助助手。以下是候选人的真实背景资料，请基于这些经历来理解和回答问题，"
                    + "必要时直接引用候选人的项目/技能/公司经历，让回答更贴合候选人实际，而不是泛泛而谈。";
            }
            else
            {
                persona = "你是实时字幕助手。";
            }

            string instruction;
            if (config.AiMode == "summary")
                instruction = "请用一句简洁中文总结这段语音的核心信息。";
            else if (config.AiMode == "qa")
                instruction = "请识别语音中的问题并直接给出简洁、准确的中文回答。若没有问题，说明未检测到明确问题。";
            else if (config.AiMode == "explain")
                instruction = "请用简洁中文解释这段语音涉及的概念或意图，不要复述全文。";
            else if (config.AiMode == "translate")
                instruction = "请将这段中文转写准确翻译为自然、简洁的英文，只输出译文。";
            else
                instruction = "若内容中包含明确问题，直接回答；否则用一句话总结或解释重点。回答简洁，不复述全文；转写可能有少量错误，请结合上下文理解。";

            string prompt;
            if (!string.IsNullOrWhiteSpace(config.AiOverridePrompt))
            {
                // 完全自定义模式：整体替换内置模板，但面试上下文（简历/JD 等）仍会拼在前面。
                prompt = contextBlock + config.AiOverridePrompt.Trim();
            }
            else if (!string.IsNullOrWhiteSpace(config.AiBuiltInPrompt))
            {
                // 设置页改过的内置模板：替换硬编码模板，面试上下文与附加要求仍自动附加。
                prompt = contextBlock + config.AiBuiltInPrompt.Trim();
            }
            else
            {
                prompt = contextBlock
                    + "这是连续的面试转写内容。请结合前几轮上下文理解当前消息，并先默默修正明显的识别错误。\n"
                    + persona + "\n" + instruction
                    + "\n直接输出纯文本，不要使用 Markdown 标记（如 **加粗**、# 标题、代码块围栏）。";
            }
            if (!string.IsNullOrWhiteSpace(config.AiSystemPrompt))
                prompt += "\n附加要求：" + config.AiSystemPrompt.Trim();
            return prompt;
        }

        internal static async Task<string> CompleteAsync(
            OverlayConfig config, string apiKey, string transcript, CancellationToken token)
        {
            AiProbeResult result = await SendChatAsync(
                config, apiKey, "转写文本：\n" + transcript, token);
            return result.Content;
        }

        // 一次非流式对话：返回答案、响应里的实际模型名（中转站可能改路由）与耗时。
        internal static async Task<AiProbeResult> SendChatAsync(
            OverlayConfig config, string apiKey, string userText, CancellationToken token)
        {
            System.Diagnostics.Stopwatch watch = System.Diagnostics.Stopwatch.StartNew();
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            Dictionary<string, object> system = new Dictionary<string, object>();
            system["role"] = "system";
            system["content"] = PromptForMode(config);
            PromptFeed.Post(Convert.ToString(system["content"]));
            Dictionary<string, object> user = new Dictionary<string, object>();
            user["role"] = "user";
            user["content"] = userText;
            Dictionary<string, object> payload = new Dictionary<string, object>();
            payload["model"] = config.AiModel;
            payload["messages"] = new object[] { system, user };
            payload["stream"] = false;
            payload["max_tokens"] = 500;
            payload["temperature"] = 0.3;

            string endpoint = config.AiBaseUrl.TrimEnd('/') + "/chat/completions";
            if (!endpoint.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
                && !endpoint.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("接口地址必须以 http:// 或 https:// 开头");
            using (HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Post, endpoint))
            {
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
                request.Content = new StringContent(serializer.Serialize(payload), Encoding.UTF8, "application/json");
                using (HttpResponseMessage response = await SharedClient.SendAsync(request, token))
                {
                    string body = await response.Content.ReadAsStringAsync();
                    if (!response.IsSuccessStatusCode)
                        throw new InvalidOperationException("AI HTTP " + (int)response.StatusCode + ": " + body);
                    Dictionary<string, object> root = serializer.Deserialize<Dictionary<string, object>>(body);
                    IList choices = root.ContainsKey("choices") ? root["choices"] as IList : null;
                    if (choices == null || choices.Count == 0) throw new InvalidOperationException("AI 返回中没有 choices");
                    Dictionary<string, object> choice = choices[0] as Dictionary<string, object>;
                    // 兼容中转站：reasoning 模型可能缺 message 或 content 字段
                    Dictionary<string, object> message = choice == null || !choice.ContainsKey("message")
                        ? null
                        : choice["message"] as Dictionary<string, object>;
                    string content = message == null || !message.ContainsKey("content")
                        ? ""
                        : Convert.ToString(message["content"]);
                    if (string.IsNullOrWhiteSpace(content)) throw new InvalidOperationException("AI 返回了空内容（思考型模型可能耗尽 token，请换模型或调小附加要求）");
                    AiProbeResult result = new AiProbeResult();
                    result.Content = content.Trim();
                    result.Model = root.ContainsKey("model") ? Convert.ToString(root["model"]) : "";
                    watch.Stop();
                    result.Seconds = watch.Elapsed.TotalSeconds;
                    return result;
                }
            }
        }

        internal static async Task<string> CompleteStreamAsync(
            OverlayConfig config,
            string apiKey,
            IList<ConversationMessage> conversation,
            Action<string> onPartial,
            CancellationToken token)
        {
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            Dictionary<string, object> system = new Dictionary<string, object>();
            system["role"] = "system";
            system["content"] = PromptForMode(config);
            PromptFeed.Post(Convert.ToString(system["content"]));
            List<object> messages = new List<object>();
            messages.Add(system);
            foreach (ConversationMessage item in conversation)
            {
                Dictionary<string, object> message = new Dictionary<string, object>();
                message["role"] = item.Role;
                message["content"] = item.Role == "user"
                    ? "转写文本：\n" + item.Text
                    : item.Text;
                messages.Add(message);
            }
            Dictionary<string, object> payload = new Dictionary<string, object>();
            payload["model"] = config.AiModel;
            payload["messages"] = messages.ToArray();
            payload["stream"] = true;
            payload["max_tokens"] = 500;
            payload["temperature"] = 0.3;

            string endpoint = config.AiBaseUrl.TrimEnd('/') + "/chat/completions";
            if (!endpoint.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
                && !endpoint.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("接口地址必须以 http:// 或 https:// 开头");
            using (HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Post, endpoint))
            {
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
                request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
                request.Content = new StringContent(serializer.Serialize(payload), Encoding.UTF8, "application/json");
                using (HttpResponseMessage response = await SharedClient.SendAsync(
                    request, HttpCompletionOption.ResponseHeadersRead, token).ConfigureAwait(false))
                {
                    if (!response.IsSuccessStatusCode)
                    {
                        string errorBody = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                        throw new InvalidOperationException(
                            "AI HTTP " + (int)response.StatusCode + ": " + errorBody);
                    }
                    StringBuilder accumulated = new StringBuilder();
                    using (Stream stream = await response.Content.ReadAsStreamAsync().ConfigureAwait(false))
                    using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                    {
                        while (!reader.EndOfStream)
                        {
                            token.ThrowIfCancellationRequested();
                            string line = await reader.ReadLineAsync().ConfigureAwait(false);
                            if (line == null) break;
                            if (!line.StartsWith("data:", StringComparison.OrdinalIgnoreCase)) continue;
                            string data = line.Substring(5).Trim();
                            if (data == "[DONE]") break;
                            if (data.Length == 0) continue;
                            Dictionary<string, object> root =
                                serializer.Deserialize<Dictionary<string, object>>(data);
                            IList choices = root.ContainsKey("choices") ? root["choices"] as IList : null;
                            if (choices == null || choices.Count == 0) continue;
                            Dictionary<string, object> choice = choices[0] as Dictionary<string, object>;
                            Dictionary<string, object> delta = choice == null || !choice.ContainsKey("delta")
                                ? null
                                : choice["delta"] as Dictionary<string, object>;
                            string content = delta != null && delta.ContainsKey("content")
                                ? Convert.ToString(delta["content"])
                                : "";
                            if (string.IsNullOrEmpty(content)) continue;
                            accumulated.Append(content);
                            if (onPartial != null) onPartial(content);
                        }
                    }
                    string result = accumulated.ToString().Trim();
                    if (result.Length == 0) throw new InvalidOperationException("AI 流式返回了空内容");
                    return result;
                }
            }
        }
    }

    internal static class LocalTranslationClient
    {
        private static readonly HttpClient Client = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(120)
        };

        private static string Endpoint(string webSocketUrl)
        {
            Uri socket = new Uri(webSocketUrl);
            UriBuilder builder = new UriBuilder(socket);
            builder.Scheme = socket.Scheme == "wss" ? "https" : "http";
            builder.Path = "/api/translate";
            builder.Query = "";
            return builder.Uri.ToString();
        }

        internal static async Task<string> TranslateAsync(
            string webSocketUrl, string source, CancellationToken token)
        {
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            Dictionary<string, object> payload = new Dictionary<string, object>();
            payload["text"] = source;
            using (HttpRequestMessage request = new HttpRequestMessage(
                HttpMethod.Post, Endpoint(webSocketUrl)))
            {
                request.Content = new StringContent(
                    serializer.Serialize(payload), Encoding.UTF8, "application/json");
                using (HttpResponseMessage response = await Client.SendAsync(request, token).ConfigureAwait(false))
                {
                    string body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                    if (!response.IsSuccessStatusCode)
                        throw new InvalidOperationException(
                            "本地翻译 HTTP " + (int)response.StatusCode + ": " + body);
                    Dictionary<string, object> result =
                        serializer.Deserialize<Dictionary<string, object>>(body);
                    return result.ContainsKey("translation")
                        ? Convert.ToString(result["translation"]).Trim()
                        : "";
                }
            }
        }
    }

    internal sealed class SubtitleState
    {
        private readonly List<string> finals = new List<string>();
        private string partial = "";
        private int partialSegment = -1;

        private static string Clean(object value)
        {
            string text = value == null ? "" : Convert.ToString(value);
            return string.Join(" ", text.Split((char[])null, StringSplitOptions.RemoveEmptyEntries));
        }

        internal bool Apply(Dictionary<string, object> message)
        {
            string type = message.ContainsKey("type") ? Convert.ToString(message["type"]) : "";
            if (type != "partial" && type != "final") return false;
            string text = message.ContainsKey("text") ? Clean(message["text"]) : "";
            int segment = message.ContainsKey("segment_id") ? Convert.ToInt32(message["segment_id"]) : -1;
            if (type == "partial")
            {
                if (text.Length == 0) return false;
                bool changed = partial != text || partialSegment != segment;
                partial = text;
                partialSegment = segment;
                return changed;
            }

            string finalText = text.Length > 0 ? text : (partialSegment == segment ? partial : "");
            partial = "";
            partialSegment = -1;
            if (finalText.Length == 0) return false;
            if (finals.Count == 0 || finals[finals.Count - 1] != finalText)
            {
                finals.Add(finalText);
                while (finals.Count > 20) finals.RemoveAt(0);
            }
            return true;
        }

        internal List<string> Finals { get { return new List<string>(finals); } }
        internal string Partial { get { return partial; } }
        internal int PartialSegment { get { return partialSegment; } }
        internal bool HasText { get { return finals.Count > 0 || partial.Length > 0; } }

        internal void DiscardPartial()
        {
            partial = "";
            partialSegment = -1;
        }

        internal void Clear()
        {
            finals.Clear();
            partial = "";
            partialSegment = -1;
        }
    }

    internal sealed class ChatEntry
    {
        internal string Role;
        internal string Text;
        internal bool Streaming;
        internal int SegmentId;
    }

    internal sealed class ConversationMessage
    {
        internal string Role;
        internal string Text;

        internal ConversationMessage(string role, string text)
        {
            Role = role;
            Text = text;
        }
    }

    internal sealed class SpeechBatch
    {
        internal readonly List<string> Segments = new List<string>();
        internal ChatEntry Entry;

        internal void Append(string text)
        {
            Segments.Add(text);
            Entry.Text = string.Join(" ", Segments.ToArray());
        }

        internal string CombinedText
        {
            get { return string.Join("\n", Segments.ToArray()); }
        }
    }

    internal sealed class WebSocketSubscriber : IDisposable
    {
        private readonly string url;
        private readonly Action<Dictionary<string, object>> onMessage;
        private readonly Action<string> onStatus;
        private readonly CancellationTokenSource cancellation = new CancellationTokenSource();
        private ClientWebSocket current;

        internal WebSocketSubscriber(
            string url,
            Action<Dictionary<string, object>> onMessage,
            Action<string> onStatus)
        {
            this.url = url;
            this.onMessage = onMessage;
            this.onStatus = onStatus;
        }

        internal void Start()
        {
            Task.Run((Func<Task>)RunAsync);
        }

        private async Task RunAsync()
        {
            int delay = 500;
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            while (!cancellation.IsCancellationRequested)
            {
                bool shouldDelay = false;
                try
                {
                    onStatus("connecting");
                    using (ClientWebSocket socket = new ClientWebSocket())
                    {
                        current = socket;
                        await socket.ConnectAsync(new Uri(url), cancellation.Token);
                        onStatus("connected");
                        delay = 500;
                        byte[] buffer = new byte[8192];
                        while (socket.State == WebSocketState.Open && !cancellation.IsCancellationRequested)
                        {
                            using (MemoryStream stream = new MemoryStream())
                            {
                                WebSocketReceiveResult result;
                                do
                                {
                                    result = await socket.ReceiveAsync(
                                        new ArraySegment<byte>(buffer), cancellation.Token);
                                    if (result.MessageType == WebSocketMessageType.Close) break;
                                    stream.Write(buffer, 0, result.Count);
                                }
                                while (!result.EndOfMessage);
                                if (result.MessageType == WebSocketMessageType.Close) break;
                                string json = Encoding.UTF8.GetString(stream.ToArray());
                                Dictionary<string, object> message =
                                    serializer.Deserialize<Dictionary<string, object>>(json);
                                if (message != null) onMessage(message);
                            }
                        }
                    }
                }
                catch (OperationCanceledException) { break; }
                catch (Exception error)
                {
                    onStatus("disconnected: " + error.Message);
                    shouldDelay = true;
                }
                finally { current = null; }
                if (shouldDelay)
                {
                    try { await Task.Delay(delay, cancellation.Token); } catch { break; }
                    delay = Math.Min(8000, (int)(delay * 1.8));
                }
            }
        }

        public void Dispose()
        {
            cancellation.Cancel();
            try
            {
                if (current != null) current.Abort();
            }
            catch { }
            cancellation.Dispose();
        }
    }

    internal sealed class SettingsWindow : Window
    {
        private readonly OverlayWindow overlay;
        private readonly TextBox fontSizeBox;
        private readonly Slider opacitySlider;
        private readonly ComboBox screenBox;
        private readonly ComboBox fontBox;
        private readonly ComboBox colorBox;
        private readonly CheckBox lockedBox;
        private readonly CheckBox captureBox;
        private readonly CheckBox liveTranslateBox;
        private readonly CheckBox aiEnabledBox;
        private readonly PasswordBox apiKeyBox;
        private readonly ComboBox aiModelBox;
        private readonly ComboBox aiModeBox;
        private readonly TextBox aiBaseUrlBox;
        private readonly Slider aiDelaySlider;
        private readonly TextBox aiPromptBox;
        private readonly TextBox aiOverrideBox;
        private readonly TextBox aiPromptPreviewBox;
        private readonly TextBox aiTestQuestionBox;
        private readonly TextBox aiTestAnswerBox;
        private readonly TextBlock aiTestStatus;
        private readonly TextBox resumeBox;
        private readonly TextBox jdBox;
        private readonly TextBox companyBox;
        private readonly TextBox extraBox;
        private readonly CheckBox visionEnabledBox;
        private readonly TextBox visionBaseUrlBox;
        private readonly TextBox visionModelBox;
        private readonly TextBox solvePromptBox;
        private readonly PasswordBox visionApiKeyBox;
        private readonly TextBlock visionStatus;
        private readonly TextBlock aiStatus;
        private readonly TabControl tabs;
        private bool syncing;
        private bool allowShutdownClose;
        internal IntPtr NativeHandle { get; private set; }

        internal SettingsWindow(OverlayWindow overlay)
        {
            this.overlay = overlay;
            Title = "字幕 Overlay 设置";
            WindowStyle = WindowStyle.None;
            AllowsTransparency = true;
            Background = Brushes.Transparent;
            ShowInTaskbar = false;
            Topmost = true;
            ResizeMode = ResizeMode.NoResize;
            SizeToContent = SizeToContent.WidthAndHeight;
            // 内容过长时窗口不超过屏幕工作区，配合页签内 ScrollViewer 滚动查看
            MaxHeight = SystemParameters.WorkArea.Height;

            Border border = new Border();
            border.CornerRadius = new CornerRadius(14);
            border.BorderBrush = new SolidColorBrush(Color.FromRgb(226, 232, 240));
            border.BorderThickness = new Thickness(1);
            border.Background = new SolidColorBrush(Color.FromArgb(252, 248, 250, 253));
            border.Padding = new Thickness(18, 14, 18, 14);
            StackPanel shell = new StackPanel();
            tabs = new TabControl();
            tabs.Width = 500;
            tabs.Background = Brushes.Transparent;
            tabs.Foreground = new SolidColorBrush(Color.FromRgb(31, 41, 55));
            tabs.BorderThickness = new Thickness(0);
            StackPanel root = new StackPanel();
            root.Margin = new Thickness(8, 12, 8, 8);
            TabItem appearanceTab = new TabItem();
            appearanceTab.Header = "字幕外观";
            appearanceTab.Content = root;
            tabs.Items.Add(appearanceTab);
            StackPanel aiRoot = new StackPanel();
            aiRoot.Margin = new Thickness(8, 12, 8, 8);
            TabItem aiTab = new TabItem();
            aiTab.Header = "翻译 / AI";
            ScrollViewer aiScroll = new ScrollViewer();
            aiScroll.Content = aiRoot;
            aiScroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            aiScroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled;
            aiTab.Content = aiScroll;
            tabs.Items.Add(aiTab);
            shell.Children.Add(tabs);
            border.Child = shell;
            Content = border;

            SourceInitialized += delegate
            {
                NativeHandle = new WindowInteropHelper(this).Handle;
                overlay.ApplyCaptureProtection(NativeHandle);
            };

            TextBlock hint = MakeText("编辑模式 · 拖动字幕框移动 · Ctrl+Alt+O（冲突时 Ctrl+Shift+O）", 13);
            hint.Foreground = new SolidColorBrush(Color.FromRgb(37, 99, 235));
            hint.FontWeight = FontWeights.SemiBold;
            hint.Margin = new Thickness(0, 0, 0, 8);
            root.Children.Add(hint);
            TextBlock resizeHint = MakeText("宽高：解锁后移到字幕，直接拖动透明外框的边或四角", 12);
            resizeHint.Foreground = new SolidColorBrush(Color.FromRgb(100, 116, 139));
            resizeHint.Margin = new Thickness(0, 0, 0, 6);
            root.Children.Add(resizeHint);

            fontSizeBox = AddNumberBox(root, "字号", 12, 96, delegate(double value)
            {
                if (!syncing) overlay.SetFontSize(value);
            });
            opacitySlider = AddSlider(root, "透明度", 45, 98, delegate(double value)
            {
                if (!syncing) overlay.SetOverlayOpacity(value / 100.0);
            });

            StackPanel typeRow = new StackPanel();
            typeRow.Orientation = Orientation.Horizontal;
            typeRow.Margin = new Thickness(0, 5, 0, 8);
            typeRow.Children.Add(MakeText("字体", 13));
            fontBox = new ComboBox();
            foreach (string family in new string[] { "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimHei", "KaiTi", "Arial" })
                fontBox.Items.Add(family);
            fontBox.Width = 190;
            fontBox.Margin = new Thickness(8, 0, 18, 0);
            fontBox.SelectionChanged += delegate
            {
                if (!syncing && fontBox.SelectedItem != null)
                    overlay.SetFontFamily(Convert.ToString(fontBox.SelectedItem));
            };
            typeRow.Children.Add(fontBox);
            typeRow.Children.Add(MakeText("颜色", 13));
            colorBox = new ComboBox();
            colorBox.Items.Add("白色");
            colorBox.Items.Add("黄色");
            colorBox.Items.Add("青色");
            colorBox.Items.Add("绿色");
            colorBox.Width = 95;
            colorBox.Margin = new Thickness(8, 0, 0, 0);
            colorBox.SelectionChanged += delegate
            {
                if (!syncing && colorBox.SelectedItem != null)
                    overlay.SetTextColor(ColorCode(Convert.ToString(colorBox.SelectedItem)));
            };
            typeRow.Children.Add(colorBox);
            root.Children.Add(typeRow);

            StackPanel options = new StackPanel();
            options.Orientation = Orientation.Horizontal;
            options.Margin = new Thickness(0, 5, 0, 8);
            options.Children.Add(MakeText("显示器", 13));
            screenBox = new ComboBox();
            screenBox.Width = 185;
            screenBox.Margin = new Thickness(8, 0, 0, 0);
            screenBox.SelectionChanged += delegate
            {
                if (!syncing && screenBox.SelectedItem != null)
                    overlay.MoveToScreen(Convert.ToString(screenBox.SelectedItem));
            };
            options.Children.Add(screenBox);
            root.Children.Add(options);
            lockedBox = new CheckBox();
            lockedBox.Content = "锁定字幕位置（锁定后字幕主体鼠标穿透）";
            lockedBox.Foreground = new SolidColorBrush(Color.FromRgb(31, 41, 55));
            lockedBox.Margin = new Thickness(0, 4, 0, 4);
            lockedBox.Checked += delegate { if (!syncing) overlay.SetPositionLocked(true); };
            lockedBox.Unchecked += delegate { if (!syncing) overlay.SetPositionLocked(false); };
            root.Children.Add(lockedBox);
            captureBox = new CheckBox();
            captureBox.Content = "共享屏幕 / 录屏时隐藏本窗口（会议软件与截图工具不可见）";
            captureBox.Foreground = new SolidColorBrush(Color.FromRgb(31, 41, 55));
            captureBox.Margin = new Thickness(0, 4, 0, 4);
            captureBox.Checked += delegate { if (!syncing) overlay.SetCaptureInvisible(true); };
            captureBox.Unchecked += delegate { if (!syncing) overlay.SetCaptureInvisible(false); };
            root.Children.Add(captureBox);

            TextBlock aiHint = MakeText("字幕停顿后，将本轮 final 文本发送给 OpenAI 兼容 AI 接口。API Key 使用 Windows DPAPI 加密。", 12);
            aiHint.TextWrapping = TextWrapping.Wrap;
            aiHint.Foreground = new SolidColorBrush(Color.FromRgb(37, 99, 235));
            aiHint.Margin = new Thickness(0, 0, 0, 10);
            aiRoot.Children.Add(aiHint);
            liveTranslateBox = new CheckBox();
            liveTranslateBox.Content = "本地英文 → 中文实时翻译（英文模型下次启动生效）";
            liveTranslateBox.Foreground = new SolidColorBrush(Color.FromRgb(22, 163, 74));
            liveTranslateBox.Margin = new Thickness(0, 0, 0, 10);
            aiRoot.Children.Add(liveTranslateBox);
            aiEnabledBox = new CheckBox();
            aiEnabledBox.Content = "启用 AI 助手";
            aiEnabledBox.Foreground = new SolidColorBrush(Color.FromRgb(31, 41, 55));
            aiEnabledBox.Margin = new Thickness(0, 0, 0, 10);
            aiRoot.Children.Add(aiEnabledBox);

            aiRoot.Children.Add(MakeText("AI 接口 API Key", 13));
            apiKeyBox = new PasswordBox();
            apiKeyBox.Margin = new Thickness(0, 4, 0, 10);
            apiKeyBox.Padding = new Thickness(8, 6, 8, 6);
            aiRoot.Children.Add(apiKeyBox);

            StackPanel aiOptions = new StackPanel();
            aiOptions.Orientation = Orientation.Horizontal;
            aiOptions.Margin = new Thickness(0, 0, 0, 8);
            aiOptions.Children.Add(MakeText("模型", 13));
            aiModelBox = new ComboBox();
            aiModelBox.Items.Add("deepseek-v4-flash");
            aiModelBox.Items.Add("deepseek-v4-pro");
            aiModelBox.IsEditable = true;
            aiModelBox.Width = 170;
            aiModelBox.Margin = new Thickness(8, 0, 18, 0);
            aiOptions.Children.Add(aiModelBox);
            aiOptions.Children.Add(MakeText("模式", 13));
            aiModeBox = new ComboBox();
            aiModeBox.Items.Add("自动判断");
            aiModeBox.Items.Add("一句话总结");
            aiModeBox.Items.Add("回答问题");
            aiModeBox.Items.Add("解释内容");
            aiModeBox.Items.Add("中文翻译为英文");
            aiModeBox.Width = 160;
            aiModeBox.Margin = new Thickness(8, 0, 0, 0);
            aiOptions.Children.Add(aiModeBox);
            aiRoot.Children.Add(aiOptions);

            StackPanel aiBaseUrlRow = new StackPanel();
            aiBaseUrlRow.Orientation = Orientation.Horizontal;
            aiBaseUrlRow.Margin = new Thickness(0, 0, 0, 8);
            aiBaseUrlRow.Children.Add(MakeText("接口地址", 13));
            aiBaseUrlBox = new TextBox();
            aiBaseUrlBox.Width = 330;
            aiBaseUrlBox.Margin = new Thickness(8, 0, 0, 0);
            aiBaseUrlRow.Children.Add(aiBaseUrlBox);
            aiRoot.Children.Add(aiBaseUrlRow);
            TextBlock aiBaseUrlHint = MakeText(
                "OpenAI 兼容接口地址（不含 /chat/completions），留空用 DeepSeek 官方。示例：https://api.deepseek.com 或 https://dashscope.aliyuncs.com/compatible-mode/v1", 12);
            aiBaseUrlHint.TextWrapping = TextWrapping.Wrap;
            aiBaseUrlHint.Foreground = new SolidColorBrush(Color.FromRgb(100, 116, 139));
            aiBaseUrlHint.Margin = new Thickness(0, 0, 0, 8);
            aiRoot.Children.Add(aiBaseUrlHint);

            aiDelaySlider = AddSecondsSlider(aiRoot);
            aiRoot.Children.Add(MakeText("完全自定义系统提示词（可选，填写后替换内置模式提示词；简历/JD 上下文仍会附加）", 13));
            aiOverrideBox = new TextBox();
            aiOverrideBox.Height = 76;
            aiOverrideBox.AcceptsReturn = true;
            aiOverrideBox.TextWrapping = TextWrapping.Wrap;
            aiOverrideBox.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            aiOverrideBox.Margin = new Thickness(0, 4, 0, 10);
            aiOverrideBox.Padding = new Thickness(7);
            aiRoot.Children.Add(aiOverrideBox);
            aiRoot.Children.Add(MakeText("附加提示词（可选）", 13));
            aiPromptBox = new TextBox();
            aiPromptBox.Height = 76;
            aiPromptBox.AcceptsReturn = true;
            aiPromptBox.TextWrapping = TextWrapping.Wrap;
            aiPromptBox.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            aiPromptBox.Margin = new Thickness(0, 4, 0, 10);
            aiPromptBox.Padding = new Thickness(7);
            aiRoot.Children.Add(aiPromptBox);

            aiRoot.Children.Add(MakeSection("👤 面试上下文（AI 回答时参考以下真实背景）"));

            aiRoot.Children.Add(MakeText("📋 简历全文（面试回答会参考这些真实经历，建议 500–3000 字）", 13));
            resumeBox = new TextBox();
            resumeBox.Height = 130;
            resumeBox.AcceptsReturn = true;
            resumeBox.TextWrapping = TextWrapping.Wrap;
            resumeBox.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            resumeBox.Margin = new Thickness(0, 4, 0, 10);
            resumeBox.Padding = new Thickness(7);
            resumeBox.FontFamily = new FontFamily("Microsoft YaHei UI");
            aiRoot.Children.Add(resumeBox);

            aiRoot.Children.Add(MakeText("🎯 目标职位 JD（可选，填写后 AI 会对照 JD 要求来回答）", 13));
            jdBox = new TextBox();
            jdBox.Height = 90;
            jdBox.AcceptsReturn = true;
            jdBox.TextWrapping = TextWrapping.Wrap;
            jdBox.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            jdBox.Margin = new Thickness(0, 4, 0, 10);
            jdBox.Padding = new Thickness(7);
            aiRoot.Children.Add(jdBox);

            StackPanel companyRow = new StackPanel();
            companyRow.Orientation = Orientation.Horizontal;
            companyRow.Margin = new Thickness(0, 0, 0, 10);
            companyRow.Children.Add(MakeText("🏢 目标公司", 13));
            companyBox = new TextBox();
            companyBox.Width = 280;
            companyBox.Margin = new Thickness(8, 0, 0, 0);
            companyRow.Children.Add(companyBox);
            aiRoot.Children.Add(companyRow);

            aiRoot.Children.Add(MakeText("📝 附加背景（可选，如：我应聘的是后端岗、我转专业、我工作 3 年）", 13));
            extraBox = new TextBox();
            extraBox.Height = 60;
            extraBox.AcceptsReturn = true;
            extraBox.TextWrapping = TextWrapping.Wrap;
            extraBox.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            extraBox.Margin = new Thickness(0, 4, 0, 10);
            extraBox.Padding = new Thickness(7);
            aiRoot.Children.Add(extraBox);

            aiRoot.Children.Add(MakeSection("🧠 内部提示词预览与回答测试"));

            aiRoot.Children.Add(MakeText("内部系统提示词（实际发送预览，只读）", 13));
            aiPromptPreviewBox = new TextBox();
            aiPromptPreviewBox.Height = 110;
            aiPromptPreviewBox.IsReadOnly = true;
            aiPromptPreviewBox.TextWrapping = TextWrapping.Wrap;
            aiPromptPreviewBox.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            aiPromptPreviewBox.Margin = new Thickness(0, 4, 0, 10);
            aiPromptPreviewBox.Padding = new Thickness(7);
            aiPromptPreviewBox.Foreground = new SolidColorBrush(Color.FromRgb(100, 116, 139));
            aiRoot.Children.Add(aiPromptPreviewBox);

            aiRoot.Children.Add(MakeText("回答测试（模拟面试问题，走与字幕 AI 完全相同的管线）", 13));
            aiTestQuestionBox = new TextBox();
            aiTestQuestionBox.Height = 44;
            aiTestQuestionBox.AcceptsReturn = true;
            aiTestQuestionBox.TextWrapping = TextWrapping.Wrap;
            aiTestQuestionBox.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            aiTestQuestionBox.Margin = new Thickness(0, 4, 0, 6);
            aiTestQuestionBox.Padding = new Thickness(7);
            aiRoot.Children.Add(aiTestQuestionBox);
            StackPanel aiTestRow = new StackPanel();
            aiTestRow.Orientation = Orientation.Horizontal;
            aiTestRow.Margin = new Thickness(0, 0, 0, 6);
            Button aiTestSend = MakeButton("发送测试问题");
            aiTestSend.Click += TestAskAi;
            aiTestRow.Children.Add(aiTestSend);
            aiTestStatus = MakeText("", 12);
            aiTestStatus.Foreground = new SolidColorBrush(Color.FromRgb(100, 116, 139));
            aiTestRow.Children.Add(aiTestStatus);
            aiRoot.Children.Add(aiTestRow);
            aiTestAnswerBox = new TextBox();
            aiTestAnswerBox.Height = 110;
            aiTestAnswerBox.IsReadOnly = true;
            aiTestAnswerBox.TextWrapping = TextWrapping.Wrap;
            aiTestAnswerBox.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            aiTestAnswerBox.Margin = new Thickness(0, 0, 0, 10);
            aiTestAnswerBox.Padding = new Thickness(7);
            aiTestAnswerBox.Foreground = new SolidColorBrush(Color.FromRgb(22, 163, 74));
            aiRoot.Children.Add(aiTestAnswerBox);

            aiRoot.Children.Add(MakeSection("📸 截图解题 · 视觉模型（Ctrl+Shift+C 或手机「截题+回答」触发）"));
            visionEnabledBox = new CheckBox();
            visionEnabledBox.Content = "启用截图解题（OpenAI 兼容格式视觉模型）";
            visionEnabledBox.Foreground = new SolidColorBrush(Color.FromRgb(31, 41, 55));
            visionEnabledBox.Margin = new Thickness(0, 4, 0, 6);
            aiRoot.Children.Add(visionEnabledBox);
            StackPanel visionUrlRow = new StackPanel();
            visionUrlRow.Orientation = Orientation.Horizontal;
            visionUrlRow.Margin = new Thickness(0, 0, 0, 6);
            visionUrlRow.Children.Add(MakeText("BaseURL", 13));
            visionBaseUrlBox = new TextBox();
            visionBaseUrlBox.Width = 330;
            visionBaseUrlBox.Margin = new Thickness(8, 0, 0, 0);
            visionUrlRow.Children.Add(visionBaseUrlBox);
            aiRoot.Children.Add(visionUrlRow);
            StackPanel visionModelRow = new StackPanel();
            visionModelRow.Orientation = Orientation.Horizontal;
            visionModelRow.Margin = new Thickness(0, 0, 0, 6);
            visionModelRow.Children.Add(MakeText("模型名", 13));
            visionModelBox = new TextBox();
            visionModelBox.Width = 330;
            visionModelBox.Margin = new Thickness(8, 18, 0, 0);
            visionModelRow.Children.Add(visionModelBox);
            aiRoot.Children.Add(visionModelRow);
            StackPanel visionKeyRow = new StackPanel();
            visionKeyRow.Orientation = Orientation.Horizontal;
            visionKeyRow.Margin = new Thickness(0, 0, 0, 4);
            visionKeyRow.Children.Add(MakeText("API Key", 13));
            visionApiKeyBox = new PasswordBox();
            visionApiKeyBox.Width = 250;
            visionApiKeyBox.Margin = new Thickness(8, 0, 8, 0);
            visionKeyRow.Children.Add(visionApiKeyBox);
            Button visionSaveKey = MakeButton("保存 Key");
            visionSaveKey.Click += delegate
            {
                VisionSecretStore.SaveApiKey(visionApiKeyBox.Password);
                visionApiKeyBox.Clear();
                visionStatus.Text = VisionSecretStore.HasApiKey ? "Key 已加密保存" : "尚未设置 Key";
            };
            visionKeyRow.Children.Add(visionSaveKey);
            aiRoot.Children.Add(visionKeyRow);
            visionStatus = MakeText("尚未设置 Key", 12);
            visionStatus.Foreground = new SolidColorBrush(Color.FromRgb(100, 116, 139));
            aiRoot.Children.Add(visionStatus);
            aiRoot.Children.Add(MakeText("解题提示词（留空用内置默认）", 13));
            solvePromptBox = new TextBox();
            solvePromptBox.Height = 60;
            solvePromptBox.AcceptsReturn = true;
            solvePromptBox.TextWrapping = TextWrapping.Wrap;
            solvePromptBox.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            solvePromptBox.Margin = new Thickness(0, 4, 0, 10);
            solvePromptBox.Padding = new Thickness(7);
            aiRoot.Children.Add(solvePromptBox);

            StackPanel testRow = new StackPanel();
            testRow.Orientation = Orientation.Horizontal;
            Button testButton = MakeButton("测试连接");
            testButton.Margin = new Thickness(0, 0, 10, 0);
            testButton.Click += TestConnection;
            aiStatus = MakeText("尚未测试", 12);
            aiStatus.Foreground = new SolidColorBrush(Color.FromRgb(100, 116, 139));
            testRow.Children.Add(testButton);
            testRow.Children.Add(aiStatus);
            aiRoot.Children.Add(testRow);

            StackPanel buttons = new StackPanel();
            buttons.Orientation = Orientation.Horizontal;
            buttons.HorizontalAlignment = HorizontalAlignment.Right;
            Button reset = MakeButton("恢复默认位置");
            Button done = MakeButton("完成");
            reset.Click += delegate { overlay.ResetPosition(); };
            done.Click += delegate { ApplyAllSettings(); overlay.SetEditMode(false); };
            buttons.Children.Add(reset);
            buttons.Children.Add(done);
            shell.Children.Add(buttons);
        }

        private Slider AddSlider(StackPanel root, string title, double min, double max, Action<double> changed)
        {
            Grid grid = new Grid();
            grid.Margin = new Thickness(0, 4, 0, 4);
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(90) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(302) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(48) });
            TextBlock name = MakeText(title, 13);
            Grid.SetColumn(name, 0);
            grid.Children.Add(name);
            Slider slider = new Slider();
            slider.Minimum = min;
            slider.Maximum = max;
            slider.TickFrequency = 1;
            slider.IsSnapToTickEnabled = true;
            slider.Margin = new Thickness(4, 0, 8, 0);
            Grid.SetColumn(slider, 1);
            grid.Children.Add(slider);
            TextBlock value = MakeText("", 12);
            value.TextAlignment = TextAlignment.Right;
            Grid.SetColumn(value, 2);
            grid.Children.Add(value);
            slider.ValueChanged += delegate
            {
                value.Text = Math.Round(slider.Value).ToString();
                changed(slider.Value);
            };
            slider.PreviewMouseLeftButtonUp += delegate { overlay.SaveConfig(); };
            root.Children.Add(grid);
            return slider;
        }

        private TextBox AddNumberBox(
            StackPanel root, string title, double minimum, double maximum, Action<double> changed)
        {
            Grid grid = new Grid();
            grid.Margin = new Thickness(0, 4, 0, 4);
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(90) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(120) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(48) });
            TextBlock name = MakeText(title, 13);
            Grid.SetColumn(name, 0);
            grid.Children.Add(name);
            TextBox input = new TextBox();
            input.Padding = new Thickness(7, 4, 7, 4);
            input.Margin = new Thickness(4, 0, 8, 0);
            input.VerticalContentAlignment = VerticalAlignment.Center;
            Grid.SetColumn(input, 1);
            grid.Children.Add(input);
            TextBlock suffix = MakeText("px", 12);
            Grid.SetColumn(suffix, 2);
            grid.Children.Add(suffix);
            input.TextChanged += delegate
            {
                double value;
                if (!syncing && double.TryParse(input.Text, out value)
                    && value >= minimum && value <= maximum)
                    changed(value);
            };
            input.LostKeyboardFocus += delegate
            {
                double value;
                if (!double.TryParse(input.Text, out value)) value = overlay.CurrentFontSize;
                value = Math.Max(minimum, Math.Min(maximum, value));
                input.Text = Math.Round(value).ToString();
                changed(value);
                overlay.SaveConfig();
            };
            root.Children.Add(grid);
            return input;
        }

        private Slider AddSecondsSlider(StackPanel root)
        {
            Grid grid = new Grid();
            grid.Margin = new Thickness(0, 4, 0, 4);
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(90) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(302) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(48) });
            TextBlock name = MakeText("停顿触发", 13);
            Grid.SetColumn(name, 0);
            grid.Children.Add(name);
            Slider slider = new Slider();
            slider.Minimum = 5;
            slider.Maximum = 80;
            slider.TickFrequency = 1;
            slider.IsSnapToTickEnabled = true;
            slider.Margin = new Thickness(4, 0, 8, 0);
            Grid.SetColumn(slider, 1);
            grid.Children.Add(slider);
            TextBlock value = MakeText("", 12);
            value.TextAlignment = TextAlignment.Right;
            Grid.SetColumn(value, 2);
            grid.Children.Add(value);
            slider.ValueChanged += delegate { value.Text = (slider.Value / 10.0).ToString("0.0") + "s"; };
            slider.PreviewMouseLeftButtonUp += delegate { ApplyAllSettings(); };
            root.Children.Add(grid);
            return slider;
        }

        private static TextBlock MakeText(string text, double size)
        {
            return new TextBlock
            {
                Text = text,
                FontFamily = new FontFamily("Microsoft YaHei UI"),
                FontSize = size,
                Foreground = new SolidColorBrush(Color.FromRgb(31, 41, 55)),
                VerticalAlignment = VerticalAlignment.Center
            };
        }

        private static TextBlock MakeSection(string text)
        {
            TextBlock block = MakeText(text, 14);
            block.FontWeight = FontWeights.SemiBold;
            block.Foreground = new SolidColorBrush(Color.FromRgb(37, 99, 235));
            block.Margin = new Thickness(0, 8, 0, 2);
            return block;
        }

        private static Button MakeButton(string text)
        {
            Button button = new Button();
            button.Content = text;
            button.Padding = new Thickness(12, 6, 12, 6);
            button.Margin = new Thickness(8, 0, 0, 0);
            button.Foreground = new SolidColorBrush(Color.FromRgb(31, 41, 55));
            button.Background = Brushes.White;
            button.BorderBrush = new SolidColorBrush(Color.FromRgb(203, 213, 225));
            return button;
        }

        private static string ColorCode(string name)
        {
            if (name == "黄色") return "#FFE66D";
            if (name == "青色") return "#74E8FF";
            if (name == "绿色") return "#86F7A8";
            return "#FFFFFF";
        }

        private static string ColorName(string code)
        {
            string value = (code ?? "").ToUpperInvariant();
            if (value == "#FFE66D") return "黄色";
            if (value == "#74E8FF") return "青色";
            if (value == "#86F7A8") return "绿色";
            return "白色";
        }

        private static string ModeKey(string display)
        {
            if (display == "一句话总结") return "summary";
            if (display == "回答问题") return "qa";
            if (display == "解释内容") return "explain";
            if (display == "中文翻译为英文") return "translate";
            return "auto";
        }

        private static string ModeDisplay(string key)
        {
            if (key == "summary") return "一句话总结";
            if (key == "qa") return "回答问题";
            if (key == "explain") return "解释内容";
            if (key == "translate") return "中文翻译为英文";
            return "自动判断";
        }

        private void ApplyAllSettings()
        {
            string model = string.IsNullOrWhiteSpace(Convert.ToString(aiModelBox.SelectedItem))
                && string.IsNullOrWhiteSpace(aiModelBox.Text)
                ? "deepseek-v4-flash"
                : (string.IsNullOrWhiteSpace(aiModelBox.Text)
                    ? Convert.ToString(aiModelBox.SelectedItem)
                    : aiModelBox.Text);
            string mode = aiModeBox.SelectedItem == null ? "auto" : ModeKey(Convert.ToString(aiModeBox.SelectedItem));
            SecretStore.SaveApiKey(apiKeyBox.Password);
            overlay.SetLiveTranslationEnabled(liveTranslateBox.IsChecked == true);
            overlay.ApplyAiSettings(
                aiEnabledBox.IsChecked == true,
                model,
                mode,
                aiBaseUrlBox.Text,
                aiDelaySlider.Value / 10.0,
                aiPromptBox.Text,
                aiOverrideBox.Text,
                resumeBox.Text,
                jdBox.Text,
                companyBox.Text,
                extraBox.Text,
                visionEnabledBox.IsChecked == true,
                visionBaseUrlBox.Text,
                visionModelBox.Text,
                solvePromptBox.Text);
            aiPromptPreviewBox.Text = DeepSeekClient.PromptForMode(overlay.CurrentConfig);
        }

        private async void TestAskAi(object sender, RoutedEventArgs args)
        {
            ApplyAllSettings();
            string question = aiTestQuestionBox.Text.Trim();
            if (question.Length == 0)
            {
                aiTestStatus.Text = "请先输入测试问题";
                return;
            }
            string key = SecretStore.LoadApiKey();
            if (key.Length == 0)
            {
                aiTestStatus.Text = "请先填写并保存 API Key";
                return;
            }
            aiTestStatus.Text = "正在请求…";
            aiTestAnswerBox.Text = "";
            try
            {
                AiProbeResult result = await DeepSeekClient.SendChatAsync(
                    overlay.CurrentConfig, key, "转写文本：\n" + question, System.Threading.CancellationToken.None);
                aiTestAnswerBox.Text = OverlayWindow.CleanAiText(result.Content)
                    + "\n\n—— 实际模型: " + (result.Model.Length > 0 ? result.Model : "未知")
                    + " · 耗时 " + Math.Round(result.Seconds, 1) + " 秒";
                aiTestStatus.Text = "完成";
            }
            catch (Exception error)
            {
                aiTestStatus.Text = "";
                aiTestAnswerBox.Text = "请求失败：" + error.Message;
            }
        }

        private async void TestConnection(object sender, RoutedEventArgs args)
        {
            ApplyAllSettings();
            aiStatus.Text = "正在连接…";
            aiStatus.Foreground = new SolidColorBrush(Color.FromRgb(37, 99, 235));
            try
            {
                string result = await overlay.TestAiConnectionAsync();
                aiStatus.Text = "成功：" + result;
                aiStatus.Foreground = new SolidColorBrush(Color.FromRgb(22, 163, 74));
            }
            catch (Exception error)
            {
                aiStatus.Text = "失败：" + error.Message;
                aiStatus.Foreground = new SolidColorBrush(Color.FromRgb(220, 38, 38));
            }
        }

        internal void Sync(OverlayConfig config)
        {
            syncing = true;
            fontSizeBox.Text = Math.Round(config.FontSize).ToString();
            opacitySlider.Value = config.Opacity * 100;
            fontBox.SelectedItem = config.FontFamilyName;
            if (fontBox.SelectedItem == null) fontBox.SelectedItem = "Microsoft YaHei UI";
            colorBox.SelectedItem = ColorName(config.TextColor);
            lockedBox.IsChecked = config.Locked;
            captureBox.IsChecked = config.CaptureInvisible;
            liveTranslateBox.IsChecked = config.LiveTranslateEnabled;
            screenBox.Items.Clear();
            foreach (Forms.Screen screen in Forms.Screen.AllScreens)
                screenBox.Items.Add(screen.DeviceName);
            screenBox.SelectedItem = config.ScreenName;
            if (screenBox.SelectedItem == null && screenBox.Items.Count > 0)
                screenBox.SelectedIndex = 0;
            aiEnabledBox.IsChecked = config.AiEnabled;
            apiKeyBox.Password = SecretStore.LoadApiKey();
            aiModelBox.SelectedItem = config.AiModel;
            if (aiModelBox.SelectedItem == null) aiModelBox.SelectedItem = "deepseek-v4-flash";
            aiModeBox.SelectedItem = ModeDisplay(config.AiMode);
            aiBaseUrlBox.Text = config.AiBaseUrl;
            aiDelaySlider.Value = Math.Round(config.AiSilenceSeconds * 10);
            aiPromptBox.Text = config.AiSystemPrompt;
            aiOverrideBox.Text = config.AiOverridePrompt;
            aiPromptPreviewBox.Text = DeepSeekClient.PromptForMode(config);
            visionEnabledBox.IsChecked = config.VisionEnabled;
            visionBaseUrlBox.Text = config.VisionBaseUrl;
            visionModelBox.Text = config.VisionModel;
            solvePromptBox.Text = config.SolvePrompt;
            visionStatus.Text = VisionSecretStore.HasApiKey ? "Key 已加密保存" : "尚未设置 Key";
            resumeBox.Text = config.ResumeContext;
            jdBox.Text = config.JdContext;
            companyBox.Text = config.TargetCompany;
            extraBox.Text = config.ExtraContext;
            aiStatus.Text = SecretStore.HasApiKey ? "API Key 已加密保存" : "尚未设置 API Key";
            syncing = false;
        }

        internal void SelectAiTab() { tabs.SelectedIndex = 1; }

        internal void CloseForShutdown()
        {
            allowShutdownClose = true;
            Close();
        }

        protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
        {
            if (allowShutdownClose || overlay.IsClosing) return;
            e.Cancel = true;
            ApplyAllSettings();
            overlay.SetEditMode(false);
        }
    }

    internal sealed class LockIndicatorWindow : Window
    {
        private readonly OverlayWindow overlay;
        private readonly Border resetControl;
        private readonly Border lockControl;
        private readonly Border closeControl;
        internal IntPtr NativeHandle { get; private set; }

        internal LockIndicatorWindow(OverlayWindow overlay)
        {
            this.overlay = overlay;
            Title = "字幕位置锁";
            Width = 102;
            Height = 34;
            WindowStyle = WindowStyle.None;
            AllowsTransparency = true;
            Background = Brushes.Transparent;
            ShowInTaskbar = false;
            ShowActivated = false;
            Topmost = true;
            ResizeMode = ResizeMode.NoResize;
            Focusable = false;
            StackPanel controls = new StackPanel();
            controls.Orientation = Orientation.Horizontal;
            resetControl = MakeIconControl(
                "\uE72C",
                "清空对话上下文（保留 system 提示词）",
                Color.FromRgb(96, 165, 250));
            lockControl = MakeIconControl(
                "\uE785",
                "锁定/解锁字幕位置",
                Color.FromRgb(96, 165, 250));
            closeControl = MakeIconControl(
                "\uE8BB",
                "隐藏字幕（使用老板键恢复）",
                Color.FromRgb(248, 113, 113));
            controls.Children.Add(resetControl);
            controls.Children.Add(lockControl);
            controls.Children.Add(closeControl);
            Content = controls;
            SourceInitialized += delegate
            {
                NativeHandle = new WindowInteropHelper(this).Handle;
                NativeMethods.SetNormalInteraction(NativeHandle, false);
                overlay.ApplyCaptureProtection(NativeHandle);
            };
        }

        internal void ActivateControl(int index)
        {
            if (index == 0)
            {
                AppLog.Write("control_reset_click");
                overlay.ResetConversation();
            }
            else if (index == 1)
            {
                AppLog.Write("control_lock_click");
                overlay.TogglePositionLock();
            }
            else if (index == 2)
            {
                AppLog.Write("control_hide_click");
                overlay.ToggleBossVisibility();
            }
        }

        internal int UpdateHoverFromCursor()
        {
            NativeMethods.NativePoint point;
            NativeMethods.NativeRect rect;
            int hovered = -1;
            if (NativeMethods.GetCursorPos(out point)
                && NativeMethods.GetWindowRect(NativeHandle, out rect)
                && point.X >= rect.Left && point.X <= rect.Right
                && point.Y >= rect.Top && point.Y <= rect.Bottom)
                hovered = Math.Max(0, Math.Min(2, (point.X - rect.Left) / 34));
            ApplyHover(resetControl, hovered == 0, Color.FromRgb(96, 165, 250));
            ApplyHover(lockControl, hovered == 1, Color.FromRgb(96, 165, 250));
            ApplyHover(closeControl, hovered == 2, Color.FromRgb(248, 113, 113));
            return hovered;
        }

        private static void ApplyHover(Border control, bool hovered, Color hoverColor)
        {
            TextBlock icon = control.Child as TextBlock;
            if (icon == null) return;
            SolidColorBrush saved = control.Tag as SolidColorBrush;
            icon.Foreground = hovered
                ? new SolidColorBrush(hoverColor)
                : (saved ?? new SolidColorBrush(Color.FromRgb(225, 235, 247)));
        }

        private static Border MakeIconControl(
            string glyph, string tooltip, Color hoverColor)
        {
            Border control = new Border();
            control.Width = 34;
            control.Height = 34;
            control.Background = Brushes.Transparent;
            control.BorderBrush = Brushes.Transparent;
            control.BorderThickness = new Thickness(0);
            control.Cursor = Cursors.Hand;
            control.ToolTip = tooltip;
            TextBlock icon = new TextBlock();
            icon.Text = glyph;
            icon.FontFamily = new FontFamily("Segoe MDL2 Assets");
            icon.FontSize = 16;
            icon.TextAlignment = TextAlignment.Center;
            icon.HorizontalAlignment = HorizontalAlignment.Center;
            icon.VerticalAlignment = VerticalAlignment.Center;
            icon.IsHitTestVisible = false;
            SolidColorBrush normalBrush = new SolidColorBrush(Color.FromRgb(225, 235, 247));
            icon.Foreground = normalBrush;
            control.Tag = normalBrush;
            SolidColorBrush hoverBrush = new SolidColorBrush(hoverColor);
            control.MouseEnter += delegate { icon.Foreground = hoverBrush; };
            control.MouseLeave += delegate
            {
                SolidColorBrush saved = control.Tag as SolidColorBrush;
                icon.Foreground = saved ?? normalBrush;
            };
            icon.Effect = new System.Windows.Media.Effects.DropShadowEffect
            {
                BlurRadius = 4,
                ShadowDepth = 1,
                Opacity = 0.9,
                Color = Colors.Black
            };
            control.Child = icon;
            return control;
        }

        internal void UpdateState(bool locked)
        {
            TextBlock icon = lockControl.Child as TextBlock;
            if (icon != null) icon.Text = locked ? "\uE72E" : "\uE785";
            lockControl.ToolTip = locked ? "点击解锁字幕位置" : "点击锁定字幕位置";
            SolidColorBrush stateBrush = new SolidColorBrush(
                locked ? Color.FromRgb(255, 225, 135) : Color.FromRgb(225, 235, 247));
            lockControl.Tag = stateBrush;
            if (icon != null && !lockControl.IsMouseOver) icon.Foreground = stateBrush;
        }
    }

    internal sealed class OverlayWindow : Window, IDisposable
    {
        private readonly OverlayConfig config;
        private readonly SubtitleState subtitle = new SubtitleState();
        private readonly Border background;
        private readonly ScrollViewer scroll;
        private readonly TextBlock text;
        private readonly System.Windows.Threading.DispatcherTimer fadeTimer;
        private readonly System.Windows.Threading.DispatcherTimer aiTimer;
        private readonly System.Windows.Threading.DispatcherTimer configTimer;
        private readonly System.Windows.Threading.DispatcherTimer hoverTimer;
        private readonly System.Windows.Threading.DispatcherTimer geometrySaveTimer;
        private readonly System.Windows.Threading.DispatcherTimer aiTypewriterTimer;
        private readonly SettingsWindow settings;
        private readonly LockIndicatorWindow lockIndicator;
        private readonly WebSocketSubscriber subscriber;
        private readonly Forms.NotifyIcon tray;
        private HwndSource hwndSource;
        private IntPtr hwnd;
        private bool editMode;
        private bool preview;
        private bool bossHidden;
        private readonly bool demoAllowCapture;
        private int ignoredSpeechSegment = -1;
        internal bool IsClosing { get; private set; }
        private readonly LinkedList<SpeechBatch> aiQueue = new LinkedList<SpeechBatch>();
        private readonly List<ChatEntry> chatEntries = new List<ChatEntry>();
        private readonly List<ConversationMessage> conversationHistory = new List<ConversationMessage>();
        private bool aiBusy;
        private SpeechBatch collectingSpeechBatch;
        private SpeechBatch activeSpeechBatch;
        private bool aiHasVisibleOutput;
        private bool restartActiveBatch;
        private ChatEntry streamingAiEntry;
        private int lastFinalSegment = -1;
        private string lastFinalText = "";
        private CancellationTokenSource aiRequestCancellation;
        private DateTime configLastWrite = DateTime.MinValue;
        private int hoverMisses;
        private bool applyingGeometry;
        private bool manualResizing;
        private int manualResizeHit;
        private NativeMethods.NativePoint resizeStartCursor;
        private double resizeStartLeft;
        private double resizeStartTop;
        private double resizeStartWidth;
        private double resizeStartHeight;
        private readonly Queue<string> aiGlyphQueue = new Queue<string>();
        private readonly StringBuilder aiTypedText = new StringBuilder();
        private TaskCompletionSource<bool> aiTypingCompletion;
        private bool aiNetworkComplete;
        private int aiTypeTick;
        private bool controlMouseWasDown;
        private int controlPressedIndex = -1;
        private bool followLatest = true;
        private bool internalScrollChange;
        private bool rebuildingText;
        private string partialTranslation = "";
        private int partialTranslationSegment = -1;
        private int translationVersion;
        private CancellationTokenSource translationCancellation;

        internal OverlayWindow(OverlayConfig config, bool demoAllowCapture)
        {
            this.config = config;
            this.demoAllowCapture = demoAllowCapture;
            PhoneAiFeed.Configure(config.WebSocketUrl);
            PromptFeed.Configure(config.WebSocketUrl);
            Title = "系统声音实时字幕 Overlay";
            WindowStyle = WindowStyle.None;
            AllowsTransparency = true;
            Background = Brushes.Transparent;
            ShowInTaskbar = false;
            ShowActivated = false;
            Topmost = true;
            ResizeMode = ResizeMode.NoResize;
            MinWidth = 280;
            MinHeight = 72;
            MaxWidth = 2200;
            MaxHeight = 800;
            Focusable = false;

            background = new Border();
            background.CornerRadius = new CornerRadius(5);
            background.BorderThickness = new Thickness(1);
            background.BorderBrush = Brushes.Transparent;
            background.Background = Brushes.Transparent;
            background.Padding = new Thickness(34, 14, 34, 14);

            text = new TextBlock();
            text.FontFamily = new FontFamily("Microsoft YaHei UI");
            text.FontWeight = FontWeights.Normal;
            text.Foreground = new LinearGradientBrush(
                Color.FromRgb(255, 255, 255),
                Color.FromRgb(220, 234, 250),
                90);
            text.TextAlignment = TextAlignment.Left;
            text.TextWrapping = TextWrapping.Wrap;
            text.VerticalAlignment = VerticalAlignment.Top;
            text.Effect = new System.Windows.Media.Effects.DropShadowEffect
            {
                BlurRadius = 2,
                ShadowDepth = 1,
                Direction = 270,
                Opacity = 0.82,
                Color = Colors.Black
            };
            scroll = new ScrollViewer();
            scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Hidden;
            scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled;
            scroll.Focusable = false;
            scroll.CanContentScroll = false;
            scroll.ScrollChanged += delegate(object sender, ScrollChangedEventArgs args)
            {
                if (internalScrollChange || rebuildingText) return;
                bool next = scroll.ScrollableHeight <= 1
                    || scroll.VerticalOffset >= scroll.ScrollableHeight - 8;
                if (next != followLatest)
                {
                    followLatest = next;
                    AppLog.Write(string.Format(
                        "scroll follow_latest={0} offset={1:0.0} max={2:0.0}",
                        followLatest, scroll.VerticalOffset, scroll.ScrollableHeight));
                }
            };
            scroll.Content = text;
            background.Child = scroll;
            Grid chrome = new Grid();
            chrome.Children.Add(background);
            AddResizeHandles(chrome);
            Content = chrome;

            settings = new SettingsWindow(this);
            lockIndicator = new LockIndicatorWindow(this);
            fadeTimer = new System.Windows.Threading.DispatcherTimer();
            fadeTimer.Interval = TimeSpan.FromMilliseconds(config.FadeDelayMs);
            fadeTimer.Tick += delegate
            {
                fadeTimer.Stop();
                preview = false;
                RefreshText();
                if (!bossHidden)
                {
                    BeginAnimation(Window.OpacityProperty, null);
                    Opacity = config.Opacity;
                }
                AppLog.Write("preview_finished placeholder_restored=True");
            };
            aiTimer = new System.Windows.Threading.DispatcherTimer();
            aiTimer.Interval = TimeSpan.FromSeconds(config.AiSilenceSeconds);
            aiTimer.Tick += delegate
            {
                aiTimer.Stop();
                StartAiRequest();
            };
            configTimer = new System.Windows.Threading.DispatcherTimer();
            configTimer.Interval = TimeSpan.FromSeconds(1);
            configTimer.Tick += delegate { ReloadConfigIfChanged(); };
            hoverTimer = new System.Windows.Threading.DispatcherTimer();
            hoverTimer.Interval = TimeSpan.FromMilliseconds(50);
            hoverTimer.Tick += delegate { PollLockHover(); };
            geometrySaveTimer = new System.Windows.Threading.DispatcherTimer();
            geometrySaveTimer.Interval = TimeSpan.FromMilliseconds(450);
            geometrySaveTimer.Tick += delegate
            {
                geometrySaveTimer.Stop();
                SaveConfig();
                AppLog.Write(string.Format(
                    "geometry saved left={0:0} top={1:0} width={2:0} height={3:0}",
                    Left, Top, ActualWidth, ActualHeight));
            };
            aiTypewriterTimer = new System.Windows.Threading.DispatcherTimer();
            aiTypewriterTimer.Interval = TimeSpan.FromMilliseconds(35);
            aiTypewriterTimer.Tick += delegate { TypeNextAiCharacters(); };
            subscriber = new WebSocketSubscriber(
                config.WebSocketUrl,
                delegate(Dictionary<string, object> message)
                {
                    Dispatcher.BeginInvoke(new Action(delegate { HandleMessage(message); }));
                },
                delegate(string status)
                {
                    Dispatcher.BeginInvoke(new Action(delegate
                    {
                        AppLog.Write("websocket " + status);
                        tray.Text = Truncate("实时字幕 · " + status, 63);
                    }));
                });

            tray = new Forms.NotifyIcon();
            tray.Icon = Drawing.SystemIcons.Information;
            tray.Text = "系统声音实时字幕";
            tray.Visible = true;
            Forms.ContextMenuStrip menu = new Forms.ContextMenuStrip();
            menu.Items.Add("编辑位置与样式  (Ctrl+Alt+O / Ctrl+Shift+O)", null, delegate { Dispatcher.BeginInvoke(new Action(ToggleEditMode)); });
            menu.Items.Add("显示字幕预览", null, delegate { Dispatcher.BeginInvoke(new Action(ShowPreview)); });
            menu.Items.Add("暂停接收并隐藏/恢复字幕  (Ctrl+Alt+H / Ctrl+Shift+H)", null, delegate { Dispatcher.BeginInvoke(new Action(ToggleBossVisibility)); });
            menu.Items.Add(new Forms.ToolStripSeparator());
            menu.Items.Add("退出 Overlay", null, delegate { Dispatcher.BeginInvoke(new Action(RequestShutdown)); });
            tray.ContextMenuStrip = menu;
            tray.DoubleClick += delegate { Dispatcher.BeginInvoke(new Action(ToggleEditMode)); };

            SourceInitialized += OnSourceInitialized;
            Loaded += delegate
            {
                ApplySize(false);
                RestorePosition();
                RefreshText();
                Opacity = config.Opacity;
                subscriber.Start();
                configLastWrite = File.Exists(OverlayConfig.ConfigPath)
                    ? File.GetLastWriteTimeUtc(OverlayConfig.ConfigPath)
                    : DateTime.MinValue;
                configTimer.Start();
                hoverTimer.Start();
            };
            LocationChanged += delegate
            {
                if (editMode) PositionSettings();
                PositionLockIndicator();
            };
            SizeChanged += delegate
            {
                PositionLockIndicator();
                if (!applyingGeometry && IsLoaded && !config.Locked)
                {
                    config.Width = ActualWidth;
                    config.Height = ActualHeight;
                    geometrySaveTimer.Stop();
                    geometrySaveTimer.Start();
                }
            };
            PreviewMouseLeftButtonDown += delegate(object sender, MouseButtonEventArgs args)
            {
                if (editMode || !config.Locked)
                {
                    int resizeHit = !editMode
                        ? NativeMethods.ResizeHitAtCursor(hwnd, 12)
                        : 0;
                    if (resizeHit != 0)
                    {
                        args.Handled = true;
                        StartManualResize(resizeHit);
                    }
                    else
                    {
                        AppLog.Write("drag_move begin");
                        try { DragMove(); }
                        catch (Exception error) { AppLog.Write("drag_move error=" + error.Message); }
                    }
                    RememberPosition();
                    SaveConfig();
                    AppLog.Write(string.Format("drag_move end left={0:0} top={1:0}", Left, Top));
                }
            };
            MouseMove += delegate(object sender, MouseEventArgs args)
            {
                if (manualResizing)
                {
                    if (args.LeftButton == MouseButtonState.Pressed) UpdateManualResize();
                    else FinishManualResize();
                    args.Handled = true;
                }
                else if (!config.Locked && !editMode)
                {
                    UpdateResizeCursor(NativeMethods.ResizeHitAtCursor(hwnd, 12));
                }
                else Cursor = Cursors.Arrow;
            };
            MouseLeftButtonUp += delegate
            {
                if (manualResizing) FinishManualResize();
            };
            MouseLeave += delegate { if (!manualResizing) Cursor = Cursors.Arrow; };
            KeyDown += delegate(object sender, KeyEventArgs args)
            {
                if (editMode && args.Key == Key.Escape) SetEditMode(false);
            };
        }

        private void AddResizeHandles(Grid chrome)
        {
            AddResizeHandle(chrome, 12, double.NaN, HorizontalAlignment.Left, VerticalAlignment.Stretch, Cursors.SizeWE, NativeMethods.HTLEFT);
            AddResizeHandle(chrome, 12, double.NaN, HorizontalAlignment.Right, VerticalAlignment.Stretch, Cursors.SizeWE, NativeMethods.HTRIGHT);
            AddResizeHandle(chrome, double.NaN, 12, HorizontalAlignment.Stretch, VerticalAlignment.Top, Cursors.SizeNS, NativeMethods.HTTOP);
            AddResizeHandle(chrome, double.NaN, 12, HorizontalAlignment.Stretch, VerticalAlignment.Bottom, Cursors.SizeNS, NativeMethods.HTBOTTOM);
            AddResizeHandle(chrome, 18, 18, HorizontalAlignment.Left, VerticalAlignment.Top, Cursors.SizeNWSE, NativeMethods.HTTOPLEFT);
            AddResizeHandle(chrome, 18, 18, HorizontalAlignment.Right, VerticalAlignment.Top, Cursors.SizeNESW, NativeMethods.HTTOPRIGHT);
            AddResizeHandle(chrome, 18, 18, HorizontalAlignment.Left, VerticalAlignment.Bottom, Cursors.SizeNESW, NativeMethods.HTBOTTOMLEFT);
            AddResizeHandle(chrome, 18, 18, HorizontalAlignment.Right, VerticalAlignment.Bottom, Cursors.SizeNWSE, NativeMethods.HTBOTTOMRIGHT);
        }

        private void AddResizeHandle(
            Grid chrome,
            double width,
            double height,
            HorizontalAlignment horizontal,
            VerticalAlignment vertical,
            Cursor cursor,
            int hit)
        {
            Border handle = new Border();
            handle.Background = new SolidColorBrush(Color.FromArgb(1, 255, 255, 255));
            if (!double.IsNaN(width)) handle.Width = width;
            if (!double.IsNaN(height)) handle.Height = height;
            handle.HorizontalAlignment = horizontal;
            handle.VerticalAlignment = vertical;
            handle.Cursor = cursor;
            Panel.SetZIndex(handle, 10);
            handle.PreviewMouseLeftButtonDown += delegate(object sender, MouseButtonEventArgs args)
            {
                if (config.Locked || editMode) return;
                args.Handled = true;
                StartManualResize(hit);
            };
            chrome.Children.Add(handle);
        }

        private void OnSourceInitialized(object sender, EventArgs e)
        {
            hwnd = new WindowInteropHelper(this).Handle;
            hwndSource = HwndSource.FromHwnd(hwnd);
            hwndSource.AddHook(WindowProc);
            NativeMethods.SetNormalInteraction(hwnd, config.Locked);
            ApplyCaptureProtection(hwnd);
            bool hotkey = NativeMethods.RegisterHotKey(
                hwnd,
                NativeMethods.HOTKEY_ID,
                NativeMethods.MOD_CONTROL | NativeMethods.MOD_ALT | NativeMethods.MOD_NOREPEAT,
                (uint)KeyInterop.VirtualKeyFromKey(Key.O));
            string hotkeyCombo = "Ctrl+Alt+O";
            if (!hotkey)
            {
                hotkey = NativeMethods.RegisterHotKey(
                    hwnd,
                    NativeMethods.HOTKEY_ID,
                    NativeMethods.MOD_CONTROL | NativeMethods.MOD_SHIFT | NativeMethods.MOD_NOREPEAT,
                    (uint)KeyInterop.VirtualKeyFromKey(Key.O));
                hotkeyCombo = "Ctrl+Shift+O";
            }
            bool bossHotkey = NativeMethods.RegisterHotKey(
                hwnd,
                NativeMethods.BOSS_HOTKEY_ID,
                NativeMethods.MOD_CONTROL | NativeMethods.MOD_ALT | NativeMethods.MOD_NOREPEAT,
                (uint)KeyInterop.VirtualKeyFromKey(Key.H));
            string bossHotkeyCombo = "Ctrl+Alt+H";
            if (!bossHotkey)
            {
                bossHotkey = NativeMethods.RegisterHotKey(
                    hwnd,
                    NativeMethods.BOSS_HOTKEY_ID,
                    NativeMethods.MOD_CONTROL | NativeMethods.MOD_SHIFT | NativeMethods.MOD_NOREPEAT,
                    (uint)KeyInterop.VirtualKeyFromKey(Key.H));
                bossHotkeyCombo = "Ctrl+Shift+H";
            }
            long style = NativeMethods.GetExtendedStyle(hwnd);
            AppLog.Write(string.Format(
                "window hwnd=0x{0:X} text_only=True hotkey={1} hotkey_combo={2} locked={3} click_through={4} no_activate={5}",
                hwnd.ToInt64(),
                hotkey,
                hotkeyCombo,
                config.Locked,
                (style & NativeMethods.WS_EX_TRANSPARENT) != 0,
                (style & NativeMethods.WS_EX_NOACTIVATE) != 0));
            AppLog.Write(string.Format(
                "boss_hotkey={0} boss_hotkey_combo={1}", bossHotkey, bossHotkeyCombo));

            // 字幕窗位置微调：Ctrl+Alt+↑/↓ 上下移动（冲突时回退 Ctrl+Shift+↑/↓）。
            string moveModifier = "Ctrl+Alt";
            bool upHotkey = NativeMethods.RegisterHotKey(
                hwnd,
                NativeMethods.MOVE_UP_HOTKEY_ID,
                NativeMethods.MOD_CONTROL | NativeMethods.MOD_ALT | NativeMethods.MOD_NOREPEAT,
                (uint)KeyInterop.VirtualKeyFromKey(Key.Up));
            if (!upHotkey)
            {
                upHotkey = NativeMethods.RegisterHotKey(
                    hwnd,
                    NativeMethods.MOVE_UP_HOTKEY_ID,
                    NativeMethods.MOD_CONTROL | NativeMethods.MOD_SHIFT | NativeMethods.MOD_NOREPEAT,
                    (uint)KeyInterop.VirtualKeyFromKey(Key.Up));
                moveModifier = "Ctrl+Shift";
            }
            bool downHotkey = NativeMethods.RegisterHotKey(
                hwnd,
                NativeMethods.MOVE_DOWN_HOTKEY_ID,
                NativeMethods.MOD_CONTROL | NativeMethods.MOD_ALT | NativeMethods.MOD_NOREPEAT,
                (uint)KeyInterop.VirtualKeyFromKey(Key.Down));
            if (!downHotkey)
            {
                downHotkey = NativeMethods.RegisterHotKey(
                    hwnd,
                    NativeMethods.MOVE_DOWN_HOTKEY_ID,
                    NativeMethods.MOD_CONTROL | NativeMethods.MOD_SHIFT | NativeMethods.MOD_NOREPEAT,
                    (uint)KeyInterop.VirtualKeyFromKey(Key.Down));
            }
            AppLog.Write(string.Format(
                "move_hotkeys up={0} down={1} combo={2}+Up/Down", upHotkey, downHotkey, moveModifier));

            // 左右移动：与上下相同修饰键（同一回退组），Ctrl+Alt+Left/Right。
            bool leftHotkey = NativeMethods.RegisterHotKey(
                hwnd,
                NativeMethods.MOVE_LEFT_HOTKEY_ID,
                moveModifier == "Ctrl+Alt"
                    ? NativeMethods.MOD_CONTROL | NativeMethods.MOD_ALT | NativeMethods.MOD_NOREPEAT
                    : NativeMethods.MOD_CONTROL | NativeMethods.MOD_SHIFT | NativeMethods.MOD_NOREPEAT,
                (uint)KeyInterop.VirtualKeyFromKey(Key.Left));
            bool rightHotkey = NativeMethods.RegisterHotKey(
                hwnd,
                NativeMethods.MOVE_RIGHT_HOTKEY_ID,
                moveModifier == "Ctrl+Alt"
                    ? NativeMethods.MOD_CONTROL | NativeMethods.MOD_ALT | NativeMethods.MOD_NOREPEAT
                    : NativeMethods.MOD_CONTROL | NativeMethods.MOD_SHIFT | NativeMethods.MOD_NOREPEAT,
                (uint)KeyInterop.VirtualKeyFromKey(Key.Right));
            AppLog.Write(string.Format(
                "move_hotkeys_lr left={0} right={1} combo={2}+Left/Right", leftHotkey, rightHotkey, moveModifier));

            // 截图解题：优先 Ctrl+Shift+C，冲突时回退 Ctrl+Alt+C。
            bool solveHotkey = NativeMethods.RegisterHotKey(
                hwnd,
                NativeMethods.SOLVE_HOTKEY_ID,
                NativeMethods.MOD_CONTROL | NativeMethods.MOD_SHIFT | NativeMethods.MOD_NOREPEAT,
                (uint)KeyInterop.VirtualKeyFromKey(Key.C));
            string solveCombo = "Ctrl+Shift+C";
            if (!solveHotkey)
            {
                solveHotkey = NativeMethods.RegisterHotKey(
                    hwnd,
                    NativeMethods.SOLVE_HOTKEY_ID,
                    NativeMethods.MOD_CONTROL | NativeMethods.MOD_ALT | NativeMethods.MOD_NOREPEAT,
                    (uint)KeyInterop.VirtualKeyFromKey(Key.C));
                solveCombo = "Ctrl+Alt+C";
            }
            AppLog.Write(string.Format(
                "solve_hotkey={0} solve_combo={1}", solveHotkey, solveCombo));
        }

        private void NudgeWindowPosition(double deltaX, double deltaY)
        {
            if (double.IsNaN(Left) || double.IsNaN(Top)) return;
            if (deltaX != 0) Left = Left + deltaX;
            if (deltaY != 0) Top = Top + deltaY;
            RememberPosition();
            SaveConfig();
            AppLog.Write(string.Format(
                "nudge_position left={0:F0} top={1:F0} delta=({2:+0;-0}, {3:+0;-0})",
                Left, Top, deltaX, deltaY));
        }

        private IntPtr WindowProc(IntPtr source, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
        {
            if (message == NativeMethods.WM_HOTKEY && wParam.ToInt32() == NativeMethods.HOTKEY_ID)
            {
                ToggleEditMode();
                handled = true;
            }
            else if (message == NativeMethods.WM_HOTKEY
                && wParam.ToInt32() == NativeMethods.BOSS_HOTKEY_ID)
            {
                ToggleBossVisibility();
                handled = true;
            }
            else if (message == NativeMethods.WM_HOTKEY
                && wParam.ToInt32() == NativeMethods.MOVE_UP_HOTKEY_ID)
            {
                NudgeWindowPosition(0, -30);
                handled = true;
            }
            else if (message == NativeMethods.WM_HOTKEY
                && wParam.ToInt32() == NativeMethods.MOVE_DOWN_HOTKEY_ID)
            {
                NudgeWindowPosition(0, 30);
                handled = true;
            }
            else if (message == NativeMethods.WM_HOTKEY
                && wParam.ToInt32() == NativeMethods.MOVE_LEFT_HOTKEY_ID)
            {
                NudgeWindowPosition(-30, 0);
                handled = true;
            }
            else if (message == NativeMethods.WM_HOTKEY
                && wParam.ToInt32() == NativeMethods.MOVE_RIGHT_HOTKEY_ID)
            {
                NudgeWindowPosition(30, 0);
                handled = true;
            }
            else if (message == NativeMethods.WM_HOTKEY
                && wParam.ToInt32() == NativeMethods.SOLVE_HOTKEY_ID)
            {
                RequestSolve();
                handled = true;
            }
            else if (message == 0x8030)
            {
                RequestShutdown();
                handled = true;
            }
            return IntPtr.Zero;
        }

        private void HandleMessage(Dictionary<string, object> message)
        {
            string type = message.ContainsKey("type") ? Convert.ToString(message["type"]) : "";
            if (type == "partial" || type == "final")
            {
                string fullText = message.ContainsKey("text") ? Convert.ToString(message["text"]) : "";
                int segment = message.ContainsKey("segment_id")
                    ? Convert.ToInt32(message["segment_id"])
                    : -1;
                if (bossHidden)
                {
                    if (type == "partial") ignoredSpeechSegment = segment;
                    else if (type == "final" && ignoredSpeechSegment == segment)
                        ignoredSpeechSegment = -1;
                    AppLog.Write(string.Format(
                        "speech ignored hidden=True type={0} segment={1}", type, segment));
                    return;
                }
                if (ignoredSpeechSegment >= 0 && segment == ignoredSpeechSegment)
                {
                    if (type == "final") ignoredSpeechSegment = -1;
                    AppLog.Write(string.Format(
                        "speech ignored hidden_tail=True type={0} segment={1}", type, segment));
                    return;
                }
                string loggedText = fullText;
                if (loggedText.Length > 120) loggedText = loggedText.Substring(0, 120);
                AppLog.Write(type + " text=" + loggedText);
                if (config.LiveTranslateEnabled && ContainsEnglishText(fullText))
                    ScheduleLocalTranslation(fullText, segment, type == "final");
                bool changed = subtitle.Apply(message);
                if (type == "partial" && !aiBusy && aiQueue.Count > 0)
                {
                    aiTimer.Stop();
                    AppLog.Write("ai batch debounce paused new_partial=True queued=" + aiQueue.Count);
                }
                bool newFinal = type == "final"
                    && !string.IsNullOrWhiteSpace(fullText)
                    && (segment != lastFinalSegment || fullText != lastFinalText);
                if (newFinal)
                {
                    lastFinalSegment = segment;
                    lastFinalText = fullText;
                    bool useAi = config.AiEnabled && SecretStore.HasApiKey;
                    if (useAi && aiBusy && !aiHasVisibleOutput && activeSpeechBatch != null)
                    {
                        activeSpeechBatch.Append(fullText);
                        restartActiveBatch = true;
                        if (streamingAiEntry != null) chatEntries.Remove(streamingAiEntry);
                        streamingAiEntry = null;
                        CancelAiRequest();
                        AppLog.Write("ai active batch extended segments="
                            + activeSpeechBatch.Segments.Count + " request_restarted=True");
                    }
                    else if (useAi && collectingSpeechBatch != null)
                    {
                        collectingSpeechBatch.Append(fullText);
                        AppLog.Write("ai pending speech merged segments="
                            + collectingSpeechBatch.Segments.Count);
                    }
                    else
                    {
                        ChatEntry voiceEntry = new ChatEntry
                        {
                            Role = "user",
                            Text = fullText,
                            Streaming = false,
                            SegmentId = segment
                        };
                        chatEntries.Add(voiceEntry);
                        TrimChatEntries();
                        if (useAi)
                        {
                            collectingSpeechBatch = new SpeechBatch { Entry = voiceEntry };
                            collectingSpeechBatch.Append(fullText);
                            aiQueue.AddLast(collectingSpeechBatch);
                        }
                    }
                    if (useAi && !aiBusy)
                    {
                        aiTimer.Stop();
                        aiTimer.Interval = TimeSpan.FromSeconds(config.AiSilenceSeconds);
                        aiTimer.Start();
                        AppLog.Write("ai batch queued_batches=" + aiQueue.Count
                            + " current_segments="
                            + (collectingSpeechBatch == null ? 0 : collectingSpeechBatch.Segments.Count)
                            + " delay_seconds=" + config.AiSilenceSeconds);
                    }
                }
                if (changed || newFinal)
                {
                    preview = false;
                    RefreshText();
                }
                if (message.ContainsKey("text") && Convert.ToString(message["text"]).Length > 0)
                    ShowForSpeech();
            }
            else if (type == "error")
            {
                // 失败一律不弹窗，只记日志（overlay.runtime.log 可查）
                AppLog.Write("service error: " + Convert.ToString(message["message"]));
            }
            else if (type == "solve_answer")
            {
                string solveText = message.ContainsKey("text") ? Convert.ToString(message["text"]) : "";
                bool solveDone = message.ContainsKey("done") && Convert.ToBoolean(message["done"]);
                ShowSolveAnswer(solveText, solveDone);
            }
        }

        private ChatEntry solveEntry;

        private void ShowSolveAnswer(string chunk, bool done)
        {
            Action update = delegate
            {
                if (string.IsNullOrEmpty(chunk) && done)
                {
                    if (solveEntry != null) solveEntry.Streaming = false;
                    RefreshText();
                    return;
                }
                if (solveEntry == null || solveEntry.Streaming == false)
                {
                    solveEntry = new ChatEntry
                    {
                        Role = "assistant",
                        Text = "",
                        Streaming = true,
                        SegmentId = -2
                    };
                    chatEntries.Add(solveEntry);
                    TrimChatEntries();
                }
                solveEntry.Text = chunk;
                solveEntry.Streaming = !done;
                RefreshText();
                FadeTo(config.Opacity, 80);
                AppLog.Write("solve_answer len=" + (chunk ?? "").Length + " done=" + done);
            };
            if (Dispatcher.CheckAccess()) update(); else Dispatcher.Invoke(update);
        }

        internal void RequestSolve()
        {
            try
            {
                Uri uri = new Uri(config.WebSocketUrl
                    .Replace("wss://", "https://")
                    .Replace("ws://", "http://"));
                string endpoint = uri.GetLeftPart(UriPartial.Authority) + "/api/phone/solve";
                new Thread(delegate()
                {
                    try
                    {
                        byte[] empty = Encoding.UTF8.GetBytes("{}");
                        HttpWebRequest request = (HttpWebRequest)WebRequest.Create(endpoint);
                        request.Method = "POST";
                        request.ContentType = "application/json";
                        request.ContentLength = empty.Length;
                        request.Timeout = 5000;
                        using (Stream stream = request.GetRequestStream()) stream.Write(empty, 0, empty.Length);
                        using (HttpWebResponse response = (HttpWebResponse)request.GetResponse()) { }
                        AppLog.Write("solve requested via hotkey");
                    }
                    catch (Exception error)
                    {
                        // 失败不弹窗，只记日志
                        AppLog.Write("solve request failed: " + error.Message);
                    }
                })
                { IsBackground = true }.Start();
            }
            catch (Exception error)
            {
                AppLog.Write("solve request error: " + error.Message);
            }
        }

        // 字幕窗是纯文本渲染，模型输出的 Markdown 标记（**、#、`）会原样露出，显示前剥掉。
        internal static string CleanAiText(string input)
        {
            if (string.IsNullOrEmpty(input)) return input ?? "";
            string[] lines = input.Replace("\r\n", "\n").Split('\n');
            StringBuilder cleaned = new StringBuilder();
            for (int i = 0; i < lines.Length; i++)
            {
                string line = lines[i].TrimStart();
                int hash = 0;
                while (hash < line.Length && line[hash] == '#') hash++;
                if (hash > 0 && hash < line.Length && line[hash] == ' ') line = line.Substring(hash + 1);
                if (line.TrimStart().StartsWith("```")) continue;
                cleaned.Append(line.Replace("**", "").Replace("__", "").Replace("`", ""));
                if (i < lines.Length - 1) cleaned.Append('\n');
            }
            return cleaned.ToString();
        }

        private void RefreshText()
        {
            double previousOffset = scroll.VerticalOffset;
            bool keepFollowing = followLatest;
            rebuildingText = true;
            text.Inlines.Clear();
            if (preview)
            {
                text.Inlines.Add(new Run("实时字幕预览 · 拖动字幕框调整位置"));
            }
            else
            {
                if (chatEntries.Count == 0 && subtitle.Partial.Length == 0)
                {
                    Run waiting = new Run("等待字幕…");
                    waiting.Foreground = new SolidColorBrush(Color.FromArgb(150, 175, 190, 208));
                    waiting.FontWeight = FontWeights.Normal;
                    text.Inlines.Add(waiting);
                }
                for (int index = 0; index < chatEntries.Count; index++)
                {
                    ChatEntry entry = chatEntries[index];
                    bool user = entry.Role == "user";
                    if (!user)
                    {
                        bool translated = entry.Role == "translation";
                        Run label = new Run(translated ? "译文  " : "AI    ");
                        label.Foreground = new SolidColorBrush(Color.FromRgb(116, 232, 255));
                        label.FontWeight = FontWeights.SemiBold;
                        text.Inlines.Add(label);
                    }
                    Run content = new Run((user ? entry.Text : CleanAiText(entry.Text)) + (entry.Streaming ? " ▍" : ""));
                    content.Foreground = user
                        ? BrushFromHex(config.TextColor, 245)
                        : new SolidColorBrush(Color.FromRgb(116, 232, 255));
                    content.FontWeight = FontWeights.Normal;
                    text.Inlines.Add(content);
                    if (index < chatEntries.Count - 1 || subtitle.Partial.Length > 0)
                        text.Inlines.Add(new LineBreak());
                }
                if (subtitle.Partial.Length > 0)
                {
                    Run partialRun = new Run(subtitle.Partial + " ▍");
                    partialRun.Foreground = BrushFromHex(config.TextColor, 255);
                    partialRun.FontWeight = FontWeights.Normal;
                    text.Inlines.Add(partialRun);
                    if (partialTranslation.Length > 0
                        && partialTranslationSegment == subtitle.PartialSegment)
                    {
                        text.Inlines.Add(new LineBreak());
                        Run translatedLabel = new Run("译文  ");
                        translatedLabel.Foreground = new SolidColorBrush(Color.FromRgb(116, 232, 255));
                        translatedLabel.FontWeight = FontWeights.SemiBold;
                        text.Inlines.Add(translatedLabel);
                        Run translatedPartial = new Run(partialTranslation + " ▍");
                        translatedPartial.Foreground = new SolidColorBrush(Color.FromRgb(116, 232, 255));
                        translatedPartial.FontWeight = FontWeights.Normal;
                        text.Inlines.Add(translatedPartial);
                    }
                }
            }
            rebuildingText = false;
            Dispatcher.BeginInvoke(new Action(delegate
            {
                internalScrollChange = true;
                try
                {
                    scroll.UpdateLayout();
                    if (keepFollowing) scroll.ScrollToEnd();
                    else scroll.ScrollToVerticalOffset(
                        Math.Max(0, Math.Min(previousOffset, scroll.ScrollableHeight)));
                }
                finally { internalScrollChange = false; }
            }), System.Windows.Threading.DispatcherPriority.Loaded);
        }

        private void TrimChatEntries()
        {
            while (chatEntries.Count > 30) chatEntries.RemoveAt(0);
        }

        private static bool ContainsEnglishText(string value)
        {
            int letters = 0;
            foreach (char item in value ?? "")
            {
                if ((item >= 'A' && item <= 'Z') || (item >= 'a' && item <= 'z'))
                {
                    letters++;
                    if (letters >= 2) return true;
                }
            }
            return false;
        }

        private void CancelLocalTranslation(bool clearPartial)
        {
            translationVersion++;
            CancellationTokenSource cancellation = translationCancellation;
            translationCancellation = null;
            if (cancellation != null)
            {
                try { cancellation.Cancel(); } catch { }
                cancellation.Dispose();
            }
            if (clearPartial)
            {
                partialTranslation = "";
                partialTranslationSegment = -1;
            }
        }

        private async void ScheduleLocalTranslation(string source, int segment, bool isFinal)
        {
            CancelLocalTranslation(false);
            int version = translationVersion;
            CancellationTokenSource cancellation = new CancellationTokenSource();
            translationCancellation = cancellation;
            if (isFinal)
            {
                partialTranslation = "";
                partialTranslationSegment = -1;
            }
            try
            {
                if (!isFinal) await Task.Delay(100, cancellation.Token);
                string translated = await LocalTranslationClient.TranslateAsync(
                    config.WebSocketUrl, source, cancellation.Token);
                if (cancellation.IsCancellationRequested || version != translationVersion
                    || !config.LiveTranslateEnabled || string.IsNullOrWhiteSpace(translated)) return;
                if (isFinal)
                {
                    for (int index = chatEntries.Count - 1; index >= 0; index--)
                    {
                        if (chatEntries[index].Role == "translation"
                            && chatEntries[index].SegmentId == segment)
                            chatEntries.RemoveAt(index);
                    }
                    ChatEntry translatedEntry = new ChatEntry
                    {
                        Role = "translation",
                        Text = translated,
                        Streaming = false,
                        SegmentId = segment
                    };
                    int sourceIndex = chatEntries.FindLastIndex(delegate(ChatEntry entry)
                    {
                        return entry.Role == "user" && entry.SegmentId == segment;
                    });
                    if (sourceIndex >= 0) chatEntries.Insert(sourceIndex + 1, translatedEntry);
                    else chatEntries.Add(translatedEntry);
                    TrimChatEntries();
                }
                else
                {
                    partialTranslation = translated;
                    partialTranslationSegment = segment;
                }
                RefreshText();
                ShowForSpeech();
                AppLog.Write(string.Format(
                    "local translation final={0} segment={1} source_chars={2} target_chars={3}",
                    isFinal, segment, source.Length, translated.Length));
            }
            catch (OperationCanceledException) { }
            catch (Exception error)
            {
                if (version == translationVersion)
                    AppLog.Write("local translation error=" + error.Message);
            }
            finally
            {
                if (translationCancellation == cancellation)
                    translationCancellation = null;
                cancellation.Dispose();
            }
        }

        private void ShowForSpeech()
        {
            FadeTo(config.Opacity, 170);
            fadeTimer.Stop();
        }

        private static SolidColorBrush BrushFromHex(string value, byte alpha)
        {
            try
            {
                Color color = (Color)ColorConverter.ConvertFromString(value);
                color.A = alpha;
                return new SolidColorBrush(color);
            }
            catch { return new SolidColorBrush(Color.FromArgb(alpha, 255, 255, 255)); }
        }

        private void FadeTo(double target, int milliseconds)
        {
            if (editMode && target == 0) return;
            if (bossHidden && target > 0) return;
            DoubleAnimation animation = new DoubleAnimation();
            animation.From = Opacity;
            animation.To = target;
            animation.Duration = TimeSpan.FromMilliseconds(milliseconds);
            animation.EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut };
            BeginAnimation(Window.OpacityProperty, animation, HandoffBehavior.SnapshotAndReplace);
        }

        internal void ToggleEditMode() { SetEditMode(!editMode); }

        internal void SetEditMode(bool enabled)
        {
            if (editMode == enabled) return;
            editMode = enabled;
            AppLog.Write("edit_mode=" + enabled);
            fadeTimer.Stop();
            BeginAnimation(Window.OpacityProperty, null);
            if (enabled) NativeMethods.SetInteractive(hwnd, true);
            else NativeMethods.SetNormalInteraction(hwnd, config.Locked);
            if (enabled)
            {
                bossHidden = false;
                lockIndicator.Hide();
                Focusable = true;
                ShowActivated = true;
                ShowInTaskbar = true;
                preview = true;
                RefreshText();
                Opacity = config.Opacity;
                background.Background = new SolidColorBrush(Color.FromArgb(38, 10, 17, 28));
                background.BorderBrush = new SolidColorBrush(Color.FromArgb(190, 96, 165, 250));
                settings.Sync(config);
                PositionSettings();
                if (settings.Owner == null) settings.Owner = this;
                settings.Show();
                settings.Activate();
            }
            else
            {
                settings.Hide();
                Focusable = false;
                ShowActivated = false;
                ShowInTaskbar = false;
                preview = false;
                RefreshText();
                background.Background = Brushes.Transparent;
                background.BorderBrush = Brushes.Transparent;
                SetResizeFrame(false);
                RememberPosition();
                SaveConfig();
                NativeMethods.SetNormalInteraction(hwnd, config.Locked);
                if (!bossHidden) Opacity = config.Opacity;
            }
            // ShowInTaskbar 切换可能重建 HWND；刷新句柄并把防截屏属性重新挂上。
            hwnd = new WindowInteropHelper(this).Handle;
            ApplyCaptureProtection(hwnd);
        }

        internal void ShowPreview()
        {
            bossHidden = false;
            preview = true;
            RefreshText();
            FadeTo(config.Opacity, 170);
            if (!editMode)
            {
                fadeTimer.Stop();
                fadeTimer.Interval = TimeSpan.FromSeconds(3);
                fadeTimer.Start();
            }
        }

        internal void ResetConversation()
        {
            CancelLocalTranslation(true);
            aiTimer.Stop();
            CancelAiRequest();
            aiQueue.Clear();
            collectingSpeechBatch = null;
            activeSpeechBatch = null;
            restartActiveBatch = false;
            aiBusy = false;
            streamingAiEntry = null;
            PhoneAiFeed.Post("", true);
            conversationHistory.Clear();
            chatEntries.Clear();
            subtitle.Clear();
            lastFinalSegment = -1;
            lastFinalText = "";
            followLatest = true;
            preview = false;
            RefreshText();
            lockIndicator.Hide();
            SetResizeFrame(false);
            if (!editMode)
            {
                BeginAnimation(Window.OpacityProperty, null);
                if (!bossHidden) Opacity = config.Opacity;
            }
            AppLog.Write("conversation_reset context_messages=0 system_preserved=True");
        }

        internal void ToggleBossVisibility()
        {
            bossHidden = !bossHidden;
            BeginAnimation(Window.OpacityProperty, null);
            if (bossHidden)
            {
                CancelLocalTranslation(true);
                if (subtitle.PartialSegment >= 0)
                    ignoredSpeechSegment = subtitle.PartialSegment;
                subtitle.DiscardPartial();
                if (editMode) SetEditMode(false);
                lockIndicator.Hide();
                SetResizeFrame(false);
                Opacity = 0;
            }
            else
            {
                RefreshText();
                Opacity = config.Opacity;
            }
            AppLog.Write(string.Format(
                "boss_hidden={0} speech_accepting={1} ignored_segment={2}",
                bossHidden, !bossHidden, ignoredSpeechSegment));
        }

        internal void RequestShutdown()
        {
            if (IsClosing) return;
            IsClosing = true;
            settings.CloseForShutdown();
            Application.Current.Shutdown();
        }

        internal void TogglePositionLock() { SetPositionLocked(!config.Locked); }
        internal OverlayConfig CurrentConfig { get { return config; } }

        internal void SetPositionLocked(bool locked)
        {
            config.Locked = locked;
            if (!editMode && hwnd != IntPtr.Zero)
                NativeMethods.SetNormalInteraction(hwnd, config.Locked);
            if (config.Locked) SetResizeFrame(false);
            lockIndicator.UpdateState(config.Locked);
            SaveConfig();
            AppLog.Write("position_locked=" + config.Locked);
        }

        internal bool ApplyCaptureProtection(IntPtr target)
        {
            bool active = config.CaptureInvisible && !demoAllowCapture;
            bool applied = NativeMethods.ApplyExcludeFromCapture(target, active);
            AppLog.Write(string.Format(
                "capture_protection={0} applied={1} demo_allow_capture={2} hwnd=0x{3:X}",
                active, applied, demoAllowCapture, target.ToInt64()));
            return applied;
        }

        internal void SetCaptureInvisible(bool invisible)
        {
            if (config.CaptureInvisible == invisible) return;
            config.CaptureInvisible = invisible;
            ApplyCaptureProtection(hwnd);
            if (settings.NativeHandle != IntPtr.Zero)
                ApplyCaptureProtection(settings.NativeHandle);
            if (lockIndicator.NativeHandle != IntPtr.Zero)
                ApplyCaptureProtection(lockIndicator.NativeHandle);
            SaveConfig();
        }

        private void PollLockHover()
        {
            if (bossHidden || editMode)
            {
                controlMouseWasDown = NativeMethods.GetAsyncKeyState(0x01) < 0;
                controlPressedIndex = -1;
                hoverMisses = 0;
                if (lockIndicator.IsVisible) lockIndicator.Hide();
                if (!editMode) SetResizeFrame(false);
                return;
            }
            bool inside = NativeMethods.CursorInside(hwnd, 4)
                || (lockIndicator.IsVisible
                    && NativeMethods.CursorInside(lockIndicator.NativeHandle, 8));
            if (inside)
            {
                hoverMisses = 0;
                SetResizeFrame(!config.Locked);
                lockIndicator.UpdateState(config.Locked);
                PositionLockIndicator();
                if (!lockIndicator.IsVisible)
                {
                    if (lockIndicator.Owner == null) lockIndicator.Owner = this;
                    lockIndicator.Show();
                }
                int hoveredControl = lockIndicator.UpdateHoverFromCursor();
                bool mouseDown = NativeMethods.GetAsyncKeyState(0x01) < 0;
                if (mouseDown && !controlMouseWasDown)
                    controlPressedIndex = hoveredControl;
                else if (!mouseDown && controlMouseWasDown)
                {
                    if (controlPressedIndex >= 0 && controlPressedIndex == hoveredControl)
                        lockIndicator.ActivateControl(hoveredControl);
                    controlPressedIndex = -1;
                }
                controlMouseWasDown = mouseDown;
            }
            else if (lockIndicator.IsVisible && ++hoverMisses >= 5)
            {
                lockIndicator.Hide();
                SetResizeFrame(false);
                hoverMisses = 0;
            }
            else if (!inside && !lockIndicator.IsVisible)
            {
                SetResizeFrame(false);
            }
        }

        private void SetResizeFrame(bool visible)
        {
            if (editMode) return;
            bool show = !bossHidden && (visible || config.FrameMode == "always");
            if (show)
            {
                // Layered windows do not receive mouse input on fully transparent pixels.
                // Alpha 1 is visually invisible but keeps the unlocked frame draggable.
                background.Background = new SolidColorBrush(Color.FromArgb(1, 0, 0, 0));
                byte alpha = (byte)Math.Round(config.FrameOpacity * 255);
                background.BorderBrush = BrushFromHex(config.FrameColor, alpha);
            }
            else
            {
                background.Background = Brushes.Transparent;
                background.BorderBrush = Brushes.Transparent;
            }
        }

        private void PositionLockIndicator()
        {
            if (double.IsNaN(Left) || double.IsNaN(Top)) return;
            lockIndicator.Left = Left + ActualWidth - lockIndicator.Width - 8;
            lockIndicator.Top = Top - lockIndicator.Height + 9;
        }

        internal void ShowAiSettingsTab() { settings.SelectAiTab(); }

        internal void SetOverlayWidth(double value)
        {
            config.Width = value;
            ApplySize(true);
            PositionSettings();
        }

        internal void SetFontSize(double value)
        {
            config.FontSize = value;
            ApplySize(true);
            PositionSettings();
        }

        internal double CurrentFontSize { get { return config.FontSize; } }

        internal void SetFontFamily(string value)
        {
            config.FontFamilyName = value;
            ApplySize(true);
            RefreshText();
            SaveConfig();
        }

        internal void SetTextColor(string value)
        {
            config.TextColor = value;
            text.Foreground = BrushFromHex(value, 255);
            RefreshText();
            SaveConfig();
        }

        internal void SetOverlayOpacity(double value)
        {
            config.Opacity = value;
            if (!bossHidden)
            {
                BeginAnimation(Window.OpacityProperty, null);
                Opacity = config.Opacity;
            }
        }

        internal void SetMaxLines(int value)
        {
            config.MaxLines = value;
            ApplySize(true);
            PositionSettings();
            SaveConfig();
        }

        internal void SetLiveTranslationEnabled(bool enabled)
        {
            if (config.LiveTranslateEnabled == enabled) return;
            config.LiveTranslateEnabled = enabled;
            if (enabled) config.AsrLanguage = "en";
            if (!enabled) CancelLocalTranslation(true);
            RefreshText();
            SaveConfig();
            AppLog.Write("live_translate_enabled=" + enabled);
        }

        internal void ApplyAiSettings(
            bool enabled, string model, string mode, string baseUrl, double silenceSeconds, string systemPrompt,
            string overridePrompt, string resumeContext, string jdContext, string targetCompany, string extraContext,
            bool visionEnabled, string visionBaseUrl, string visionModel, string solvePrompt)
        {
            config.AiEnabled = enabled;
            config.AiModel = model;
            config.AiMode = mode;
            config.AiBaseUrl = baseUrl ?? "";
            config.AiSilenceSeconds = silenceSeconds;
            config.AiSystemPrompt = systemPrompt ?? "";
            config.AiOverridePrompt = overridePrompt ?? "";
            config.ResumeContext = resumeContext ?? "";
            config.JdContext = jdContext ?? "";
            config.TargetCompany = targetCompany ?? "";
            config.ExtraContext = extraContext ?? "";
            config.VisionEnabled = visionEnabled;
            config.VisionBaseUrl = visionBaseUrl ?? "";
            config.VisionModel = visionModel ?? "";
            config.SolvePrompt = solvePrompt ?? "";
            config.Normalize();
            aiTimer.Interval = TimeSpan.FromSeconds(config.AiSilenceSeconds);
            if (!enabled)
            {
                aiTimer.Stop();
                CancelAiRequest();
                aiQueue.Clear();
                collectingSpeechBatch = null;
                activeSpeechBatch = null;
                restartActiveBatch = false;
                aiBusy = false;
                if (streamingAiEntry != null && streamingAiEntry.Streaming)
                    chatEntries.Remove(streamingAiEntry);
                streamingAiEntry = null;
                RefreshText();
            }
            SaveConfig();
            AppLog.Write(string.Format(
                "ai settings enabled={0} model={1} mode={2} delay={3}",
                config.AiEnabled, config.AiModel, config.AiMode, config.AiSilenceSeconds));
        }

        internal async Task<string> TestAiConnectionAsync()
        {
            string key = SecretStore.LoadApiKey();
            if (key.Length == 0) throw new InvalidOperationException("请先填写 API Key");
            using (CancellationTokenSource timeout = new CancellationTokenSource(TimeSpan.FromSeconds(35)))
            {
                return await DeepSeekClient.CompleteAsync(
                    config, key, "请只回复：连接成功", timeout.Token);
            }
        }

        private void CancelAiRequest()
        {
            StopAiTypewriter();
            CancellationTokenSource request = aiRequestCancellation;
            aiRequestCancellation = null;
            if (request == null) return;
            try { request.Cancel(); } catch { }
        }

        private void ResetAiTypewriter()
        {
            aiTypewriterTimer.Stop();
            aiGlyphQueue.Clear();
            aiTypedText.Clear();
            aiNetworkComplete = false;
            aiTypeTick = 0;
            aiTypingCompletion = new TaskCompletionSource<bool>();
        }

        private void EnqueueAiDelta(string delta)
        {
            if (string.IsNullOrEmpty(delta)) return;
            TextElementEnumerator elements = StringInfo.GetTextElementEnumerator(delta);
            while (elements.MoveNext()) aiGlyphQueue.Enqueue(elements.GetTextElement());
            if (!aiTypewriterTimer.IsEnabled) aiTypewriterTimer.Start();
        }

        private void TypeNextAiCharacters()
        {
            if (streamingAiEntry == null)
            {
                aiTypewriterTimer.Stop();
                return;
            }
            int requested = (++aiTypeTick % 2 == 0) ? 2 : 1;
            int emitted = 0;
            while (emitted < requested && aiGlyphQueue.Count > 0)
            {
                aiTypedText.Append(aiGlyphQueue.Dequeue());
                emitted++;
            }
            if (emitted > 0)
            {
                aiHasVisibleOutput = true;
                streamingAiEntry.Text = aiTypedText.ToString();
                streamingAiEntry.Streaming = true;
                PhoneAiFeed.Post(streamingAiEntry.Text, false);
                RefreshText();
                FadeTo(config.Opacity, 80);
                AppLog.Write(string.Format(
                    "ai type +{0} total={1} pending={2}",
                    emitted, aiTypedText.Length, aiGlyphQueue.Count));
            }
            if (aiGlyphQueue.Count == 0)
            {
                aiTypewriterTimer.Stop();
                if (aiNetworkComplete) CompleteAiTyping();
            }
        }

        private void CompleteAiTyping()
        {
            if (aiTypingCompletion != null) aiTypingCompletion.TrySetResult(true);
        }

        private void StopAiTypewriter()
        {
            aiTypewriterTimer.Stop();
            aiGlyphQueue.Clear();
            aiNetworkComplete = false;
            if (aiTypingCompletion != null) aiTypingCompletion.TrySetCanceled();
        }

        private async void StartAiRequest()
        {
            if (!config.AiEnabled || aiBusy || aiQueue.Count == 0) return;
            string key = SecretStore.LoadApiKey();
            if (key.Length == 0)
            {
                AppLog.Write("ai skipped missing_api_key");
                return;
            }
            aiBusy = true;
            aiHasVisibleOutput = false;
            restartActiveBatch = false;
            CancellationTokenSource requestCancellation = new CancellationTokenSource();
            aiRequestCancellation = requestCancellation;
            activeSpeechBatch = aiQueue.First.Value;
            aiQueue.RemoveFirst();
            if (collectingSpeechBatch == activeSpeechBatch) collectingSpeechBatch = null;
            string transcript = activeSpeechBatch.CombinedText;
            if (transcript.Length > 5000) transcript = transcript.Substring(transcript.Length - 5000);
            List<ConversationMessage> context = new List<ConversationMessage>(conversationHistory);
            context.Add(new ConversationMessage("user", transcript));
            streamingAiEntry = new ChatEntry
            {
                Role = "assistant",
                Text = "",
                Streaming = true,
                SegmentId = -1
            };
            chatEntries.Add(streamingAiEntry);
            TrimChatEntries();
            RefreshText();
            ResetAiTypewriter();
            AppLog.Write(string.Format(
                "ai request model={0} mode={1} segments={2} chars={3} context_messages={4} queued_batches={5}",
                config.AiModel,
                config.AiMode,
                activeSpeechBatch.Segments.Count,
                transcript.Length,
                context.Count,
                aiQueue.Count));
            try
            {
                string result = await DeepSeekClient.CompleteStreamAsync(
                    config,
                    key,
                    context,
                    delegate(string delta)
                    {
                        Action update = delegate
                        {
                            if (requestCancellation.IsCancellationRequested) return;
                            EnqueueAiDelta(delta);
                        };
                        if (Dispatcher.CheckAccess()) update(); else Dispatcher.Invoke(update);
                    },
                    requestCancellation.Token);
                if (requestCancellation.IsCancellationRequested) return;
                aiNetworkComplete = true;
                AppLog.Write("ai sse complete chars=" + result.Length
                    + " pending_glyphs=" + aiGlyphQueue.Count);
                if (aiGlyphQueue.Count == 0) CompleteAiTyping();
                await aiTypingCompletion.Task;
                if (requestCancellation.IsCancellationRequested) return;
                streamingAiEntry.Text = result;
                streamingAiEntry.Streaming = false;
                PhoneAiFeed.Post(result, true);
                conversationHistory.Add(new ConversationMessage("user", transcript));
                conversationHistory.Add(new ConversationMessage("assistant", result));
                TrimConversationHistory();
                RefreshText();
                FadeTo(config.Opacity, 170);
                string logResult = result.Length > 160 ? result.Substring(0, 160) : result;
                AppLog.Write("ai result=" + logResult);
            }
            catch (OperationCanceledException)
            {
                StopAiTypewriter();
                if (streamingAiEntry != null) chatEntries.Remove(streamingAiEntry);
                PhoneAiFeed.Post("", true);
                RefreshText();
                AppLog.Write("ai cancelled");
            }
            catch (Exception error)
            {
                StopAiTypewriter();
                if (streamingAiEntry != null)
                {
                    streamingAiEntry.Text = "请求失败，请检查 AI 设置。";
                    streamingAiEntry.Streaming = false;
                    PhoneAiFeed.Post(streamingAiEntry.Text, true);
                    RefreshText();
                }
                AppLog.Write("ai error=" + error.Message);
                // 失败不弹窗：字幕区已内嵌显示「请求失败」，日志记录详情
            }
            finally
            {
                if (restartActiveBatch && activeSpeechBatch != null && config.AiEnabled)
                {
                    aiQueue.AddFirst(activeSpeechBatch);
                    collectingSpeechBatch = activeSpeechBatch;
                    AppLog.Write("ai request restarted merged_segments="
                        + activeSpeechBatch.Segments.Count);
                }
                restartActiveBatch = false;
                activeSpeechBatch = null;
                aiHasVisibleOutput = false;
                streamingAiEntry = null;
                aiBusy = false;
                if (aiRequestCancellation == requestCancellation)
                {
                    aiRequestCancellation = null;
                }
                requestCancellation.Dispose();
                if (config.AiEnabled && aiQueue.Count > 0)
                {
                    aiTimer.Stop();
                    aiTimer.Interval = TimeSpan.FromSeconds(config.AiSilenceSeconds);
                    aiTimer.Start();
                    AppLog.Write("ai next batch queued_batches=" + aiQueue.Count
                        + " delay_seconds=" + config.AiSilenceSeconds);
                }
            }
        }

        private void TrimConversationHistory()
        {
            while (conversationHistory.Count > 20)
            {
                conversationHistory.RemoveAt(0);
                if (conversationHistory.Count > 0 && conversationHistory[0].Role == "assistant")
                    conversationHistory.RemoveAt(0);
            }
        }

        private void StartManualResize(int hit)
        {
            if (config.Locked || hit == 0) return;
            manualResizeHit = hit;
            NativeMethods.GetCursorPos(out resizeStartCursor);
            resizeStartLeft = Left;
            resizeStartTop = Top;
            resizeStartWidth = ActualWidth;
            resizeStartHeight = ActualHeight;
            manualResizing = CaptureMouse();
            AppLog.Write("resize begin hit=" + hit);
        }

        private void UpdateManualResize()
        {
            NativeMethods.NativePoint cursor;
            if (!NativeMethods.GetCursorPos(out cursor)) return;
            double scaleX = 1.0;
            double scaleY = 1.0;
            PresentationSource source = PresentationSource.FromVisual(this);
            if (source != null && source.CompositionTarget != null)
            {
                Matrix transform = source.CompositionTarget.TransformFromDevice;
                scaleX = transform.M11;
                scaleY = transform.M22;
            }
            double dx = (cursor.X - resizeStartCursor.X) * scaleX;
            double dy = (cursor.Y - resizeStartCursor.Y) * scaleY;
            double newLeft = resizeStartLeft;
            double newTop = resizeStartTop;
            double newWidth = resizeStartWidth;
            double newHeight = resizeStartHeight;
            bool left = manualResizeHit == NativeMethods.HTLEFT
                || manualResizeHit == NativeMethods.HTTOPLEFT
                || manualResizeHit == NativeMethods.HTBOTTOMLEFT;
            bool right = manualResizeHit == NativeMethods.HTRIGHT
                || manualResizeHit == NativeMethods.HTTOPRIGHT
                || manualResizeHit == NativeMethods.HTBOTTOMRIGHT;
            bool top = manualResizeHit == NativeMethods.HTTOP
                || manualResizeHit == NativeMethods.HTTOPLEFT
                || manualResizeHit == NativeMethods.HTTOPRIGHT;
            bool bottom = manualResizeHit == NativeMethods.HTBOTTOM
                || manualResizeHit == NativeMethods.HTBOTTOMLEFT
                || manualResizeHit == NativeMethods.HTBOTTOMRIGHT;
            if (left) { newLeft += dx; newWidth -= dx; }
            if (right) newWidth += dx;
            if (top) { newTop += dy; newHeight -= dy; }
            if (bottom) newHeight += dy;
            if (newWidth < MinWidth)
            {
                if (left) newLeft -= MinWidth - newWidth;
                newWidth = MinWidth;
            }
            if (newWidth > MaxWidth)
            {
                if (left) newLeft += newWidth - MaxWidth;
                newWidth = MaxWidth;
            }
            if (newHeight < MinHeight)
            {
                if (top) newTop -= MinHeight - newHeight;
                newHeight = MinHeight;
            }
            if (newHeight > MaxHeight)
            {
                if (top) newTop += newHeight - MaxHeight;
                newHeight = MaxHeight;
            }
            applyingGeometry = true;
            try
            {
                Left = newLeft;
                Top = newTop;
                Width = newWidth;
                Height = newHeight;
            }
            finally { applyingGeometry = false; }
            PositionLockIndicator();
        }

        private void FinishManualResize()
        {
            if (!manualResizing) return;
            manualResizing = false;
            ReleaseMouseCapture();
            config.Width = ActualWidth;
            config.Height = ActualHeight;
            RememberPosition();
            SaveConfig();
            AppLog.Write(string.Format(
                "resize end width={0:0} height={1:0}", ActualWidth, ActualHeight));
            UpdateResizeCursor(NativeMethods.ResizeHitAtCursor(hwnd, 12));
        }

        private void UpdateResizeCursor(int hit)
        {
            if (hit == NativeMethods.HTLEFT || hit == NativeMethods.HTRIGHT)
                Cursor = Cursors.SizeWE;
            else if (hit == NativeMethods.HTTOP || hit == NativeMethods.HTBOTTOM)
                Cursor = Cursors.SizeNS;
            else if (hit == NativeMethods.HTTOPLEFT || hit == NativeMethods.HTBOTTOMRIGHT)
                Cursor = Cursors.SizeNWSE;
            else if (hit == NativeMethods.HTTOPRIGHT || hit == NativeMethods.HTBOTTOMLEFT)
                Cursor = Cursors.SizeNESW;
            else Cursor = Cursors.Arrow;
        }

        private double CalculatedHeight()
        {
            return config.Height;
        }

        private void ApplySize(bool keepAnchor)
        {
            double oldCenter = Left + ActualWidth / 2;
            double oldBottom = Top + ActualHeight;
            applyingGeometry = true;
            try
            {
                Width = config.Width;
                Height = config.Height;
                text.FontSize = config.FontSize;
                text.FontFamily = new FontFamily(config.FontFamilyName);
                text.Foreground = BrushFromHex(config.TextColor, 255);
                text.LineHeight = config.FontSize * 1.38;
                scroll.MaxHeight = double.PositiveInfinity;
                if (keepAnchor && !double.IsNaN(oldCenter) && ActualWidth > 0)
                {
                    Left = oldCenter - Width / 2;
                    Top = oldBottom - Height;
                }
            }
            finally { applyingGeometry = false; }
        }

        private void RestorePosition()
        {
            if (!double.IsNaN(config.Left) && !double.IsNaN(config.Top) && PositionVisible(config.Left, config.Top))
            {
                Left = config.Left;
                Top = config.Top;
                return;
            }
            Forms.Screen screen = FindConfiguredScreen() ?? Forms.Screen.PrimaryScreen;
            PlaceBottomCenter(screen);
        }

        private bool PositionVisible(double left, double top)
        {
            foreach (Forms.Screen screen in Forms.Screen.AllScreens)
            {
                System.Drawing.Rectangle area = screen.WorkingArea;
                if (left + Width > area.Left + 120 && left < area.Right - 120 &&
                    top + Height > area.Top + 50 && top < area.Bottom - 50)
                    return true;
            }
            return false;
        }

        private Forms.Screen FindConfiguredScreen()
        {
            foreach (Forms.Screen screen in Forms.Screen.AllScreens)
                if (screen.DeviceName == config.ScreenName) return screen;
            return null;
        }

        private void PlaceBottomCenter(Forms.Screen screen)
        {
            System.Drawing.Rectangle area = screen.WorkingArea;
            Left = area.Left + (area.Width - Width) / 2.0;
            Top = area.Bottom - Height - 72;
            config.ScreenName = screen.DeviceName;
            RememberPosition();
        }

        internal void MoveToScreen(string name)
        {
            foreach (Forms.Screen screen in Forms.Screen.AllScreens)
            {
                if (screen.DeviceName == name)
                {
                    PlaceBottomCenter(screen);
                    PositionSettings();
                    SaveConfig();
                    break;
                }
            }
        }

        internal void ResetPosition()
        {
            config.Width = 980;
            config.Height = 150;
            ApplySize(false);
            PlaceBottomCenter(FindConfiguredScreen() ?? Forms.Screen.PrimaryScreen);
            settings.Sync(config);
            RefreshText();
            PositionSettings();
            SaveConfig();
        }

        private void RememberPosition()
        {
            config.Left = Left;
            config.Top = Top;
            System.Drawing.Point center = new System.Drawing.Point(
                (int)(Left + Width / 2), (int)(Top + Height / 2));
            config.ScreenName = Forms.Screen.FromPoint(center).DeviceName;
        }

        internal void SaveConfig()
        {
            RememberPosition();
            config.Save();
        }

        private void ReloadConfigIfChanged()
        {
            try
            {
                if (!File.Exists(OverlayConfig.ConfigPath)) return;
                DateTime stamp = File.GetLastWriteTimeUtc(OverlayConfig.ConfigPath);
                if (stamp <= configLastWrite) return;
                configLastWrite = stamp;
                OverlayConfig fresh = OverlayConfig.Load();
                string oldScreen = config.ScreenName;
                bool oldLiveTranslate = config.LiveTranslateEnabled;
                config.ApplyFrom(fresh);
                if (!editMode && hwnd != IntPtr.Zero)
                    NativeMethods.SetNormalInteraction(hwnd, config.Locked);
                lockIndicator.UpdateState(config.Locked);
                aiTimer.Interval = TimeSpan.FromSeconds(config.AiSilenceSeconds);
                if (oldLiveTranslate && !config.LiveTranslateEnabled)
                    CancelLocalTranslation(true);
                if (!config.AiEnabled)
                {
                    aiTimer.Stop();
                    CancelAiRequest();
                    aiQueue.Clear();
                    collectingSpeechBatch = null;
                    activeSpeechBatch = null;
                    restartActiveBatch = false;
                    aiBusy = false;
                    if (streamingAiEntry != null && streamingAiEntry.Streaming)
                        chatEntries.Remove(streamingAiEntry);
                    streamingAiEntry = null;
                }
                ApplySize(true);
                SetResizeFrame(false);
                if (oldScreen != config.ScreenName)
                {
                    Forms.Screen target = FindConfiguredScreen();
                    if (target != null) PlaceBottomCenter(target);
                }
                RefreshText();
                if (!bossHidden)
                {
                    BeginAnimation(Window.OpacityProperty, null);
                    Opacity = config.Opacity;
                }
                if (settings.IsVisible) settings.Sync(config);
                AppLog.Write(string.Format(
                    "config reloaded font={0} size={1} color={2} width={3} height={4} opacity={5:0.00} applied_opacity={6:0.00} locked={7} click_through={8} ai={9} live_translate={10}",
                    config.FontFamilyName,
                    config.FontSize,
                    config.TextColor,
                    config.Width,
                    config.Height,
                    config.Opacity,
                    Opacity,
                    config.Locked,
                    (NativeMethods.GetExtendedStyle(hwnd) & NativeMethods.WS_EX_TRANSPARENT) != 0,
                    config.AiEnabled,
                    config.LiveTranslateEnabled));
            }
            catch (Exception error)
            {
                AppLog.Write("config reload error=" + error.Message);
            }
        }

        private void PositionSettings()
        {
            if (!editMode) return;
            settings.UpdateLayout();
            Forms.Screen screen = Forms.Screen.FromPoint(
                new System.Drawing.Point((int)(Left + Width / 2), (int)(Top + Height / 2)));
            System.Drawing.Rectangle area = screen.WorkingArea;
            double x = Math.Max(area.Left, Math.Min(area.Right - settings.ActualWidth, Left + Width / 2 - settings.ActualWidth / 2));
            double above = Top - settings.ActualHeight - 12;
            settings.Left = x;
            settings.Top = above >= area.Top ? above : Math.Min(area.Bottom - settings.ActualHeight, Top + Height + 12);
        }

        private static string Truncate(string value, int length)
        {
            return value.Length <= length ? value : value.Substring(0, length);
        }

        public void Dispose()
        {
            IsClosing = true;
            SaveConfig();
            hoverTimer.Stop();
            geometrySaveTimer.Stop();
            configTimer.Stop();
            aiTimer.Stop();
            CancelLocalTranslation(true);
            CancelAiRequest();
            subscriber.Dispose();
            if (hwnd != IntPtr.Zero)
            {
                NativeMethods.UnregisterHotKey(hwnd, NativeMethods.HOTKEY_ID);
                NativeMethods.UnregisterHotKey(hwnd, NativeMethods.BOSS_HOTKEY_ID);
                NativeMethods.UnregisterHotKey(hwnd, NativeMethods.MOVE_UP_HOTKEY_ID);
                NativeMethods.UnregisterHotKey(hwnd, NativeMethods.MOVE_DOWN_HOTKEY_ID);
                NativeMethods.UnregisterHotKey(hwnd, NativeMethods.MOVE_LEFT_HOTKEY_ID);
                NativeMethods.UnregisterHotKey(hwnd, NativeMethods.MOVE_RIGHT_HOTKEY_ID);
                NativeMethods.UnregisterHotKey(hwnd, NativeMethods.SOLVE_HOTKEY_ID);
                if (hwndSource != null) hwndSource.RemoveHook(WindowProc);
            }
            tray.Visible = false;
            tray.Dispose();
            lockIndicator.Close();
            settings.CloseForShutdown();
        }
    }

    internal static class Program
    {
        private static Mutex instanceMutex;
        private static OverlayWindow overlay;

        [STAThread]
        internal static void Main(string[] args)
        {
            bool created;
            instanceMutex = new Mutex(true, "Local\\WasapiParaformerDesktopOverlay", out created);
            if (!created) return;
            try { NativeMethods.SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch { }

            OverlayConfig config = OverlayConfig.Load();
            bool edit = false;
            bool aiSettings = false;
            bool demoAllowCapture = false;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--url" && i + 1 < args.Length) config.WebSocketUrl = args[++i];
                else if (args[i] == "--edit") edit = true;
                else if (args[i] == "--ai-settings") { edit = true; aiSettings = true; }
                else if (args[i] == "--allow-capture") demoAllowCapture = true;
            }

            Application application = new Application();
            application.ShutdownMode = ShutdownMode.OnExplicitShutdown;
            application.DispatcherUnhandledException += delegate(
                object sender, System.Windows.Threading.DispatcherUnhandledExceptionEventArgs error)
            {
                AppLog.Write("unhandled " + error.Exception);
                error.Handled = true;
            };
            AppLog.Write("startup url=" + config.WebSocketUrl
                + " capture_invisible=" + config.CaptureInvisible
                + " demo_allow_capture=" + demoAllowCapture);
            overlay = new OverlayWindow(config, demoAllowCapture);
            application.Exit += delegate { if (overlay != null) overlay.Dispose(); };
            overlay.Show();
            if (edit) overlay.Dispatcher.BeginInvoke(new Action(delegate
            {
                overlay.SetEditMode(true);
                if (aiSettings) overlay.ShowAiSettingsTab();
            }));
            application.Run();
            AppLog.Write("shutdown");
            instanceMutex.ReleaseMutex();
            instanceMutex.Dispose();
        }
    }
}
