using System;
using System.IO;
using UnityEngine;

/// <summary>
/// VR 端启动时读取 vr_config.json（PC 端 vr_config.py 维护），把 PC IP/端口读到 PICO 单例。
///
/// 协议：
///   - 文件位置：Assets/StreamingAssets/vr_config.json
///   - 格式：{"pc_ip":"192.168.x.x","trigger_port":12345,"video_port":12346,...}
///   - PC 端 vr_config.py 改文件 → 重新打 APK → VR 端启动读到新 IP
///   - 找不到文件时使用 PICO.cs 里的硬编码兜底值
/// </summary>
public static class VRConfigLoader
{
    public const string CONFIG_REL_PATH = "vr_config.json";
    public const string DEFAULT_PC_IP   = "192.168.1.101";
    public const int    DEFAULT_TRIG_PORT = 12345;
    public const int    DEFAULT_VIDEO_PORT = 12346;

    [Serializable]
    private class JsonConfig
    {
        public string pc_ip = DEFAULT_PC_IP;
        public int trigger_port = DEFAULT_TRIG_PORT;
        public int video_port = DEFAULT_VIDEO_PORT;
    }

    /// <summary>读 StreamingAssets/vr_config.json，失败时返回默认配置</summary>
    public static (string ip, int trigPort, int videoPort) Load()
    {
        string path = Path.Combine(Application.streamingAssetsPath, CONFIG_REL_PATH);
        if (!File.Exists(path))
        {
            Debug.LogWarning($"[VRConfig] 未找到 {path}，使用默认 IP={DEFAULT_PC_IP}");
            return (DEFAULT_PC_IP, DEFAULT_TRIG_PORT, DEFAULT_VIDEO_PORT);
        }

        try
        {
            string text = File.ReadAllText(path);
            JsonConfig jc = JsonUtility.FromJson<JsonConfig>(text);
            if (jc == null || string.IsNullOrEmpty(jc.pc_ip))
            {
                Debug.LogWarning($"[VRConfig] {path} 解析失败，使用默认值");
                return (DEFAULT_PC_IP, DEFAULT_TRIG_PORT, DEFAULT_VIDEO_PORT);
            }
            Debug.Log($"[VRConfig] ✓ 读配置: PC={jc.pc_ip} trigger={jc.trigger_port} video={jc.video_port}");
            return (jc.pc_ip, jc.trigger_port, jc.video_port);
        }
        catch (Exception e)
        {
            Debug.LogError($"[VRConfig] 读 {path} 异常: {e.Message}");
            return (DEFAULT_PC_IP, DEFAULT_TRIG_PORT, DEFAULT_VIDEO_PORT);
        }
    }
}
