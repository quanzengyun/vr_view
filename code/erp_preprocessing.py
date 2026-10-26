"""在线 ERP 预处理：固定 FIR、后台 ICA/ICLabel、仅去除眼电成分。

原始 EEG/EDF 不修改。FIR 群延迟通过输出样本的原始时间补偿，而非移动标签。
ICA 只在本次记录的校准数据上拟合一次；所有试次使用同一组权重。
"""
from dataclasses import dataclass, asdict
import threading
import numpy as np
from scipy.signal import lfilter


@dataclass(frozen=True)
class ERPPreprocessingConfig:
    low_hz: float = 1.0
    high_hz: float = 30.0
    calibration_seconds: float = 60.0
    minimum_calibration_seconds: float = 30.0
    eye_probability: float = 0.8
    max_eye_components: int = 2
    amplitude_uv: float = 100.0
    variance_uv2: float = 1000.0


def select_eye_components(probabilities, threshold=0.8, maximum=2):
    probabilities = np.asarray(probabilities, dtype=float)
    if probabilities.ndim != 2 or probabilities.shape[1] != 7 or not np.all(np.isfinite(probabilities)):
        raise ValueError("ICLabel 分类概率无效")
    candidates = np.flatnonzero(probabilities[:, 2] >= threshold)
    return candidates[np.argsort(-probabilities[candidates, 2], kind="stable")][:maximum].tolist()


def fit_eye_model(data, channel_names, sample_rate, config):
    """与 MATLAB 同样用 ICLabel 第3类、>=0.8、最多两个，不删除其他类别。"""
    import mne
    from mne.preprocessing import ICA
    from mne_icalabel.iclabel import iclabel_label_components
    from threadpoolctl import threadpool_limits

    info = mne.create_info(channel_names, sample_rate, "eeg")
    raw = mne.io.RawArray(np.asarray(data).T, info, verbose="ERROR")
    raw.set_montage(mne.channels.make_standard_montage("standard_1020"), match_case=False, on_missing="raise")
    # ICLabel 的训练输入约定为1–100 Hz；这是校准副本，不是多频段ERP分析。
    raw.filter(1.0, 100.0, verbose="ERROR")
    raw.set_eeg_reference("average", projection=False, verbose="ERROR")
    rank = mne.compute_rank(raw, verbose="ERROR")["eeg"]
    if rank < 2:
        raise ValueError("校准数据的独立通道不足，不能拟合眼电模型")
    with threadpool_limits(limits=2):
        ica = ICA(n_components=rank, method="infomax", fit_params=dict(extended=True),
                  random_state=97, max_iter=512, verbose="ERROR")
        # 仅拟合时抽样加速；采集、保存和ERP时间轴仍是原采样率。
        ica.fit(raw, decim=4, verbose="ERROR")
        probabilities = iclabel_label_components(raw, ica, inplace=False, backend="onnx")
    exclude = select_eye_components(probabilities, config.eye_probability, config.max_eye_components)
    # 用公开apply接口构造固定仿射映射，在线无需反复创建MNE对象或重训ICA。
    channels = len(channel_names)
    basis = np.column_stack([np.zeros(channels), np.eye(channels) * 1e-6])
    probe = mne.io.RawArray(basis, raw.info.copy(), verbose="ERROR")
    cleaned = ica.apply(probe, exclude=exclude, verbose="ERROR").get_data()
    offset = cleaned[:, 0]
    projection = (cleaned[:, 1:] - offset[:, None]) / 1e-6
    validation = raw.copy().crop(tmin=0, tmax=2)
    direct = ica.apply(validation.copy(), exclude=exclude, verbose="ERROR").get_data()
    affine = projection @ validation.get_data() + offset[:, None]
    if not np.allclose(affine, direct, rtol=1e-8, atol=1e-12):
        raise ValueError("眼电投影与ICA.apply验证不一致")
    return dict(projection=projection, offset=offset, probabilities=probabilities,
                excluded=exclude, rank=int(rank), iterations=int(ica.n_iter_))


