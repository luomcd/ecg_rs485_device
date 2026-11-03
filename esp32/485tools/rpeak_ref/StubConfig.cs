// HeartRateDetector 只用到 AppConfig.DisplayFrameRate 一个成员，
// 这里给个最小替身，避免把整个 WinForms 的 AppConfig 拖进来。
namespace ADS1293_Ecg.Config;

public static class AppConfig
{
    public const double DisplayFrameRate = 500;
}
