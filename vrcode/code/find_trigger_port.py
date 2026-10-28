"""
扫描本机所有 COM 端口，识别哪个可能是"同步盒 / trigger 设备"。

用法：
    python find_trigger_port.py
"""
import sys
from diag import install_global_hook, log, section
install_global_hook()
section("Trigger 端口扫描")

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    log("pyserial 未安装，请：conda activate VR && pip install pyserial")
    sys.exit(1)

KEYWORDS_TRIGGER = [
    "ch340", "ch341",          # 国产 USB-Serial 常用
    "ftdi", "ft232", "ft2232", # 国外常见
    "cp210", "cp211",          # Silicon Labs
    "pl2303",                  # Prolific
    "usb-serial", "usb serial",
    "trigger", "sync", "eego", "biosemi", "brainproducts", "ant", "neuroscan",
    "同步",
]

def score(name: str, desc: str, hwid: str) -> int:
    s = f"{name} {desc} {hwid}".lower()
    for i, kw in enumerate(KEYWORDS_TRIGGER):
        if kw in s:
            return 100 - i  # 越靠前越像
    return 0

ports = list(list_ports.comports())
if not ports:
    log("=== 系统里一个 COM 端口都没有 ===")
    log("可能原因：")
    log("  1. 同步盒的 USB 转串口驱动没装（最常见：CH340 / FTDI / CP210x）")
    log("  2. 同步盒不是串口型（HID / 音频 / 网卡）—— 这种要换方案")
    log("  3. USB 线只供电、没接数据线（换根线 / 换 USB 口）")
    log("")
    log("请先打开 设备管理器 -> 端口 (COM 和 LPT)，看看有没有这一项。")
    log("如果没有，先按同步盒型号装对应驱动（CH340/FTDI/CP210x 居多）。")
    sys.exit(0)

log(f"=== 发现 {len(ports)} 个 COM 端口 ===\n")
print(f"{'PORT':<8} {'SCORE':<6} {'NAME':<28} DESCRIPTION")
print("-" * 100)

candidates = []
for p in ports:
    sc = score(p.name, p.description, p.hwid)
    candidates.append((sc, p))

# 按 score 降序
candidates.sort(key=lambda x: -x[0])
for sc, p in candidates:
    flag = " <-- 可能是 trigger" if sc >= 80 else (" (低分)" if sc > 0 else "")
    print(f"{p.device:<8} {sc:<6} {p.name[:28]:<28} {p.description}{flag}")
    if p.hwid:
        print(f"         HWID: {p.hwid}")
    print()

best = candidates[0]
if best[0] >= 80:
    log(f"建议先试: {best[1].device}   (score={best[0]})")
    log("接下来手动测一下：")
    log(f"    python -c \"import serial; s=serial.Serial('{best[1].device}', 57600, timeout=1); print('open ok'); s.close()\"")
else:
    log("没有一眼能识别的 trigger 设备。可能：")
    log("  - 同步盒没装驱动（CH340/FTDI/CP210x）")
    log("  - 同步盒不是串口型，请告诉我是哪个型号，我换识别方式")
