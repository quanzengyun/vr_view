import ctypes
import time
import threading
from ctypes import byref, POINTER, c_int, c_double, c_ulonglong
from typing import List, Optional, Callable, Tuple

import numpy as np

from PyQt5 import QtCore

from shared_buffer import SharedBuffer, DataPacket
from marker_session import MarkerSession


# 业务侧真正需要的 32 个 EEG 导联（按显示顺序）
STANDARD_32_CHANNELS: List[str] = [
    "Fp1", "Fpz", "Fp2",
    "F7", "F3", "Fz", "F4", "F8",
    "FC5", "FC1", "FC2", "FC6",
    "T7", "C3", "Cz", "C4", "T8",
    "M1", "CP5", "CP1", "CP2", "CP6", "M2",
    "P7", "P3", "Pz", "P4", "P8",
    "POz", "O1", "Oz", "O2",
]

# eemagine 设备通道索引约定：
#   - index 0..63 : 64 路 EEG 参考通道
#   - index 64..67: 4 路双极通道（EOG/EMG 等）
#   - index 68    : Trigger 通道（marker 数字脉冲）
#   - index 69    : Sample Counter 通道
#   - index 70..72: 3 路加速度计
#   - index 73..75: 3 路陀螺仪
#   - index 76..78: 3 路磁力计
# 这里把 64 路 EEG 中与 STANDARD_32_CHANNELS 对应的索引挑出来；
# 通道顺序按照 eemagine 参考系统常见布局（与 ANT 10-20 系统一致）。
EEG64_TO_STD32_INDEX: List[int] = [
    # Fp1, Fpz, Fp2
    0, 1, 2,
    # F7, F3, Fz, F4, F8
    3, 4, 5, 6, 7,
    # FC5, FC1, FC2, FC6
    8, 9, 10, 11,
    # T7, C3, Cz, C4, T8
    12, 13, 14, 15, 16,
    # M1, CP5, CP1, CP2, CP6, M2
    17, 18, 19, 20, 21, 22,
    # P7, P3, Pz, P4, P8
    23, 24, 25, 26, 27,
    # POz, O1, Oz, O2
    28, 29, 30, 31,
]


# ============================================================================
# ctypes structures
# ============================================================================
class AmplifierInfo(ctypes.Structure):
    _fields_ = [
        ("id", c_int),
        ("serial", ctypes.c_char * 64)
    ]


class ChannelInfo(ctypes.Structure):
    _fields_ = [
        ("index", c_int),
        ("type", c_int)
    ]