class OnlineERPPreprocessor:
    def __init__(self, channel_names, sample_rate=1000.0, config=None, fit_function=None):
        import mne
        self.channel_names = list(channel_names)
        self.sample_rate = float(sample_rate)
        self.config = config or ERPPreprocessingConfig()
        self._fit_function = fit_function or fit_eye_model
        self._lock = threading.RLock()
        self._generation = 0
        self.taps = mne.filter.create_filter(None, self.sample_rate,
                    self.config.low_hz, self.config.high_hz, phase="zero", fir_design="firwin", verbose="ERROR")
        self.delay_samples = (len(self.taps) - 1) // 2
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
            self._zi = np.zeros((len(self.taps) - 1, len(self.channel_names)))

    def _start_fit(self):
        with self._lock:
            if self.state != "collecting":
                return
            data = np.concatenate(self._calibration, axis=0)
            self._calibration.clear()
            generation = self._generation
            self.state = "fitting"

        def work():
            try:
                model = self._fit_function(data, self.channel_names, self.sample_rate, self.config)
                with self._lock:
                    if generation == self._generation:
                        self.model = model
                        self.state = "ready"
            except Exception as exc:
                with self._lock:
                    if generation == self._generation:
                        self.error = str(exc)
                        self.state = "error"
        threading.Thread(target=work, daemon=True, name="ERP-ICA-ICLabel").start()

    def process_batch(self, packets):
        if not packets:
            return []
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
            if self.state == "collecting":
                needed = int(round(self.config.calibration_seconds * self.sample_rate)) - self._calibration_count
                take = min(len(data), max(needed, 0))
                if take:
                    self._calibration.append(data[:take].copy())
                    self._calibration_count += take
                if self._calibration_count >= self.config.calibration_seconds * self.sample_rate:
                    self._start_fit()
            filtered, self._zi = lfilter(self.taps, [1.0], data, axis=0, zi=self._zi)
            aligned = np.arange(self._input_count, self._input_count + len(data)) - self.delay_samples
            self._input_count += len(data)
            keep = aligned >= 0
            return [(self._anchor_ms + int(i) * 1000.0 / self.sample_rate, row)
                    for i, row in zip(aligned[keep], filtered[keep])]
        except Exception as exc:
            with self._lock:
                self._generation += 1
                self.model = None
                self.error, self.state = str(exc), "error"
            return []

    def clean_epoch_uv(self, epoch_volts):
        with self._lock:
            model = self.model
        if model is None:
            return None
        centered = epoch_volts - epoch_volts.mean(axis=0, keepdims=True)
        cleaned = model["projection"] @ centered + model["offset"][:, None]
        # 保留所有通道，统一为全通道平均参考。
        cleaned -= cleaned.mean(axis=0, keepdims=True)
        return cleaned * 1e6

    def finish_collection(self):
        if self.state == "collecting":
            if self._calibration_count >= self.config.minimum_calibration_seconds * self.sample_rate:
                self._start_fit()
            else:
                self.error = f"眼电校准数据不足{self.config.minimum_calibration_seconds:g}秒"
                self.state = "insufficient"

    def metadata(self):
        with self._lock:
            model = self.model
            return dict(config=asdict(self.config), state=self.state, error=self.error,
                        channels=self.channel_names, sample_rate=self.sample_rate,
                        calibration_samples=self._calibration_count, filter_delay_samples=self.delay_samples,
                        eye_components=[] if model is None else model["excluded"],
                        eye_probabilities=[] if model is None else model["probabilities"][:, 2].tolist(),
                        rank=None if model is None else model["rank"],
                        ica_iterations=None if model is None else model["iterations"],
                        ica_iteration_limit=512, ica_method="extended_infomax", random_seed=97,
                        montage="standard_1020", classification_backend="onnx",
                        reference="all_channel_average", output_unit="uV",
                        ica_training_filter_hz=[1,100], bad_channel_removal=False)

    def status_text(self):
        prefix = f"整体{self.config.low_hz:g}–{self.config.high_hz:g}Hz；平均参考；滤波延迟已补偿{self.delay_samples/self.sample_rate:.2f}s。"
        if self.state == "collecting":
            return prefix + f"眼电校准 {self._calibration_count/self.sample_rate:.1f}/{self.config.calibration_seconds:g}s"
        if self.state == "fitting":
            return prefix + "ICA/ICLabel后台拟合中，完整试次暂存"
        if self.state == "ready":
            return prefix + f"眼电模型就绪：去除{len(self.model['excluded'])}个成分"
        return f"ERP预处理未就绪：{self.error}"
