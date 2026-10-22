"""
VR 连接配置管理
================

VR 端（PICO）和 PC 端在同一个 WiFi 下时，PC 的 IP 可能会变（DHCP 重启、换路由器等）。
我们把 IP/端口存到 vr_config.json 里：

  1. PC 端 启动时检查：能 ping/连就用当前 IP 写文件，VR 启动时读最新 IP
  2. VR 端 Unity 启动时读 StreamingAssets/vr_config.json，用里面写的 PC IP 去连
  3. 命令行工具：python -m vr_config show  /  set 192.168.1.50

这样 IP 变了，只改这一个文件，VR 端重启就生效。

文件位置（两份要保持一致）：
  - PC:  e:\\VR展示\\vr_config.json
  - VR:  Assets/StreamingAssets/vr_config.json  (Unity 打包到 APK 里)
"""

import json
import os
import sys
import socket
import subprocess
from typing import Optional, Tuple

# Windows PowerShell GBK 终端兼容
try:
    if sys.stdout.encoding.lower().startswith('gb'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass


# 配置的相对路径（PC 和 VR 都要读这个）
DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vr_config.json",
)

DEFAULT_CONFIG = {
    "pc_ip": "192.168.1.101",          # PC 端 IP（VR 端连这个）
    "trigger_port": 12345,              # 标记 TCP 端口
    "video_port": 12346,                # 视频 TCP 端口
    "wifi_ssid_hint": "",               # 提示：要连的 WiFi 名（不强制）
    "last_updated": "",                 # 上次修改时间
    "auto_discover": True,              # 启动时是否自动探测 PC IP
}


def load(path: str = DEFAULT_PATH) -> dict:
    if not os.path.isfile(path):
        save(DEFAULT_CONFIG, path)
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 补默认字段
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception as e:
        print(f"[vr_config] 读 {path} 失败 ({e})，用默认配置")
        return dict(DEFAULT_CONFIG)


def save(cfg: dict, path: str = DEFAULT_PATH) -> None:
    """同步到磁盘（同时写 PC 端和 StreamingAssets 两份）"""
    import time
    cfg["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    # 同步到 Unity StreamingAssets（如果存在），避免两边漂移
    sa_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "vrcode", "Assets", "StreamingAssets", "vr_config.json",
    )
    try:
        os.makedirs(os.path.dirname(sa_path), exist_ok=True)
        with open(sa_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"[vr_config] 已同步到 {sa_path}")
    except Exception as e:
        print(f"[vr_config] 同步到 StreamingAssets 失败: {e}（不影响 PC 端）")


def get_pc_ip() -> str:
    """
    自动探测 PC 当前局域网 IP。
    优先返回 WiFi 适配器 IP（VR PICO 连 WiFi），其次才是有线网。

    实现：用 PowerShell Get-NetIPAddress 枚举所有网卡，匹配 WiFi 关键字，
    不发包，纯本地查注册表/接口信息，速度快（< 200ms）。
    """
    import subprocess

    # 优先 WiFi 关键字（不同 Windows 语言也覆盖）
    WIFI_KEYWORDS = ["wi-fi", "wlan", "wifi", "无线", "802.11"]

    try:
        # PowerShell: 拿所有物理网卡 + 有 IPv4 地址的
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-NetIPAddress -AddressFamily IPv4 | "
                "Where-Object { $_.InterfaceAlias -match 'Wi-Fi|WLAN|802.11|Wireless|无线' } | "
                "Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } | "
                "Select-Object -First 1 -ExpandProperty IPAddress"
            ],
            capture_output=True, text=True,
            timeout=5,
        )
        if out.returncode == 0:
            ip = out.stdout.strip()
            if ip and _is_valid_ip(ip):
                return ip
    except Exception:
        pass

    # Fallback A：所有非 127 的 IPv4（兜底，比 socket.connect 准）
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-NetIPAddress -AddressFamily IPv4 | "
                "Where-Object { $_.IPAddress -notlike '127.*' -and "
                "$_.IPAddress -notlike '169.254.*' } | "
                "Select-Object -First 1 -ExpandProperty IPAddress"
            ],
            capture_output=True, text=True,
            timeout=5,
        )
        if out.returncode == 0:
            ip = out.stdout.strip()
            if ip and _is_valid_ip(ip):
                return ip
    except Exception:
        pass

    # Fallback B：socket.connect（最兜底，但会选默认路由）
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _is_valid_ip(ip: str) -> bool:
    import re
    return bool(re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip))


