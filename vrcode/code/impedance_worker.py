"""
ImpedanceWorker — 在 QThread 中打开 eego 阻抗流并定期发送。

由于 eego SDK 同时只允许 1 个 stream，阻抗检测与 EEG 采集互斥：
- EEG 流关闭（停止采集）→ 打开阻抗流
- 阻抗检测结束 → 关闭阻抗流（用户开始 EEG 采集时再打开 EEG 流）

此 Worker 通过信号与主窗口通信：
- impedanceData(impedance_dict, cutoff_kohm)
- sdkStateChanged(connected: bool, channel_count: int)
- errorOccurred(message)
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from PyQt5 import QtCore

from data_worker import EemagineSDK
from impedance_widget import ELECTRODES_BY_STREAM_POS


class ImpedanceWorker(QtCore.QObject):
    """
    在独立 QThread 中运行：
    - 打开 eego 阻抗流（前提：放大器已 init，且当前没有 EEG 流）
    - 每 250ms 读一次最新阻抗数据 → emit 给主窗口
    - 用户切回 EEG 采集时由外部调用 stop() 关闭阻抗流
    """

    impedanceData = QtCore.pyqtSignal(dict)        # {name: kΩ}
    sdkStateChanged = QtCore.pyqtSignal(bool, int) # (connected, channel_count)
    errorOccurred = QtCore.pyqtSignal(str)

    POLL_INTERVAL_MS = 250

    def __init__(self, sdk: EemagineSDK, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self._sdk = sdk
        self._is_running: bool = False

    @QtCore.pyqtSlot()
    def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        try:
            if not self._sdk.open_impedance_stream():
                raise RuntimeError("打开阻抗流失败（请先停止 EEG 采集）")
            ch_count = self._sdk._channel_count
            self.sdkStateChanged.emit(True, ch_count)
            print(f"[ImpedanceWorker] 阻抗流开启，{ch_count} 通道")
        except Exception as e:
            self._is_running = False
            self.sdkStateChanged.emit(False, 0)
            self.errorOccurred.emit(f"阻抗流开启失败: {e}")
            return

        # 循环读取
        while self._is_running:
            try:
                values, n = self._sdk.get_impedance_data()
                if values:
                    # values 是按 stream position 顺序的阻抗（kΩ），index 0..N-1
                    # SDK stream position j → 通道名 ELECTRODES_BY_STREAM_POS[j]
                    mapping: Dict[str, float] = {}
                    for i, v in enumerate(values):
                        if i < len(ELECTRODES_BY_STREAM_POS):
                            name = ELECTRODES_BY_STREAM_POS[i]
                        else:
                            name = f"Ch{i+1}"
                        # -1 表示异常（eemagine 把未连接电极返回大数或 0）
                        if v is None or v < 0 or (isinstance(v, float) and (v != v)):
                            v = -1.0
                        mapping[name] = float(v)
                    self.impedanceData.emit(mapping)
            except Exception as e:
                self.errorOccurred.emit(f"读取阻抗失败: {e}")
                break

            # 简单 sleep（不能阻塞 UI，用 Qt event-loop 不可行，因为我们在子线程）
            time.sleep(self.POLL_INTERVAL_MS / 1000.0)

        # 退出前关闭阻抗流
        try:
            self._sdk.close_impedance_stream()
        except Exception:
            pass
        self.sdkStateChanged.emit(False, 0)
        print("[ImpedanceWorker] 阻抗流关闭")

    @QtCore.pyqtSlot()
    def stop(self) -> None:
        self._is_running = False