# ============================================================================
# EemagineSDK — ctypes wrapper for wrapper.h C API
# ============================================================================
class EemagineSDK:
    """
    ctypes wrapper for eemagine SDK (wrapper.h C API).
    Uses the same DLL: E:\\VR展示\\eego-SDK.dll
    """

    CHANNEL_TYPE_REFERENCE = 0
    CHANNEL_TYPE_BIPOLAR = 1
    CHANNEL_TYPE_ACCELEROMETER = 2
    CHANNEL_TYPE_GYROSCOPE = 3
    CHANNEL_TYPE_MAGNETOMETER = 4
    CHANNEL_TYPE_TRIGGER = 5
    CHANNEL_TYPE_SAMPLE_COUNTER = 6
    CHANNEL_TYPE_IMPEDANCE_REFERENCE = 7
    CHANNEL_TYPE_IMPEDANCE_GROUND = 8

    def __init__(self, dll_path: str):
        self._dll_path = dll_path
        self._lib: Optional[ctypes.CDLL] = None
        self._amplifier_id: int = -1
        self._stream_id: int = -1
        self._impedance_stream_id: int = -1
        self._channel_count: int = 0
        self._initialized: bool = False
        self._amp_info = (AmplifierInfo * 8)()
        self._channel_info = (ChannelInfo * 128)()

    def _check_error(self, result: int) -> int:
        if result < 0:
            errors = {
                -1: "EEMAGINE_SDK_NOT_CONNECTED",
                -2: "EEMAGINE_SDK_ALREADY_EXISTS",
                -3: "EEMAGINE_SDK_NOT_FOUND",
                -4: "EEMAGINE_SDK_INCORRECT_VALUE",
                -5: "EEMAGINE_SDK_UNKNOWN"
            }
            raise RuntimeError(f"eemagine SDK error {result}: {errors.get(result, 'Unknown')}")
        return result

    def init(self) -> bool:
        try:
            import os
            if not os.path.exists(self._dll_path):
                raise RuntimeError(f"eego-SDK.dll not found at: {self._dll_path}")

            print(f"Loading DLL from: {self._dll_path}")
            self._lib = ctypes.CDLL(self._dll_path)
            print("DLL loaded successfully")

            self._lib.eemagine_sdk_init.restype = None
            self._lib.eemagine_sdk_exit.restype = None
            self._lib.eemagine_sdk_get_version.restype = c_int

            self._lib.eemagine_sdk_get_amplifiers_info.argtypes = [POINTER(AmplifierInfo), c_int]
            self._lib.eemagine_sdk_get_amplifiers_info.restype = c_int

            self._lib.eemagine_sdk_open_eeg_stream.argtypes = [
                c_int, c_int, c_double, c_double, c_ulonglong, c_ulonglong
            ]
            self._lib.eemagine_sdk_open_eeg_stream.restype = c_int

            # ----- 阻抗流 API -----
            # int eemagine_sdk_open_impedance_stream(int amplifier_id, unsigned long long ref_mask);
            self._lib.eemagine_sdk_open_impedance_stream.argtypes = [c_int, c_ulonglong]
            self._lib.eemagine_sdk_open_impedance_stream.restype = c_int

            self._lib.eemagine_sdk_get_stream_channel_count.argtypes = [c_int]
            self._lib.eemagine_sdk_get_stream_channel_count.restype = c_int

            self._lib.eemagine_sdk_get_stream_channel_list.argtypes = [
                c_int, POINTER(ChannelInfo), c_int
            ]
            self._lib.eemagine_sdk_get_stream_channel_list.restype = c_int

            self._lib.eemagine_sdk_prefetch.argtypes = [c_int]
            self._lib.eemagine_sdk_prefetch.restype = c_int

            self._lib.eemagine_sdk_get_data.argtypes = [c_int, POINTER(c_double), c_int]
            self._lib.eemagine_sdk_get_data.restype = c_int

            self._lib.eemagine_sdk_close_stream.argtypes = [c_int]
            self._lib.eemagine_sdk_close_stream.restype = c_int

            self._lib.eemagine_sdk_close_amplifier.argtypes = [c_int]
            self._lib.eemagine_sdk_close_amplifier.restype = c_int

            self._lib.eemagine_sdk_init()

            n_amps = self._lib.eemagine_sdk_get_amplifiers_info(self._amp_info, 8)
            if n_amps <= 0:
                raise RuntimeError("No amplifiers found")

            self._amplifier_id = self._amp_info[0].id
            self._initialized = True
            return True

        except Exception as e:
            print(f"SDK init error: {e}")
            self._initialized = False
            return False

    def open_stream(
        self,
        sampling_rate: int = 1000,
        reference_range: float = 1.0,
        bipolar_range: float = 4.0,
        ref_mask: int = 0xFFFFFFFFFFFFFFFF,
        bip_mask: int = 0xFFFFFFFFFFFFFFFF,
    ) -> bool:
        if not self._initialized:
            return False
        try:
            self._stream_id = self._check_error(
                self._lib.eemagine_sdk_open_eeg_stream(
                    self._amplifier_id,
                    sampling_rate,
                    c_double(reference_range),
                    c_double(bipolar_range),
                    c_ulonglong(ref_mask),
                    c_ulonglong(bip_mask)
                )
            )
            self._channel_count = self._lib.eemagine_sdk_get_stream_channel_count(self._stream_id)
            self._lib.eemagine_sdk_get_stream_channel_list(
                self._stream_id, self._channel_info, 128
            )
            return True
        except Exception as e:
            print(f"Open stream error: {e}")
            return False

    def get_data(self, max_samples: int = 0) -> tuple:
        """
        Get a batch of samples from the stream.

        Args:
            max_samples: 限制本次最多返回的 sample 数；0 表示读光缓存。

        Returns:
            (samples, channel_count, channel_list)
            samples: list of lists, each inner list has n_channels values (Volts).
            channel_count: 放大器返回的总通道数（包含 trigger/samp_cnt 等）
            channel_list: list of (index, type) for each stream channel (in stream order)
        """
        if not self._initialized or self._stream_id < 0:
            return [], 0, []

        try:
            # Step 1: prefetch returns the number of bytes available
            bytes_available = self._lib.eemagine_sdk_prefetch(self._stream_id)
            if bytes_available <= 0:
                return [], 0, []

            # Step 2: get channel count and list (these never change after open_stream)
            n_channels = self._lib.eemagine_sdk_get_stream_channel_count(self._stream_id)
            if n_channels <= 0:
                return [], 0, []

            channel_list = []
            for ci in self._channel_info[:n_channels]:
                channel_list.append((ci.index, ci.type))

            double_count = bytes_available // ctypes.sizeof(ctypes.c_double)
            actual_samples_available = double_count // n_channels
            if actual_samples_available <= 0:
                return [], 0, channel_list

            # Step 3: 限制每次最多取 max_samples 个 sample（剩余留给下次）
            if max_samples > 0:
                actual_samples = min(actual_samples_available, max_samples)
            else:
                actual_samples = actual_samples_available
            actual_bytes = actual_samples * n_channels * ctypes.sizeof(ctypes.c_double)

            buffer = (ctypes.c_double * (actual_samples * n_channels))()
            bytes_written = self._lib.eemagine_sdk_get_data(
                self._stream_id, buffer, actual_bytes
            )
            if bytes_written <= 0:
                return [], 0, channel_list
            # 在 Python 大批量转换之前记录主机接收到数据的时刻，避免把转换耗时计入锚点。
            self._last_batch_received_ns = time.monotonic_ns()
            self._last_batch_received_ms = time.time_ns() / 1e6

            # Step 4: 将 C 数组按 sample-major 解析（与 C++ buffer.h L88 一致）
            # C++ getSample(channel, sample): _data[channel + sample * channel_count]
            # 即：buffer[sample * channel_count + channel] = channel 的 sample 数据
            # 对照 C++ Thread.cpp L511:
            #   for x in range(channel_count * sample_count):
            #       gm_pBuffer[x] = buf._data[x]  (sample-major, x = sample*C + ch)
            # 注：预分配 list 容量 + 索引赋值，避免大 batch 时 100w+ 次 append 慢
            samples = [None] * actual_samples
            for s in range(actual_samples):
                row_start = s * n_channels
                sample_data = [buffer[row_start + ch] for ch in range(n_channels)]
                samples[s] = sample_data

            return samples, n_channels, channel_list

        except Exception as e:
            print(f"Get data error: {e}")
            return [], 0, []

    def close(self) -> None:
        if not self._initialized:
            return
        try:
            if self._stream_id >= 0:
                self._lib.eemagine_sdk_close_stream(self._stream_id)
                self._stream_id = -1
            if self._impedance_stream_id >= 0:
                self._lib.eemagine_sdk_close_stream(self._impedance_stream_id)
                self._impedance_stream_id = -1
            if self._amplifier_id >= 0:
                self._lib.eemagine_sdk_close_amplifier(self._amplifier_id)
                self._amplifier_id = -1
            self._lib.eemagine_sdk_exit()
            self._initialized = False
        except Exception as e:
            print(f"Close error: {e}")

    # ---------- 阻抗流 API ----------

    def open_impedance_stream(self, ref_mask: int = 0xFFFFFFFFFFFFFFFF) -> bool:
        """
        打开阻抗流。注意：eego SDK 同时只允许一个流存在，因此必须在 EEG 流
        关闭后调用，或在使用 EEG 流的同时关闭 EEG 流。
        """
        if not self._initialized:
            return False
        try:
            if self._stream_id >= 0:
                # 先关闭 EEG 流，腾出 stream slot
                self._lib.eemagine_sdk_close_stream(self._stream_id)
                self._stream_id = -1

            self._impedance_stream_id = self._check_error(
                self._lib.eemagine_sdk_open_impedance_stream(
                    self._amplifier_id, c_ulonglong(ref_mask)
                )
            )
            self._channel_count = self._lib.eemagine_sdk_get_stream_channel_count(
                self._impedance_stream_id
            )
            self._lib.eemagine_sdk_get_stream_channel_list(
                self._impedance_stream_id, self._channel_info, 128
            )
            return True
        except Exception as e:
            print(f"Open impedance stream error: {e}")
            return False

    def get_impedance_data(self) -> Tuple[List[float], int]:
        """
        读取最新一批阻抗数据。

        Returns:
            (imp_values, channel_count)
            imp_values: list[float] 单位 kΩ（已从 Ohm 换算）
            channel_count: 总通道数（仅参考通道有效）
        """
        if not self._initialized or self._impedance_stream_id < 0:
            return [], 0
        try:
            bytes_available = self._lib.eemagine_sdk_prefetch(self._impedance_stream_id)
            if bytes_available <= 0:
                return [], self._channel_count

            n_channels = self._lib.eemagine_sdk_get_stream_channel_count(
                self._impedance_stream_id
            )
            if n_channels <= 0:
                return [], 0

            double_count = bytes_available // ctypes.sizeof(ctypes.c_double)
            n_samples = double_count // n_channels
            if n_samples <= 0:
                return [], n_channels

            buffer = (ctypes.c_double * (n_samples * n_channels))()
            bytes_written = self._lib.eemagine_sdk_get_data(
                self._impedance_stream_id, buffer, n_samples * n_channels * 8
            )
            if bytes_written <= 0:
                return [], n_channels

            # 取每个通道的最新一个 sample（最后一帧），单位 Ohm → kΩ
            latest = []
            for ch in range(n_channels):
                latest.append(buffer[(n_samples - 1) * n_channels + ch] / 1000.0)
            return latest, n_channels
        except Exception as e:
            print(f"Get impedance data error: {e}")
            return [], 0

    def close_impedance_stream(self) -> None:
        if self._impedance_stream_id >= 0:
            try:
                self._lib.eemagine_sdk_close_stream(self._impedance_stream_id)
            except Exception:
                pass
            self._impedance_stream_id = -1