def ping(ip: str, timeout_ms: int = 800) -> bool:
    """快速 ping 检测（一发即收）"""
    try:
        kwargs = {"timeout": timeout_ms / 1000.0}
        # Windows 用 -n 1 -w
        if os.name == "nt":
            out = subprocess.run(
                ["ping", "-n", "1", "-w", str(timeout_ms), ip],
                capture_output=True, text=True, **kwargs
            )
        else:
            out = subprocess.run(
                ["ping", "-c", "1", "-W", str(int(timeout_ms / 1000)), ip],
                capture_output=True, text=True, **kwargs
            )
        return out.returncode == 0
    except Exception:
        return False


def check_connect(ip: str, port: int, timeout_s: float = 1.0) -> bool:
    """TCP 连一下试试"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout_s)
        s.connect((ip, port))
        s.close()
        return True
    except Exception:
        return False


def auto_discover_and_update(path: str = DEFAULT_PATH) -> dict:
    """
    启动时调一次：
      1) 探测 PC 当前 IP（优先 WiFi）
      2) 与配置文件对比
         - 相同：什么都不做
         - 不同：ping 旧 IP
           - 能通：保留旧配置（可能有线/无线并存，WiFi IP 变了但有线还通）
           - 不通：自动更新为新 IP（DHCP 换了）
    """
    cfg = load(path)
    new_ip = get_pc_ip()
    print(f"[vr_config] 探测到 PC 当前 IP: {new_ip}")
    print(f"[vr_config] 配置文件中 IP:     {cfg['pc_ip']}")

    if cfg["pc_ip"] == new_ip:
        print("[vr_config] 配置一致，无需更新")
        return cfg

    # IP 变了，ping 旧 IP 看看还通不通
    old_alive = ping(cfg["pc_ip"])
    if old_alive:
        print(f"[vr_config] 旧 IP {cfg['pc_ip']} 仍可 ping 通，保留旧配置")
        return cfg

    # 旧 IP 不通了，且 auto_discover=True → 自动更新
    if not cfg.get("auto_discover", True):
        print(f"[vr_config] auto_discover=False，"
              f"旧 IP {cfg['pc_ip']} 不通但跳过更新（需手动 set）")
        return cfg

    print(f"[vr_config] 旧 IP {cfg['pc_ip']} 不可达，"
          f"自动更新为新 IP {new_ip}")
    cfg["pc_ip"] = new_ip
    save(cfg, path)
    return cfg


def cli():
    """命令行入口：python -m vr_config [show|set IP|discover|check]"""
    if len(sys.argv) < 2 or sys.argv[1] in ("show", "-h", "--help"):
        cfg = load()
        print("当前 vr_config.json 内容：")
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        if len(sys.argv) < 2:
            print("\n用法：")
            print("  python vr_config.py show             # 显示当前配置")
            print("  python vr_config.py set 192.168.1.50 # 修改 PC IP")
            print("  python vr_config.py discover         # 探测当前 PC IP 并自动更新")
            print("  python vr_config.py check            # 检查配置 IP 是否可达")
        return

    cmd = sys.argv[1]
    if cmd == "set":
        if len(sys.argv) < 3:
            print("用法：python vr_config.py set <new_ip>")
            sys.exit(1)
        cfg = load()
        cfg["pc_ip"] = sys.argv[2]
        save(cfg)
        print(f"✓ 已更新 PC IP 为 {sys.argv[2]}")
    elif cmd == "discover":
        auto_discover_and_update()
    elif cmd == "check":
        cfg = load()
        ip = cfg["pc_ip"]
        ok = ping(ip, 1000)
        print(f"{ip}  ping: {'✓ 通' if ok else '✗ 不通'}")
        if ok:
            for port_name in ("trigger_port", "video_port"):
                p = cfg.get(port_name, 0)
                if p:
                    c = check_connect(ip, p, 0.5)
                    print(f"{ip}:{p} ({port_name})  TCP: {'✓ 通' if c else '✗ 不通'}")
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
