"""在线 ERP：1–10 Hz FIR、固定坏导球面插值、重参考（全通道平均参考）。

原始 EEG/EDF 不修改。FIR 群延迟通过输出样本的原始时间补偿，而非移动标签。
坏导初始化后固定插值矩阵；连续数据先重参考，再提取试次。不执行ICA或眼电成分去除。
"""
from dataclasses import dataclass, asdict
import threading
import numpy as np
from scipy.signal import lfilter


@dataclass(frozen=True)
class ERPPreprocessingConfig:
    # 正式与空采使用同一滤波/分段设置；调试仅跳过坏导处理和试次剔除。
    empty_acquisition_debug: bool = False
    low_hz: float = 1.0
    high_hz: float = 10.0
    calibration_seconds: float = 10.0
    minimum_calibration_seconds: float = 3.0
    amplitude_uv: float = 100.0
    variance_uv2: float = 1000.0
    bad_window_samples: int = 500
    bad_overlap: float = 0.5
    bad_amplitude_uv: float = 200.0
    bad_special_amplitude_uv: float = 150.0
    bad_amplitude_ratio: float = 0.7
    bad_flat_ratio: float = 0.95
    bad_flat_range_uv: float = 20.0


def detect_bad_channels(data_volts, channel_names, config):
    """Port of detect_bad_electrodes.m: channels 27–30 use the special threshold."""
    data = np.asarray(data_volts) * 1e6  # time x channels, microvolts
    window = config.bad_window_samples
    # 窗长沿用MATLAB的500个采样点；1000 Hz下为0.5秒，时长随采样率变化。
    step = int(np.floor(window * (1 - config.bad_overlap) + 0.5))

    starts = range(0, len(data) - window + 1, step)
    peaks = np.array([np.max(np.abs(data[s:s+window]), axis=0) for s in starts])
    ranges = np.array([np.ptp(data[s:s+window], axis=0) for s in starts])
    thresholds = np.full(data.shape[1], config.bad_amplitude_uv)
    thresholds[26:30] = config.bad_special_amplitude_uv
    # 每通道独立统计异常窗口比例；通道索引26:30对应MATLAB的第27～30通道。
    high = np.mean(peaks > thresholds, axis=0)
    flat = np.mean(ranges < config.bad_flat_range_uv, axis=0)
    bad = (high > config.bad_amplitude_ratio) | (flat > config.bad_flat_ratio)
    stats = [dict(channel=name, high_amplitude_ratio=float(high[i]),
                  flat_signal_ratio=float(flat[i]), bad=bool(bad[i]))
             for i, name in enumerate(channel_names)]
    return np.flatnonzero(bad).tolist(), stats


def build_interpolation_model(filtered_data, channel_names, sample_rate, config):
    """Build only a spherical interpolation matrix from filtered calibration data."""
    import mne
    # Streaming FIR has no zero-padded end edge. Ignore its initial transient.
    edge = int(round(2 * sample_rate))
    detection = np.asarray(filtered_data)[edge:]
    bad, stats = detect_bad_channels(detection, channel_names, config)
    channels = len(channel_names)
    interpolation = np.eye(channels)
    if bad:
        if channels - len(bad) < 4:
            raise ValueError("坏导检测后有效通道不足4个，不能可靠球面插值；请检查电极或检测阈值")
        info = mne.create_info(channel_names, sample_rate, "eeg")
        probe = mne.io.RawArray(np.eye(channels), info, verbose="ERROR")
        # 对单位矩阵插值得到固定空间变换矩阵，后续无需逐批重建球面插值模型。
        probe.set_montage(mne.channels.make_standard_montage("standard_1020"),
                          match_case=False, on_missing="raise")
        probe.info['bads'] = [channel_names[i] for i in bad]
        probe.interpolate_bads(reset_bads=True, method=dict(eeg='spline'), verbose="ERROR")
        interpolation = probe.get_data()
    return dict(interpolation=interpolation, bad_channels=[channel_names[i] for i in bad],
                bad_channel_stats=stats)


