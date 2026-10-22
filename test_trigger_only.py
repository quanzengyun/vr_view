"""纯串口打标测试：直接调 eego_trigger，不启动 Qt/SDK/EEG。
按 Ctrl+C 停止。
"""
import os, time, sys
os.environ['PYTHONIOENCODING'] = 'utf-8'

# UTF-8 输出
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import serial

PORT = "COM5"
BAUD = 57600

print("=" * 60)
print(f"测试 {PORT} @ {BAUD} 8N1 的真实写/读")
print("=" * 60)

try:
    s = serial.Serial(PORT, BAUD, timeout=0.2)
    print(f"[OK] {PORT} 已打开")

    # 测 3 个 label：11 (target), 1 (standard), 89 (response)
    for label, name in [(11, "target"), (1, "standard"), (89, "response")]:
        # V2.m 协议: head=0x00, label=byte
        s.write(bytes([0x00, label & 0xFF]))
        s.flush()
        time.sleep(0.05)
        n = s.in_waiting
        rx = s.read(n) if n else b''
        print(f"  [{time.strftime('%H:%M:%S')}] send label={label:3d} ({name:8s}) → rx {n} bytes {rx.hex() if rx else ''}")

    s.close()
    print("[OK] 测试完成。如果没看到硬件反应（放大器指示灯/接收器），")
    print("     是因为 COM5 是你刚找到的，但接收端可能接在另一台设备上。")

except serial.SerialException as e:
    print(f"[ERR] 打开 {PORT} 失败: {e}")
    sys.exit(1)
except KeyboardInterrupt:
    print("\n[STOP] 用户中断")
