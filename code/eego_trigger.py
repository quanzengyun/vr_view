"""
eego 硬件 trigger 打标模块

功能：通过串口发送字节触发脉冲，写入 eego 放大器的 trigger 通道（index 68）。
即使没有这个串口打标硬件，eego 放大器本身的 trigger 通道也在 stream 里，
marker 可以通过其他方式写入（如 SDK API 或外部 LPT）。本模块是 V2.m 的
serial_sendLabel.m 的 Python 等价物，仅用于硬件打标可选路径。

Marker 编码（与 V2.m 一致，便于比对旧数据）：
    X (target)  第二个字符重复:  X1->11, X2->22, X3->33, ..., X8->88
    G (standard) 第二个字符:     G1->1,  G2->2,  G3->3,  ..., G8->8
    含 Y        -> 89 (按键反应)
"""

import threading
from typing import Optional

try:
    import serial  # pyserial
    _HAS_SERIAL = True
except ImportError:
    _HAS_SERIAL = False

from diag import log, log_exc, debug


class EegoTrigger:
    """串口打标：把 marker 字节发给 eego 放大器 trigger 通道。

    使用前请确认：
    1. 你的放大器上有 USB-to-Serial 触发器（COM 端口）连接到 PC
    2. 端口号、波特率与放大器要求一致（V2.m 用 COM5/57600）
    3. 没硬件时调用 send() 是 no-op，不会报错
    """

    def __init__(self, port: str = "COM5", baudrate: int = 57600):
        self.port = port
        self.baudrate = baudrate
        self._ser: Optional["serial.Serial"] = None
        self._lock = threading.Lock()
        self._enabled = False
        self._try_open()

    def _try_open(self) -> None:
        """尝试打开串口；失败则禁用（send 变 no-op）。"""
        if not _HAS_SERIAL:
            log("[EegoTrigger] pyserial 未安装，硬件打标已禁用（pip install pyserial）")
            return
        try:
            self._ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
            self._enabled = True
            log(f"[EegoTrigger] 串口打标已启用: {self.port} @ {self.baudrate}")
        except Exception as e:
            self._enabled = False
            log(f"[EegoTrigger] 串口 {self.port} 打开失败: {e} → 硬件打标已禁用（软件 marker 仍可用）")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, label: int) -> None:
        """发送 trigger 标签（与 V2.m serial_sendLabel.m 一致）。

        V2.m 发送 [head=0x00, label] 两个字节：
            head  = 0x00
            label = 0x00-0xFF (V2.m 里 target 1~88, standard 1~8, response 89)
        这里保持字节级兼容——发同样的 2 字节序列给同一接收端。
        """
        if not self._enabled or self._ser is None:
            debug(f"[EegoTrigger] send({label}) 被跳过：触发器未启用（串口未打开或无硬件）")
            return
        with self._lock:
            try:
                head = 0x00
                self._ser.write(bytes([head, label & 0xFF]))
                debug(f"[EegoTrigger] send label={label} (0x{label:02X}) OK")
            except Exception:
                log_exc(f"[EegoTrigger] 写入失败 label={label}，后续 send 将被禁用:")
                self._enabled = False

    def close(self) -> None:
        """关闭串口。"""
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                log_exc("[EegoTrigger] 关闭串口异常:")
            self._ser = None
            self._enabled = False

    # ---- 便捷构造 ----
    def marker_from_event(self, event_type: str, channel: int) -> int:
        """根据 VR 端 marker 算出要发的字节（与 PICO.cs (247-252) 和 V2.m 保持一致）。

        PICO.cs 打标规则（blockCue命中=target, 其余=standard）：
            X1-X8 (target)   -> 字符重复 2 次，如 '3' -> '33' -> 33
            G1-G8 (standard) -> ASCII(c) - 48，如 '3' -> ASCII 51 - 48 = 3
            Y  (被试按键)    -> 固定 89
        V2.m 打标逻辑 (line 102-120) 与上述一致：
            X -> 字符重复2次（11,22,33...88）
            G -> ASCII-48   （1,2,3...8）
        因此:
            target  : 11, 22, 33, 44, 55, 66, 77, 88  (channel * 11)
            standard: 1, 2, 3, 4, 5, 6, 7, 8            (channel)
            response: 89

        Args:
            event_type: 'target' / 'standard' / 'response' (Y)
            channel: 1~8

        Returns:
            0~255 的整数；非法输入返回 0
        """
        et = event_type.lower() if event_type else ""
        if et == "response" or et == "y":
            return 89
        if not (1 <= channel <= 8):
            return 0
        if et == "target":
            return channel * 11   # X1-X8 → 11, 22, 33, ..., 88 (PICO.cs 247-252, V2.m 102-110)
        elif et == "standard":
            return channel        # G1-G8 → 1, 2, 3, 4, 5, 6, 7, 8  (PICO.cs 247-252, V2.m 115-120)
        return 0


# 全局单例（延迟初始化，使用前调用 init_trigger）
_trigger: Optional[EegoTrigger] = None


def init_trigger(port: str = "COM5", baudrate: int = 57600) -> EegoTrigger:
    """初始化全局 trigger 单例，返回实例。多次调用幂等。"""
    global _trigger
    if _trigger is None:
        _trigger = EegoTrigger(port=port, baudrate=baudrate)
    return _trigger


def get_trigger() -> Optional[EegoTrigger]:
    """获取全局 trigger 单例，未初始化则返回 None。"""
    return _trigger


def shutdown_trigger() -> None:
    """关闭全局 trigger。"""
    global _trigger
    if _trigger is not None:
        _trigger.close()
        _trigger = None