class OnlineERPPreprocessor:
    def __init__(self, channel_names, sample_rate=1000.0, config=None, model_function=None):
        import mne
        self.channel_names = list(channel_names)
        self.sample_rate = float(sample_rate)
        self.config = config or ERPPreprocessingConfig()
        self.continuous_output = True
        self._model_function = model_function or build_interpolation_model
        self._lock = threading.RLock()
        self._generation = 0
        self.taps = mne.filter.create_filter(None, self.sample_rate,
                    self.config.low_hz, self.config.high_hz, phase="zero", fir_design="firwin", verbose="ERROR")
        self.delay_samples = (len(self.taps) - 1) // 2
        # 对称FIR在线因果执行仍有群延迟；1000 Hz默认3301个系数，延迟1650点。
        self.reset()

    def reset(self):
        with self._lock:
            self._generation += 1
            self.state = "collecting"
            self.error = None
            self.model = None
            self._calibration = []
            self._calibration_count = 0
            self._input_count = 0
            self._anchor_ms = None
            self._pending_filtered = []
            self._zi = np.zeros((len(self.taps) - 1, len(self.channel_names)))
            if self.config.empty_acquisition_debug:
                # 单位矩阵表示不插值；直接就绪，免去空采坏导校准，但仍等待FIR数据。
                self.model = dict(interpolation=np.eye(len(self.channel_names)),
                                  bad_channels=[], bad_channel_stats=[])
                self.state = "ready"

    def _start_setup(self):
        with self._lock:
            if self.state != "collecting":
                return
            data = np.concatenate(self._calibration, axis=0)
            self._calibration.clear()
            generation = self._generation
            # 后台初始化可能跨越停止/新实验；只允许本次记录的结果写回模型。
            self.state = "processing"

        def work():
            try:
                model = self._model_function(data, self.channel_names, self.sample_rate, self.config)
                with self._lock:
                    if generation == self._generation:
                        self.model = model
                        self.state = "ready"
            except Exception as exc:
                with self._lock:
                    if generation == self._generation:
                        self.error = str(exc)
                        self.state = "error"
        threading.Thread(target=work, daemon=True, name="ERP-Bad-Channel-Setup").start()

    def process_batch(self, packets):
        if not packets:
            return self._drain_cleaned()
        if self.state == "error":
            return []
        try:
            data = np.asarray([p.voltage for p in packets], dtype=float)
            if data.shape != (len(packets), len(self.channel_names)) or not np.all(np.isfinite(data)):
                raise ValueError("ERP输入包含缺失值或通道数不匹配；未填零或删除通道")
            indices = np.array([p.sample_index for p in packets])
            if not np.array_equal(indices, np.arange(self._input_count, self._input_count + len(packets))):
                raise ValueError("ERP输入采样索引不连续")
            if self._anchor_ms is None:
                self._anchor_ms = float(packets[0].timestamp)
            filtered, self._zi = lfilter(self.taps, [1.0], data, axis=0, zi=self._zi)
            # 保留各批次滤波状态；将输出标回原采样位置，不移动软件事件标签。
            aligned = np.arange(self._input_count, self._input_count + len(data)) - self.delay_samples
            self._input_count += len(data)
            keep = aligned >= 0
            # 负索引对应启动时尚无真实历史的滤波输出，不作为有效连续样本。
            if np.any(keep):
                self._pending_filtered.append((aligned[keep], filtered[keep].copy()))
                if self.state == "collecting":
                    needed = int(round(self.config.calibration_seconds * self.sample_rate)) - self._calibration_count
                    take = min(int(keep.sum()), max(needed, 0))
                    if take:
                        self._calibration.append(filtered[keep][:take].copy())
                        self._calibration_count += take
                    if self._calibration_count >= self.config.calibration_seconds * self.sample_rate:
                        self._start_setup()
            return self._drain_cleaned()
        except Exception as exc:
            with self._lock:
                self._generation += 1
                self.model = None
                self.error, self.state = str(exc), "error"
            return []

    def _drain_cleaned(self):
        """Apply fixed interpolation and re-reference to continuous data before epoching."""
        with self._lock:
            if self.state != 'ready' or self.model is None:
                return []
            pending, self._pending_filtered = self._pending_filtered, []
        frames = []
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=2):
            for indices, data in pending:
                # 空间处理函数输出µV，此处还原为V，保持连续缓冲区统一单位。
                cleaned = self.process_samples_uv(data.T).T * 1e-6
                frames.extend((self._anchor_ms + int(i) * 1000.0 / self.sample_rate, row)
                              for i, row in zip(indices, cleaned))
        return frames

    def process_samples_uv(self, epoch_volts):
        """Fixed spatial transform for any continuous channels-by-time block."""
        with self._lock:
            model = self.model
        if model is None:
            return None
        interpolated = model.get('interpolation', np.eye(len(self.channel_names))) @ epoch_volts
        # 每个采样时刻减去全采集通道均值；这是平均参考，不是MATLAB的REST。
        rereferenced = interpolated - interpolated.mean(axis=0, keepdims=True)
        return rereferenced * 1e6

    def finish_collection(self):
        # 正式模式提前停止时允许用已有≥3秒有效数据初始化，不补造后续样本。
        if self.state == "collecting":
            if self._calibration_count >= self.config.minimum_calibration_seconds * self.sample_rate:
                self._start_setup()
            else:
                self.error = f"坏导初始化数据不足{self.config.minimum_calibration_seconds:g}秒"
                self.state = "insufficient"

    def metadata(self):
        with self._lock:
            model = self.model
            return dict(config=asdict(self.config), state=self.state, error=self.error,
                        channels=self.channel_names, sample_rate=self.sample_rate,
                        calibration_samples=self._calibration_count, filter_delay_samples=self.delay_samples,
                        ica_enabled=False, ocular_correction=False,
                        reference="all_channel_average", reference_name="重参考（全通道平均参考）",
                        output_unit="uV", bad_channel_removal=False,
                        experiment_mode='empty_acquisition_debug' if self.config.empty_acquisition_debug else 'experiment',
                        trial_rejection_enabled=not self.config.empty_acquisition_debug,
                        bad_channel_method='disabled_for_debug' if self.config.empty_acquisition_debug else 'spherical_spline_interpolation',
                        bad_channels=[] if model is None else model['bad_channels'],
                        bad_channel_stats=[] if model is None else model['bad_channel_stats'],
                        processing_order=(['continuous_1_10_hz_filter','rereference','epoch','baseline','erp']
                            if self.config.empty_acquisition_debug else
                            ['continuous_1_10_hz_filter','bad_channel_interpolation',
                             'rereference','epoch','baseline','trial_rejection','erp']))

    def status_text(self):
        prefix = f"整体{self.config.low_hz:g}–{self.config.high_hz:g}Hz；重参考（全通道平均参考）。"
        if self.config.empty_acquisition_debug:
            return ('空采调试（非实验分析）：' + prefix + '跳过坏导检测与试次剔除'
                    if self.state == 'ready' else f'空采调试预处理不可用：{self.error}')
        if self.state == "collecting":
            return prefix + f"坏导初始化 {self._calibration_count/self.sample_rate:.1f}/{self.config.calibration_seconds:g}s"
        if self.state == "processing":
            return prefix + "坏导检测与插值初始化中"
        if self.state == "ready":
            return prefix + f"插值{len(self.model['bad_channels'])}个坏导"
        return f"ERP预处理未就绪：{self.error}"
