// S7 参考基准：用 WinApp 的 HeartRateDetector（原封不动）跑 ecg.bin，
// 输出 R 峰索引与 BPM，作为 C 移植的比对基准。
//
// 用法: dotnet run -- <bin 文件> [输出 txt]
using System.Text;
using ADS1293_Ecg.Processing;

internal static class Program
{
    private static int Main(string[] args)
    {
        var path = args.Length > 0 ? args[0]
            : @"C:/work2026/VsPro/WinApp_ADSECG/ADS1293_Ecg/bin/Debug/net10.0-windows/recordings/ecg_20260929_093408.bin";
        var outPath = args.Length > 1 ? args[1] : "ref_peaks.txt";

        var samples = LoadCh1(path, out int frames, out int lenType);

        // --max N：只喂前 N 个样本。用途：HeartRateDetector.Peaks 只保留最近 32 个，
        // 想看**前段**的峰就必须让总峰数 < 32，否则前面的被挤掉。
        int maxN = int.MaxValue;
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == "--max" && int.TryParse(args[i + 1], out var v)) maxN = v;
        }
        if (maxN < samples.Count) samples = samples.GetRange(0, maxN);

        Console.WriteLine($"file      : {path}");
        Console.WriteLine($"frames    : {frames}, LEN={lenType}");
        Console.WriteLine($"samples   : {samples.Count}  ({samples.Count / 500.0:F2} s @ 500 Hz)");

        // --trace <out.txt>：每帧（50 ms）记一次 HeartRate，用来复现"上电后 BPM 怎么一步步
        // 收敛到真值"的过程 —— 固件每 200 ms 发布一次，所以这就是屏上会看到的读数序列。
        string tracePath = null;
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == "--trace") tracePath = args[i + 1];
        }
        var trace = (tracePath != null) ? new StringBuilder() : null;
        if (trace != null) trace.AppendLine("frame,t_s,bpm");

        // --all-peaks <out.txt>：把**每一个** AcceptedPeak 都落盘（MIT-BIH 评估必需）。
        // det.Peaks 只保留最近 32 个，30 min 的记录上万拍，拿不到全量就没法算 Se/+P。
        // 每帧（25 样本）轮询一次 GetDiagnostics().LastPeakIndex；75 bpm 下一拍跨 ~8 帧，
        // 正常不会漏。启动瞬态若一帧内连认两拍可能少记一个，评估时会计成 FN —— 如实暴露。
        string allPeaksPath = null;
        for (int i = 0; i < args.Length - 1; i++)
            if (args[i] == "--all-peaks") allPeaksPath = args[i + 1];
        var allPeaks = (allPeaksPath != null) ? new List<long>() : null;
        long lastSeenPeak = long.MinValue;

        // --per-frame N：每帧喂多少样本。默认 25（模拟实机"一帧一调"）。
        // W1-2 用途：同一条记录跑 N=25 与 N=1，公共区峰序列必须一致，才算证明
        // 「每帧轮询 LastPeakIndex」不漏峰。N=1 时每样本轮询一次，等价于逐次 accept 记账。
        int perFrame = 25;
        for (int i = 0; i < args.Length - 1; i++)
            if (args[i] == "--per-frame" && int.TryParse(args[i + 1], out var pf) && pf > 0)
                perFrame = pf;

        var det = new HeartRateDetector();
        // 逐帧喂（每帧 perFrame 样本）
        for (int i = 0; i + perFrame <= samples.Count; i += perFrame)
        {
            det.Process(System.Runtime.InteropServices.CollectionsMarshal.AsSpan(samples).Slice(i, perFrame));
            if (trace != null)
            {
                trace.AppendLine($"{i / perFrame},{(i + perFrame) / 500.0:F2},{det.HeartRate:F2}");
            }
            if (allPeaks != null)
            {
                var lp = det.GetDiagnostics().LastPeakIndex;
                if (lp.HasValue && lp.Value != lastSeenPeak)
                {
                    allPeaks.Add(lp.Value);
                    lastSeenPeak = lp.Value;
                }
            }
        }

        var peaks = det.Peaks;
        Console.WriteLine($"peaks     : {peaks.Count}");
        Console.WriteLine($"heartRate : {det.HeartRate:F2} bpm");

        var sb = new StringBuilder();
        sb.AppendLine($"# file={System.IO.Path.GetFileName(path)}");
        sb.AppendLine($"# samples={samples.Count} frames={frames} len={lenType}");
        sb.AppendLine($"# peaks={peaks.Count} bpm={det.HeartRate:F4}");
        sb.AppendLine("index,time_s");
        foreach (var p in peaks)
        {
            sb.AppendLine($"{p},{(p / 500.0):F4}");
        }
        File.WriteAllText(outPath, sb.ToString(), new UTF8Encoding(false));
        Console.WriteLine($"written   : {outPath}");

        if (allPeaks != null && allPeaksPath != null)
        {
            var pb = new StringBuilder();
            pb.AppendLine("index,time_s");
            foreach (var v in allPeaks) pb.AppendLine($"{v},{(v / 500.0):F4}");
            File.WriteAllText(allPeaksPath, pb.ToString(), new UTF8Encoding(false));
            Console.WriteLine($"all-peaks : {allPeaksPath}  ({allPeaks.Count} 个)");
        }

        if (trace != null && tracePath != null)
        {
            File.WriteAllText(tracePath, trace.ToString(), new UTF8Encoding(false));
            Console.WriteLine($"trace     : {tracePath}");
        }

        // 前 10 个 + 末 5 个，便于直观看
        Console.Write("first 10  : ");
        Console.WriteLine(string.Join(", ", peaks.Take(10)));
        Console.Write("last 5    : ");
        Console.WriteLine(string.Join(", ", peaks.TakeLast(5)));
        return 0;
    }

    /// <summary>按帧解析，只取 ch1（双通道时 ch1 在前 25 个）</summary>
    private static List<double> LoadCh1(string path, out int frames, out int lenType)
    {
        var data = File.ReadAllBytes(path);
        var outp = new List<double>(data.Length / 2);
        frames = 0;
        lenType = 0;

        int i = 0;
        while (i + 6 <= data.Length)
        {
            if (data[i] != 0xAA || data[i + 1] != 0x55) break;
            int len = data[i + 3] | (data[i + 4] << 8);
            int total = 6 + len;
            if (i + total > data.Length) break;

            lenType = len;
            int off = (len % 2 == 1) ? 3 : 2;      // 奇数：SEQ(2)+STA(1)；偶数：SEQ(2)
            int n = (len - off) / 2;
            if (len == 103) n = 25;                // 双通道块布局：ch1 前 25
            for (int k = 0; k < n; k++)
            {
                outp.Add(BitConverter.ToInt16(data, i + 5 + off + k * 2));
            }
            frames++;
            i += total;
        }
        return outp;
    }
}
