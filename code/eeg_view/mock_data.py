"""
EEGView 内部子模块：Mock 数据生成 + 软件 trigger 注入。

原本在 EEGViewWidget 的 enable_mock_data / inject_mock_trigger / _consume_mock_trigger
/ _generate_mock_packets（L595-1379）。封装到独立类后，EEGViewWidget 通过组合使用。
"""

import time
from typing import List, Optional

import numpy as np

from shared_buffer import DataPacket


# ---------------------------------------------------------------------------
# 真实数据特征（用于 mock 校准）
# ---------------------------------------------------------------------------
# 从 data_worker.py 的 startRead / read_chunk 提取的真实数据特征：
#   - voltage 单位: Volt（不是 µV），eego 原始数据已经是 V
#   - 典型 EEG 幅度:
#       * alpha 节律 (8-13Hz, 闭眼枕叶): 10-20 µV → ~15e-6 V
#       * delta 节律 (0.5-4Hz):            50-100 µV → ~70e-6 V
#       * 背景噪声:                       1-2 µV → ~1.5e-6 V
#   - 软件事件由共享 MarkerSession 定位；模拟数据不伪造硬件触发。
#   - sample_counter: 真实是 eego 内部硬件计数器（递增 int），
#                     mock 模拟同样的递增行为便于丢包/对齐检测
#   - timestamp: 样本连续递增；录制时由 MarkerSession 统一赋予采样时间。
# ---------------------------------------------------------------------------
MOCK_ALPHA_AMP_V = 15e-6   # alpha 节律幅度 (V)
MOCK_DELTA_AMP_V = 70e-6   # delta 节律幅度 (V)
MOCK_NOISE_STD_V = 1.5e-6  # 背景噪声 STD (V)
MOCK_ALPHA_FREQ_HZ = 10.0
MOCK_DELTA_FREQ_HZ = 2.0



class MockDataGenerator:
    """
    模拟 32 通道 EEG 数据生成器（无放大器时调试 UI 用）。

    特性（与真实 eego 数据对齐）：
    - 10Hz alpha 节律（顶叶/枕叶较强，幅度 ~15µV）
    - 2Hz delta 慢波（普遍，幅度 ~70µV）
    - 各通道独立的随机噪声（STD ~1.5µV）
    - 软件事件通过共享 MarkerSession 定位，硬件触发保持不可用
    - sample_counter 字段递增（与真实 eego 内部硬件计数器行为一致）
    - 样本数量随单调时钟推进，独立于界面刷新频率
    """

    def __init__(self, channel_names: List[str], sample_rate: float = 1000.0, session=None) -> None:
        self.session = session
        self._clock_ns_start = None
        self._clock_ms_start = None
        self.channel_names = channel_names
        self.sample_rate = sample_rate
        self.enabled: bool = False
        self.t0_ms: int = 0
        self.phase: float = 0.0
        self.sample_counter: int = 0
        self.last_ms: float = 0.0  # mock 数据推进时间戳

    def set_enabled(self, enabled: bool, sample_rate: Optional[float] = None) -> None:
        self._clock_ns_start = self._clock_ms_start = None
        self.enabled = bool(enabled)
        if enabled:
            self.t0_ms = int(time.time() * 1000)
        else:
            self.t0_ms = 0
        self.phase = 0.0
        self.sample_counter = 0
        if sample_rate is not None:
            self.sample_rate = float(sample_rate)
        print(f"[EEG] 模拟数据 {'开启' if enabled else '关闭'} @ {self.sample_rate:.0f} Hz")

    def inject_trigger(self, trigger_value: int, hold_ms: int = 50):
        if self.session is not None:
            return self.session.submit(trigger_value, source="mock")

    def generate_packets(self) -> List[DataPacket]:
        """按实际经过的单调时钟生成样本，避免 40ms 刷新却生成 80ms 数据。"""
        now_ns = time.monotonic_ns()
        now_ms = time.time_ns() / 1e6
        if self._clock_ns_start is None:
            self._clock_ns_start, self._clock_ms_start = now_ns, now_ms
            self.t0_ms = now_ms
        sr = self.sample_rate or 1000.0
        dt_ms = 1000.0 / sr
        target = int((now_ns - self._clock_ns_start) * sr / 1e9) + 1
        n = max(0, target - self.sample_counter)
        if not n:
            return []
        ts_arr = self._clock_ms_start + (self.sample_counter + np.arange(n)) * dt_ms

        # 波形（相对时间 t_sec，从 t0_ms 算起，保持 alpha/δ 相位的连续性）
        t_sec = (ts_arr - self.t0_ms) / 1000.0
        alpha = np.sin(2 * np.pi * MOCK_ALPHA_FREQ_HZ * t_sec) * MOCK_ALPHA_AMP_V
        delta = np.sin(2 * np.pi * MOCK_DELTA_FREQ_HZ * t_sec + self.phase) * MOCK_DELTA_AMP_V

        # 各通道空间分布：alpha 在枕叶强，delta 普遍
        c = len(self.channel_names)
        spatial_alpha = np.zeros(c)
        spatial_delta = np.zeros(c)
        rng = np.random.default_rng(int(now_ms) % 100000)
        for i, name in enumerate(self.channel_names):
            name_u = name.upper()
            if name_u in {"O1", "O2", "OZ", "POZ", "PZ", "P3", "P4", "P7", "P8"}:
                spatial_alpha[i] = 1.0
            elif name_u in {"M1", "M2", "A1", "A2", "VREF", "REF", "GND"}:
                spatial_alpha[i] = 0.0  # 参考通道静音
                spatial_delta[i] = 0.0
            else:
                spatial_alpha[i] = 0.3
            spatial_delta[i] = max(spatial_delta[i], 0.5)

        # vals: (n, c)，单位 Volt（与真实 eego 输出一致）
        noise = rng.normal(0, MOCK_NOISE_STD_V, (n, c))
        vals = (
            noise
            + alpha[:, None] * spatial_alpha[None, :]
            + delta[:, None] * spatial_delta[None, :]
        )

        self.phase += 0.1
        packets = []
        for i in range(n):
            trigger_val = 0
            packets.append(DataPacket(
                timestamp=int(ts_arr[i]),
                voltage=vals[i].tolist(),
                trigger=trigger_val,
                # 真实 eego 的 sample_counter 是硬件内部递增的 int，
                # mock 模拟同样的行为以保持丢包/对齐检测逻辑的可用性
                sample_counter=self.sample_counter + i,
            ))
        self.sample_counter += n
        if self.session is not None:
            self.session.append_batch(packets, now_ms, now_ns)
        return packets