# ============================================================================
# DataWorker — runs in a QThread, reads from eemagine SDK and pushes to buffer
# ============================================================================
class DataWorker(QtCore.QObject):
    """
    Reads EEG from eemagine SDK via ctypes and pushes DataPackets into SharedBuffer.
    Must run on its own QThread (moveToThread).
    """

    acquisitionStopped = QtCore.pyqtSignal()
    errorOccurred = QtCore.pyqtSignal(str)
    experimentDurationChanged = QtCore.pyqtSignal(int)
    sdkConnected = QtCore.pyqtSignal(int, int)        # (channel_count, sample_rate)
    channelNamesAvailable = QtCore.pyqtSignal(list)    # [str, ...]

    def __init__(
        self,
        sample_rate: int = 1000,
        dll_path: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._is_running = False
        self._stop_requested = threading.Event()
        self.markers = MarkerSession()
        self._sample_rate = sample_rate
        self._stream_sample_rate = None
        self._start_system_time_ms = 0
        self._total_samples = 0
        self._experiment_start_time = 0.0
        self._last_duration_update = 0

        self.shared_buffer = SharedBuffer(max_capacity=15000)  # 500Hz * 30s，覆盖 SDK 缓存积压

        self._dll_path = dll_path or r"E:\VR展示\eego-SDK.dll"
        self._sdk: Optional[EemagineSDK] = None
        self._is_initialized = False

        # 通道过滤：SDK 返回 N 通道 -> 只保留 32 个 EEG 导联
        self._channel_index_map: List[int] = list(EEG64_TO_STD32_INDEX)
        self._active_channel_names: List[str] = list(STANDARD_32_CHANNELS)

        # 新增：trigger / sample_counter 通道的 stream position（在 SDK 初始化时被覆盖）
        # 硬件 trigger 通道由 eego 放大器原生提供，无需额外硬件；
        # sample_counter 用于检测丢包和与 wall-clock 对齐。
        self._trigger_stream_pos: Optional[int] = None
        self._sample_counter_pos: Optional[int] = None
        self._event_callbacks: List[Callable] = []
        self._first_sample_counter = 0
        self._sync_wallclock_ms = 0

    def get_anchor_ms(self):
        return self._sync_wallclock_ms

    def inject_marker(self, label, arrival_sample_counter=None, **event):
        """接收软件事件；旧的相对/硬件计数器参数不再用于定位。"""
        return self.markers.submit(label, **event)

    def request_start(self):
        self._stop_requested.clear()

    def request_stop(self):
        # startRead 占用工作线程的事件循环，停止请求不能只依赖 queued slot。
        self._stop_requested.set()
        self.markers.stop_accepting()

    def set_event_marker(self, marker_type: str, intensity: float = 1.0) -> None:
        """触发事件标记，通知所有回调。"""
        for callback in self._event_callbacks:
            try:
                callback(marker_type, intensity)
            except Exception:
                pass

    def register_event_callback(self, callback: Callable) -> None:
        self._event_callbacks.append(callback)

    @QtCore.pyqtSlot(int)
    def set_sample_rate(self, sample_rate: int) -> None:
        if sample_rate > 0:
            self._sample_rate = int(sample_rate)

    # ---------- Qt slots ----------

    @QtCore.pyqtSlot()
    def initializeSDK(self) -> None:
        """
        Initialize eemagine SDK (called in worker thread via QThread.started).
        """
        try:
            print(f"Initializing eemagine SDK with DLL: {self._dll_path}")

            self._sdk = EemagineSDK(self._dll_path)
            if not self._sdk.init():
                raise RuntimeError("Failed to initialize eemagine SDK")

            print("eemagine SDK initialized, opening stream...")
            if not self._sdk.open_stream(sampling_rate=self._sample_rate,
                                         reference_range=1.0,
                                         bipolar_range=4.0):
                raise RuntimeError("Failed to open EEG stream")

            self._is_initialized = True
            self._stream_sample_rate = self._sample_rate

            # ---- 通道过滤：根据 channel type 选出所有 reference + bipolar 通道 ----
            n_total = self._sdk._channel_count
            print(f"[通道诊断] n_total={n_total}, 总通道列表:")
            channel_by_pos = []  # (stream_pos, index, type, type_name)
            for pos, ci in enumerate(self._sdk._channel_info[:n_total]):
                t = ci.type
                tname = {
                    0: 'ref', 1: 'bip', 2: 'acc', 3: 'gyro',
                    4: 'mag', 5: 'trig', 6: 'samp_cnt',
                    7: 'imp_ref', 8: 'imp_gnd',
                }.get(t, f'unknown({t})')
                channel_by_pos.append((pos, ci.index, t, tname))
                if pos < 8 or pos >= n_total - 4:
                    print(f"  [ch{pos}] index={ci.index:3d} type={t:2d}({tname})")
                elif pos == 8:
                    print("  ... (中间省略) ...")

            # 收集所有 reference + bipolar 通道的 stream position（EEG 通道）
            # keep_stream_pos[i] = stream_position of the i-th EEG channel to keep
            eeg_stream_positions = [
                pos for pos, _, t, _ in channel_by_pos
                if t in (0, 1)  # REFERENCE=0, BIPOLAR=1
            ]
            # 按 SDK 通道类型查找原始硬件触发及计数器，不假定触发值的单位/编码。
            trigger_positions = [
                pos for pos, _, t, _ in channel_by_pos if t == 5
            ]
            sample_counter_positions = [
                pos for pos, _, t, _ in channel_by_pos if t == 6
            ]
            self._trigger_stream_pos = trigger_positions[0] if trigger_positions else None
            self._sample_counter_pos = sample_counter_positions[0] if sample_counter_positions else None
            print(f"[通道过滤] EEG 通道(stream position)={len(eeg_stream_positions)}: {eeg_stream_positions}")
            print(f"[通道过滤] Trigger stream position = {self._trigger_stream_pos}, "
                  f"sample_counter stream position = {self._sample_counter_pos}")

            # 取前 32 个 EEG 通道
            if len(eeg_stream_positions) >= 32:
                self._channel_index_map = eeg_stream_positions[:32]
            else:
                self._channel_index_map = eeg_stream_positions
                print(f"[通道过滤] 警告: EEG 通道数({len(eeg_stream_positions)})不足 32")
            self._active_channel_names = [
                STANDARD_32_CHANNELS[i]
                for i in range(len(self._channel_index_map))
                if i < len(STANDARD_32_CHANNELS)
            ]
            print(f"[通道过滤] 保留 {len(self._channel_index_map)} 个 EEG 导联: {self._active_channel_names}")

            self.sdkConnected.emit(len(self._active_channel_names), self._sample_rate)
            self.channelNamesAvailable.emit(self._active_channel_names)
            print(f"eemagine SDK connected: {n_total} channels @ {self._sample_rate} Hz")

        except Exception as e:
            msg = f"SDK 初始化异常: {e}"
            self.errorOccurred.emit(msg)
            print(msg)
            import traceback
            traceback.print_exc()
            self._is_initialized = False

    @QtCore.pyqtSlot()
    def startRead(self):
        """采集线程读取；录制缓存独立于绘图刷新，结束后通知界面保存。"""
        if self._is_running:
            return
        self._is_running = True
        self._total_samples = 0
        self._experiment_start_time = time.time()
        self._last_duration_update = -1
        try:
            if not self.markers.active or self._stop_requested.is_set():
                return
            if not self._is_initialized or self._sdk is None:
                raise RuntimeError("SDK 尚未连接，无法开始真实采集")
            self._prepare_stream_rate()
            while not self._stop_requested.is_set():
                samples, n_channels, _ = self._sdk.get_data(max_samples=0)
                if samples:
                    packets = self._convert_samples(samples, n_channels)
                    self.shared_buffer.push_many(packets)
                else:
                    time.sleep(0.005)
                elapsed = int(time.time() - self._experiment_start_time)
                if elapsed != self._last_duration_update:
                    self._last_duration_update = elapsed
                    self.experimentDurationChanged.emit(elapsed)
        except Exception as exc:
            self.errorOccurred.emit(f"数据读取异常: {exc}")
        finally:
            self._is_running = False
            self.markers.finish()
            self.acquisitionStopped.emit()

    def _prepare_stream_rate(self):
        # 修改 Python 中的采样率不会改变已打开的 SDK 流，开始新录制前必须重开。
        if self._stream_sample_rate is None or self._stream_sample_rate == self._sample_rate:
            return
        sdk = self._sdk
        sdk._lib.eemagine_sdk_close_stream(sdk._stream_id)
        sdk._stream_id = -1
        if not sdk.open_stream(sampling_rate=self._sample_rate, reference_range=1.0, bipolar_range=4.0):
            raise RuntimeError(f"放大器无法以 {self._sample_rate} Hz 打开采集流")
        channels = list(sdk._channel_info[:sdk._channel_count])
        eeg = [i for i, ch in enumerate(channels) if ch.type in (0, 1)][:32]
        if len(eeg) != len(self._active_channel_names):
            raise RuntimeError("切换采样率后 EEG 通道数量变化，请重新连接设备")
        self._channel_index_map = eeg
        self._trigger_stream_pos = next((i for i, ch in enumerate(channels) if ch.type == 5), None)
        self._sample_counter_pos = next((i for i, ch in enumerate(channels) if ch.type == 6), None)
        self._stream_sample_rate = self._sample_rate

    def _convert_samples(self, samples, n_channels, received_ms=None, received_monotonic_ns=None):
        """只解析原始硬件值；软件事件定位与录制缓存交给 MarkerSession。"""
        received_ms = (getattr(self._sdk, "_last_batch_received_ms", None)
                       if received_ms is None else received_ms)
        received_monotonic_ns = (getattr(self._sdk, "_last_batch_received_ns", None)
                                 if received_monotonic_ns is None else received_monotonic_ns)
        received_ms = time.time_ns() / 1e6 if received_ms is None else received_ms
        received_monotonic_ns = time.monotonic_ns() if received_monotonic_ns is None else received_monotonic_ns
        keep = [i for i in self._channel_index_map if i < n_channels]
        packets = []
        for row in samples:
            counter = (int(row[self._sample_counter_pos])
                       if self._sample_counter_pos is not None else 0)
            hardware = (float(row[self._trigger_stream_pos])
                        if self._trigger_stream_pos is not None else None)
            packets.append(DataPacket(timestamp=0, voltage=[row[i] for i in keep],
                                      sample_counter=counter, hardware_trigger=hardware))
        self.markers.append_batch(packets, received_ms, received_monotonic_ns)
        if packets and packets[0].sample_index is not None:
            self._total_samples = packets[-1].sample_index + 1
            self._sync_wallclock_ms = packets[0].timestamp - packets[0].sample_index * 1000.0 / self._sample_rate
        return packets

    @QtCore.pyqtSlot()
    def stopRead(self):
        self.request_stop()

    def _read_from_device_batch(self):
        if not self._sdk:
            return []
        samples, n_channels, _ = self._sdk.get_data(max_samples=0)
        return self._convert_samples(samples, n_channels) if samples else []
