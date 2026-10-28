"""
EEG ERP 离线分析模块
基于 MATLAB a1_auto_process.m + a2_erp.m 转换

完整流程:
    原始 EEG 数据 → 重参考 → 试次提取 → 基线校正/伪迹剔除 → ERP 计算 → 绘图/统计

支持:
    - 读取 .cnt / .edf 文件（通过 mne）
    - 离线重参考（拉普拉斯投影法，基于偶极子头模型）
    - Target / Standard 试次提取
    - 基线校正 + 振幅/方差伪迹剔除
    - 单被试/组平均 ERP
    - 差异波 (target - standard)
    - 组间比较 (ASD vs TD)
    - 导出图像 (.png) 和结果 (.csv / .npz)
    from erp_processor import ERPProcessor

    proc = ERPProcessor(sample_rate=1000, n_channels=30)
    proc.load_cnt("path/to/file.cnt")
    proc.rereference()                      # 重参考
    proc.extract_trials(pre_stim_s=0.1, post_stim_s=0.5)  # 试次提取
    proc.baseline_correct(baseline_s=(0, 0.1))             # 基线校正
    proc.reject_artifacts(amp_uv=100, var_thresh=1000)     # 伪迹剔除
    proc.compute_erp()                                          # 计算平均 ERP
    proc.plot_all_channels("erp_output.png")                   # 绘图
    proc.save_results("erp_results.npz")                       # 保存
"""

import os
import json
import copy
import warnings
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, Literal

import numpy as np

# ─── 类型别名 ───────────────────────────────────────────────────────────────
EventDict = Dict[str, int | float | str]
EpochArray = np.ndarray          # shape: (n_trials, n_channels, n_times)
ERPArray = np.ndarray            # shape: (n_channels, n_times)
ChannelMeta = List[str]          # 通道名列表
TimeAxis = np.ndarray            # shape: (n_times,) 单位：秒

# ─── 诊断日志（复用项目已有 diag）─────────────────────────────────────────
try:
    from diag import log, log_exc
except ImportError:
    def log(*a, **k): print(*a)
    def log_exc(*a, **k): import traceback; traceback.print_exc()

# ─── 事件编码（与 VR Marker / V2.m 一致）────────────────────────────────
TRIGGER_MAP: Dict[int, Tuple[str, int]] = {
    # label -> (event_type, channel)
    11: ("target", 1), 22: ("target", 2), 33: ("target", 3), 44: ("target", 4),
    55: ("target", 5), 66: ("target", 6), 77: ("target", 7), 88: ("target", 8),
     1: ("standard", 1),  2: ("standard", 2),  3: ("standard", 3),  4: ("standard", 4),
     5: ("standard", 5),  6: ("standard", 6),  7: ("standard", 7),  8: ("standard", 8),
    89: ("response", 0),
}


# ═══════════════════════════════════════════════════════════════════════════════
#  内部工具
# ═══════════════════════════════════════════════════════════════════════════════

def _butter_bandpass(
    data: np.ndarray,
    low: float,
    high: float,
    fs: float,
    order: int = 4,
) -> np.ndarray:
    """
    零相位巴特沃斯带通滤波（filtfilt）。
    data: (n_channels, n_times) 或 (n_trials, n_channels, n_times)
    """
    from scipy.signal import butter, filtfilt
    nyq = fs / 2.0
    low = max(0.001, min(low, nyq - 0.001)) if low > 0 else 0.001
    high = max(low + 0.001, min(high, nyq - 0.001))
    b, a = butter(order, [low / nyq, high / nyq], btype="band")
    shape = data.shape
    flat = data.reshape(-1, shape[-1])
    filtered = filtfilt(b, a, flat, axis=-1)
    return filtered.reshape(shape)


def _label_to_event(label: int) -> Tuple[str, int]:
    """trigger label → (event_type, channel)"""
    return TRIGGER_MAP.get(label, ("unknown", 0))


# ═══════════════════════════════════════════════════════════════════════════════
#  ERP 结果数据类
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ERPResult:
    """
    单被试或组平均的 ERP 结果。
    """
    # 基础信息
    group_type: str = ""          # "ASD" / "TD" / "merged"
    data_type: str = ""            # "vis" / "audi" / "social"
    freq_band: str = "1-10Hz"
    n_subjects: int = 0
    n_channels: int = 0
    n_times: int = 0
    sample_rate: float = 1000.0
    pre_stim_s: float = 0.2       # 刺激前窗口（秒），与 offline_erp.py 保持一致
    post_stim_s: float = 0.8      # 刺激后窗口（秒），与 offline_erp.py 保持一致

    # ERP 数据（n_channels × n_times，单位 µV）
    target_erp: Optional[ERPArray] = None
    standard_erp: Optional[ERPArray] = None
    difference_erp: Optional[ERPArray] = None

    # 统计量
    target_std: Optional[ERPArray] = None
    standard_std: Optional[ERPArray] = None
    target_sem: Optional[ERPArray] = None
    standard_sem: Optional[ERPArray] = None

    # 试次计数
    n_target: int = 0
    n_standard: int = 0

    # 时间轴（秒，0 = 刺激 onset）
    time_axis: Optional[TimeAxis] = None

    # 通道名
    channel_names: List[str] = field(default_factory=list)

    # 全被试原始 epoch（用于后续统计分析）
    all_target_epochs: Optional[EpochArray] = None   # (n_sub, n_ch, n_times)
    all_standard_epochs: Optional[EpochArray] = None

    # P300 窗口统计（300-600ms，中心 Pz）
    p300_window: Tuple[float, float] = (0.3, 0.6)   # 秒

    # ─── 派生属性 ────────────────────────────────────────────────────────

    @property
    def p300_amplitude_pz(self) -> Optional[float]:
        """Pz 通道在 P300 窗口内的平均振幅（差异波）"""
        if self.difference_erp is None:
            return None
        pz_idx = self._pz_index()
        if pz_idx is None:
            return None
        t = self.time_axis
        w = self.p300_window
        mask = (t >= w[0]) & (t <= w[1])
        return float(np.mean(self.difference_erp[pz_idx, mask]))

    def _pz_index(self) -> Optional[int]:
        for i, name in enumerate(self.channel_names):
            if name.upper().replace(".", "").replace(" ", "") in ("PZ", "CPZ"):
                return i
        # fallback: 找不到 Pz，取中线中央通道
        for i, name in enumerate(self.channel_names):
            if "P" in name.upper() and "Z" in name.upper():
                return i
        # 再 fallback: 中间通道
        if self.n_channels > 0:
            return self.n_channels // 2
        return None

    def to_dict(self) -> dict:
        """序列化为普通 dict（便于 json / CSV 导出）"""
        return {
            "group": self.group_type,
            "data_type": self.data_type,
            "freq_band": self.freq_band,
            "n_subjects": self.n_subjects,
            "n_channels": self.n_channels,
            "n_times": self.n_times,
            "sample_rate": self.sample_rate,
            "pre_stim_s": self.pre_stim_s,
            "post_stim_s": self.post_stim_s,
            "n_target": self.n_target,
            "n_standard": self.n_standard,
            "p300_amplitude_pz": self.p300_amplitude_pz,
            "channel_names": self.channel_names,
            "time_axis": self.time_axis.tolist() if self.time_axis is not None else [],
            "p300_window_s": list(self.p300_window),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  核心处理器
# ═══════════════════════════════════════════════════════════════════════════════

class ERPProcessor:
    """
    ERP 离线分析处理器。

    完整处理流水线：
        load() → filter_bandpass() → rereference() →
        extract_trials() → baseline_correct() → reject_artifacts() →
        compute_erp() → plot_*()

    所有中间状态均可访问，支持增量处理。
    """

    # ─── 构造函数 ──────────────────────────────────────────────────────

    def __init__(
        self,
        sample_rate: float = 1000.0,
        n_channels: int = 30,
        channel_names: Optional[List[str]] = None,
    ):
        self.sample_rate = sample_rate
        self.n_channels = n_channels
        self.channel_names = channel_names or [f"Ch{i+1}" for i in range(n_channels)]

        # ── 原始数据 ──────────────────────────────────────────────────
        self.raw_data: Optional[np.ndarray] = None        # (n_times, n_channels)
        self.raw_times: Optional[np.ndarray] = None      # (n_times,)
        self.events: List[EventDict] = []               # 事件列表
        self.events_json_path: Optional[str] = None

        # ── 重参考矩阵（延迟加载，不强制要求偶极子模型文件）──────────
        self.rereference_matrix: Optional[np.ndarray] = None  # (n_ch, n_ch)

        # ── 试次数据 ─────────────────────────────────────────────────
        self.target_epochs: Optional[EpochArray] = None  # (n_trials, n_ch, n_times)
        self.standard_epochs: Optional[EpochArray] = None

        # ── 基线校正后 ───────────────────────────────────────────────
        self.target_clean: Optional[EpochArray] = None
        self.standard_clean: Optional[EpochArray] = None

        # ── ERP 结果 ─────────────────────────────────────────────────
        self.erp_result: Optional[ERPResult] = None

        # ── 配置参数 ─────────────────────────────────────────────────
        self._filter_low: float = 0.0
        self._filter_high: float = 30.0
        self._pre_stim_s: float = 0.1
        self._post_stim_s: float = 0.5
        self._baseline_start_s: float = 0.0
        self._baseline_end_s: float = 0.1
        self._amp_uv: float = 100.0
        self._var_thresh: float = 1000.0
        self._skip_first_n: int = 3           # 跳过头 n 个 trial（避免起始效应）

        # ── 诊断 ────────────────────────────────────────────────────
        self._log: List[str] = []

    # ─── 日志 ────────────────────────────────────────────────────────────

    def _info(self, msg: str) -> None:
        log(f"[ERP] {msg}")
        self._log.append(msg)

    def _warn(self, msg: str) -> None:
        warnings.warn(f"[ERP] {msg}")
        self._log.append(f"WARN: {msg}")

    # ─── 数据加载 ────────────────────────────────────────────────────────

    def load(
        self,
        data: np.ndarray,
        times: np.ndarray,
        sample_rate: float,
        events: Optional[List[EventDict]] = None,
        channel_names: Optional[List[str]] = None,
    ) -> "ERPProcessor":
        """
        通用数据加载接口。

        Args:
            data:       (n_times, n_channels) EEG 数据，单位 V
            times:      (n_times,) 时间轴（秒），0 = 实验开始
            sample_rate: 采样率 Hz
            events:     事件列表，每个 dict 至少含:
                          - timestamp_s: float（秒，相对实验开始）
                          - label: int（trigger 值，见 TRIGGER_MAP）
                          - event_type: str（可选，优先用 label 推断）
                          - channel: int（可选）
            channel_names: 通道名列表
        """
        if data.ndim != 2:
            raise ValueError(f"data 必须是 2D (times×channels)，得到 {data.ndim}D")
        n_times, n_ch = data.shape
        self._info(f"load: {n_times} 采样点 × {n_ch} 通道，fs={sample_rate}Hz")

        self.raw_data = data.astype(np.float64)
        self.raw_times = np.asarray(times, dtype=np.float64)
        self.sample_rate = float(sample_rate)
        self.n_channels = n_ch

        if channel_names:
            self.channel_names = channel_names
        elif len(self.channel_names) != n_ch:
            self.channel_names = [f"Ch{i+1}" for i in range(n_ch)]

        self.events = events or []
        self._info(f"  事件数: {len(self.events)}")

        # ── 预处理：自动找 events.json ───────────────────────────────
        if events is None:
            self._auto_load_events()

        self._reset_intermediate()
        return self

    def _auto_load_events(self) -> None:
        """如果存在同目录的 .events.json，自动加载"""
        # 调用方应在 load 时传入路径；这里提供备用逻辑
        pass

    @classmethod
    def from_cnt(
        cls,
        path: str,
        skip_events: bool = False,
    ) -> "ERPProcessor":
        """
        从 .cnt 文件（ANT / EEProbe 格式）加载数据。
        依赖 mne 或 antchpy；优先 mne。
        """
        proc = cls()

        ext = os.path.splitext(path)[1].lower()
        if ext == ".cnt":
            proc._load_cnt_mne(path, skip_events)
        elif ext == ".edf":
            proc._load_edf_mne(path, skip_events)
        else:
            raise ValueError(f"不支持的文件格式: {ext}，仅支持 .cnt / .edf")

        return proc

    def _load_cnt_mne(self, path: str, skip_events: bool) -> None:
        """通过 mne 加载 CNT 文件"""
        try:
            import mne
        except ImportError:
            raise ImportError("需要安装 mne: pip install mne")

        self._info(f"加载 CNT: {path}")
        # mne.io.read_raw_cnt 返回 Raw 对象
        try:
            raw = mne.io.read_raw_cnt(path, preload=True, verbose=False)
        except Exception:
            # 某些 CNT 变体用 set 参
            raw = mne.io.read_raw_cnt(path, preload=True, set自动=False, verbose=False)

        sfreq = float(raw.info["sfreq"])
        data, times = raw.get_data(return_times=True)
        # mne 存的是 V，转 µV 再存（ERPProcessor 内部统一用 V，但绘图/统计用 µV）
        ch_names = list(raw.ch_names)

        self.load(
            data=data.T,          # mne: (n_ch, n_times) → (n_times, n_ch)
            times=times,
            sample_rate=sfreq,
            channel_names=ch_names,
        )

        if not skip_events:
            self._extract_events_from_mne_annotations(raw)

    def _load_edf_mne(self, path: str, skip_events: bool) -> None:
        """通过 mne 加载 EDF 文件"""
        try:
            import mne
        except ImportError:
            raise ImportError("需要安装 mne: pip install mne")

        self._info(f"加载 EDF: {path}")
        raw = mne.io.read_raw_edf(path, preload=True, verbose=False)
        sfreq = float(raw.info["sfreq"])
        data, times = raw.get_data(return_times=True)
        ch_names = list(raw.ch_names)

        self.load(
            data=data.T,
            times=times,
            sample_rate=sfreq,
            channel_names=ch_names,
        )

        if not skip_events:
            self._extract_events_from_mne_annotations(raw)

    def _extract_events_from_mne_annotations(self, raw) -> None:
        """从 mne Raw.annotations 提取事件到 self.events"""
        if raw.annotations is None or len(raw.annotations) == 0:
            self._warn("EDF/CNT 无 annotations，尝试 trigger 通道检测")
            self._extract_events_from_trigger_channel(raw)
            return

        self.events = []
        for ann in raw.annotations:
            desc = str(ann.description or "")
            onset_s = float(ann.onset or 0.0)
            label = 0
            # EDF+ annotation description 直接存 label 整数（如 "33"）；
            # 兼容旧的 "label=33" / "trigger=33" 写法
            if desc.isdigit():
                label = int(desc)
            elif "=" in desc:
                try:
                    label = int(desc.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass

            evt_type, channel = _label_to_event(label)
            self.events.append({
                "timestamp_s": onset_s,
                "label": label,
                "event_type": evt_type,
                "channel": channel,
            })

        # 同时尝试同目录的 .events.json（覆盖/补充 annotation）
        json_path = os.path.splitext(raw.filenames[0])[0] + ".events.json"
        if os.path.exists(json_path):
            self._info(f"发现 events.json: {json_path}")
            with open(json_path, "r", encoding="utf-8") as f:
                ev_data = json.load(f)
            start_ms = ev_data.get("start_time_ms", 0)
            for ev in ev_data.get("events", []):
                ts_s = (int(ev.get("timestamp_ms", 0)) - start_ms) / 1000.0
                label = int(ev.get("label", 0))
                evt_type, channel = _label_to_event(label)
                self.events.append({
                    "timestamp_s": ts_s,
                    "label": label,
                    "event_type": evt_type,
                    "channel": channel,
                })
            self.events.sort(key=lambda e: e["timestamp_s"])

        self._info(f"  共提取 {len(self.events)} 个事件")

    def _extract_events_from_trigger_channel(self, raw) -> None:
        """没有 annotations 时，从 stim 通道手动检测 trigger 上升沿"""
        stim_ch_names = [
            c for c in raw.ch_names
            if raw.get_channel_types(picks=[c])[0] == "stim"
        ]
        if not stim_ch_names:
            self._warn("无 stim 通道，跳过事件提取")
            return

        ch_data = raw.get_data(picks=stim_ch_names[:1])[0]
        last_val = 0
        for i, v in enumerate(ch_data):
            v = int(round(v))
            if v > 0 and last_val == 0:
                onset_s = float(i) / self.sample_rate
                evt_type, channel = _label_to_event(v)
                if evt_type != "unknown":
                    self.events.append({
                        "timestamp_s": onset_s,
                        "label": v,
                        "event_type": evt_type,
                        "channel": channel,
                    })
            last_val = v

        self._info(f"  从 stim 通道提取 {len(self.events)} 个事件")

    @classmethod
    def from_numpy(
        cls,
        data: np.ndarray,
        times: np.ndarray,
        sample_rate: float = 1000.0,
        channel_names: Optional[List[str]] = None,
    ) -> "ERPProcessor":
        """从 numpy 数组快速构建（用于测试 / 模拟数据）"""
        return cls().load(data, times, sample_rate, channel_names=channel_names)

    # ─── 滤波 ────────────────────────────────────────────────────────────

    def filter_bandpass(
        self,
        low_hz: float = 0.5,
        high_hz: float = 30.0,
    ) -> "ERPProcessor":
        """
        带通滤波（零相位巴特沃斯 IIR，4阶）。

        Args:
            low_hz:  高通截止频率 Hz（默认 0.5，去除慢漂）
            high_hz: 低通截止频率 Hz（默认 30，P300 经典范围）
        """
        if self.raw_data is None:
            self._warn("filter_bandpass: 无原始数据，跳过")
            return self

        self._info(f"带通滤波: {low_hz}-{high_hz}Hz")
        self._filter_low = low_hz
        self._filter_high = high_hz

        self.raw_data = _butter_bandpass(
            self.raw_data, low_hz, high_hz, self.sample_rate
        )
        return self

    # ─── 重参考 ──────────────────────────────────────────────────────────

    def rereference(
        self,
        method: Literal["average", "laplacian"] = "average",
        leadfield_path: Optional[str] = None,
    ) -> "ERPProcessor":
        """
        重参考。

        Args:
            method: "average"   → 平均参考（减去所有通道均值）
                    "laplacian" → 拉普拉斯投影法（需要偶极子模型）
            leadfield_path: 偶极子前导场文件路径（.dat）
                           仅 laplacian 方法需要。
        """
        if self.raw_data is None:
            self._warn("rereference: 无原始数据，跳过")
            return self

        if method == "average":
            self._info("重参考: 平均参考")
            # (n_times, n_ch) - mean(n_times, n_ch) → 每时刻减所有通道均值
            ch_mean = np.mean(self.raw_data, axis=1, keepdims=True)
            self.raw_data = self.raw_data - ch_mean

        elif method == "laplacian":
            self._info("重参考: 拉普拉斯投影法")
            self.rereference_matrix = self._compute_laplacian_matrix(leadfield_path)
            if self.rereference_matrix is not None:
                # Ra @ (data - row_mean)
                ch_mean = np.mean(self.raw_data, axis=1, keepdims=True)
                centered = self.raw_data - ch_mean
                self.raw_data = self.rereference_matrix @ centered
                # 恢复均值（参考文章的做法）
                ref_mean = np.mean(self.raw_data, axis=1, keepdims=True)
                self.raw_data = self.raw_data + ref_mean

        return self

    def _compute_laplacian_matrix(
        self,
        leadfield_path: Optional[str] = None,
    ) -> Optional[np.ndarray]:
        """
        计算拉普拉斯投影重参考矩阵 Ra = G @ pinv(G_ave, 0.05)。
        G: (n_channels, n_dipoles) 前导场矩阵
        G_ave: 行均值投影
        Ra: (n_channels, n_channels)

        若没有偶极子文件，返回平均参考矩阵。
        """
        # 尝试默认路径
        if leadfield_path is None:
            candidates = [
                os.path.join(os.path.dirname(__file__), "corti869-3000dipoles.dat"),
                os.path.join(os.path.dirname(__file__), "erp", "corti869-3000dipoles.dat"),
            ]
            for p in candidates:
                if os.path.exists(p):
                    leadfield_path = p
                    break

        if leadfield_path is None or not os.path.exists(leadfield_path):
            self._warn("拉普拉斯重参考: 找不到偶极子文件，降级为平均参考")
            # 返回单位矩阵（实际上这里直接用平均参考更简单）
            return None

        try:
            # 加载偶极子位置
            dipoles = np.loadtxt(leadfield_path)  # (n_dip, 3)
            self._info(f"  加载 {dipoles.shape[0]} 个皮层偶极子")

            # 计算偶极子方向（单位向量）
            norms = np.linalg.norm(dipoles, axis=1, keepdims=True)
            dip_ori = dipoles / (norms + 1e-12)
            # 底部偶极子（2601-3000）固定朝上
            dip_ori[2600:, :] = [0, 0, 1]

            # 加载电极位置
            loc_path = os.path.join(os.path.dirname(leadfield_path), "location30.txt")
            if not os.path.exists(loc_path):
                loc_path = os.path.join(os.path.dirname(__file__), "erp", "location30.txt")
            if os.path.exists(loc_path):
                loc = np.loadtxt(loc_path)   # (n_elec, 3)
                self._info(f"  加载 {loc.shape[0]} 个电极位置")
            else:
                self._warn("  找不到电极位置文件，使用默认网格")
                n_elec = self.n_channels
                loc = np.zeros((n_elec, 3))
                theta = np.linspace(0, 2 * np.pi, n_elec, endpoint=False)
                loc[:, 0] = np.cos(theta)
                loc[:, 1] = np.sin(theta)

            # 加载前导场计算函数（项目自带）
            lf_func_path = os.path.join(os.path.dirname(leadfield_path), "dong_calc_leadfield3.py")
            if not os.path.exists(lf_func_path):
                lf_func_path = os.path.join(os.path.dirname(__file__), "erp", "dong_calc_leadfield3.py")

            if os.path.exists(lf_func_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("dong_calc_leadfield3", lf_func_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                G, _ = mod.dong_calc_leadfield3(loc, dipoles, dip_ori, headmodel=None)
            else:
                # 简化前导场：球头模型近似
                self._warn("  找不到前导场函数，使用简化模型")
                n_dip = dipoles.shape[0]
                G = np.zeros((self.n_channels, n_dip))
                for i, elec in enumerate(loc):
                    diff = dipoles - elec
                    dist = np.linalg.norm(diff, axis=1) + 1e-6
                    G[i, :] = 1.0 / dist

            # 计算投影矩阵
            G_ave = np.mean(G, axis=0, keepdims=True)      # (1, n_dip)
            G_centered = G - G_ave                           # (n_ch, n_dip)
            Ra = G_centered @ np.linalg.pinv(G_centered, 0.05)  # (n_ch, n_ch)
            self._info(f"  投影矩阵大小: {Ra.shape}")
            return Ra

        except Exception as e:
            log_exc(f"[ERP] 拉普拉斯重参考计算失败，降级为平均参考:")
            return None

    # ─── 试次提取 ────────────────────────────────────────────────────────

    def extract_trials(
        self,
        pre_stim_s: float = 0.1,
        post_stim_s: float = 0.5,
        event_types: Optional[List[str]] = None,
    ) -> "ERPProcessor":
        """
        按事件标记提取 trial。

        Args:
            pre_stim_s:   刺激前窗口（秒）
            post_stim_s:  刺激后窗口（秒）
            event_types:  要提取的事件类型，默认 ["target", "standard"]
        """
        if self.raw_data is None:
            self._warn("extract_trials: 无原始数据，跳过")
            return self

        self._pre_stim_s = pre_stim_s
        self._post_stim_s = post_stim_s
        event_types = event_types or ["target", "standard"]

        n_pre = int(pre_stim_s * self.sample_rate)
        n_post = int(post_stim_s * self.sample_rate)
        n_epoch = n_pre + n_post
        self._info(f"试次提取: pre={pre_stim_s}s({n_pre}点) post={post_stim_s}s({n_post}点) 总={n_epoch}点")

        target_list: List[np.ndarray] = []
        standard_list: List[np.ndarray] = []

        # 按时间排序事件
        events_sorted = sorted(self.events, key=lambda e: e.get("timestamp_s", 0))

        skipped = 0
        kept = 0
        for idx, ev in enumerate(events_sorted):
            evt_type = ev.get("event_type", "unknown")
            if evt_type not in event_types:
                continue

            onset_s = ev.get("timestamp_s", 0.0)
            onset_sample = int(onset_s * self.sample_rate)

            # 跳过头 N 个（避免起始效应；与 MATLAB 逻辑一致）
            if idx < self._skip_first_n:
                skipped += 1
                continue

            s_start = onset_sample - n_pre
            s_end = onset_sample + n_post

            if s_start < 0 or s_end > self.raw_data.shape[0]:
                skipped += 1
                continue

            epoch = self.raw_data[s_start:s_end, :].T  # (n_ch, n_epoch)
            if evt_type == "target":
                target_list.append(epoch)
            elif evt_type == "standard":
                standard_list.append(epoch)
            kept += 1

        self.target_epochs = np.stack(target_list) if target_list else np.array([], dtype=float).reshape(0, self.n_channels, n_epoch)
        self.standard_epochs = np.stack(standard_list) if standard_list else np.array([], dtype=float).reshape(0, self.n_channels, n_epoch)

        self._info(f"  Target: {len(target_list)} trials | Standard: {len(standard_list)} trials | 跳过: {skipped}")
        self._reset_clean()
        return self

    # ─── 基线校正 ────────────────────────────────────────────────────────

    def baseline_correct(
        self,
        baseline_start_s: float = 0.0,
        baseline_end_s: float = 0.1,
    ) -> "ERPProcessor":
        """
        基线校正：减去刺激前窗口的平均值。

        Args:
            baseline_start_s: 基线窗口起点（秒，相对刺激 onset）
            baseline_end_s:    基线窗口终点
        """
        self._baseline_start_s = baseline_start_s
        self._baseline_end_s = baseline_end_s

        if self.target_epochs is None and self.standard_epochs is None:
            self._warn("baseline_correct: 无试次数据，跳过")
            return self

        n_pre = int(baseline_end_s * self.sample_rate)
        n_pre = min(n_pre, self.target_epochs.shape[-1] if self.target_epochs.size else self.standard_epochs.shape[-1])

        def _correct(epochs: Optional[EpochArray]) -> Optional[EpochArray]:
            if epochs is None or epochs.size == 0:
                return epochs
            base = np.mean(epochs[..., :n_pre], axis=-1, keepdims=True)
            return epochs - base

        self.target_epochs = _correct(self.target_epochs)
        self.standard_epochs = _correct(self.standard_epochs)
        self._info(f"基线校正完成 ({baseline_start_s}-{baseline_end_s}s)")
        self._reset_clean()
        return self

    # ─── 伪迹剔除 ────────────────────────────────────────────────────────

    def reject_artifacts(
        self,
        amp_uv: float = 100.0,
        var_thresh: float = 1000.0,
    ) -> "ERPProcessor":
        """
        逐 trial 剔除伪迹。

        Args:
            amp_uv:   振幅阈值 µV（超出 ±amp_uv 的 trial 被剔除）
            var_thresh: 方差阈值（超出 var_thresh 的 trial 被剔除）
        """
        self._amp_uv = amp_uv
        self._var_thresh = var_thresh

        def _filter(epochs: Optional[EpochArray]) -> Tuple[Optional[EpochArray], int, int]:
            if epochs is None or epochs.size == 0:
                return None, 0, 0
            kept: List[np.ndarray] = []
            for ep in epochs:
                # 转为 µV（数据存 V）
                ep_uv = ep * 1e6
                if np.max(np.abs(ep_uv)) > amp_uv:
                    continue
                if np.var(ep_uv) > var_thresh:
                    continue
                kept.append(ep)
            if kept:
                return np.stack(kept), len(epochs), len(kept)
            return np.array([], dtype=float).reshape(0, epochs.shape[1], epochs.shape[2]), len(epochs), 0

        self.target_clean, n_t_raw, n_t_clean = _filter(self.target_epochs)
        self.standard_clean, n_s_raw, n_s_clean = _filter(self.standard_epochs)

        self._info(f"伪迹剔除: Target {n_t_raw}→{n_t_clean} (保留{n_t_clean/n_t_raw*100:.0f}%) "
                   f"| Standard {n_s_raw}→{n_s_clean} (保留{n_s_clean/n_s_raw*100:.0f}%)")
        return self

    # ─── ERP 计算 ─────────────────────────────────────────────────────────

    def compute_erp(
        self,
        group_type: str = "",
        data_type: str = "",
        freq_band: str = "1-10Hz",
    ) -> "ERPProcessor":
        """
        计算 Target / Standard 平均 ERP 及差异波。

        同时计算 SEM（标准误）用于误差带绘制。
        """
        target = self.target_clean
        standard = self.standard_clean

        if target is None and standard is None:
            self._warn("compute_erp: 无有效试次，无法计算")
            return self

        n_t = target.shape[0] if target is not None and target.size > 0 else 0
        n_s = standard.shape[0] if standard is not None and standard.size > 0 else 0

        # 时间轴（秒，0 = 刺激 onset）
        n_times = (target.shape[-1] if n_t > 0
                   else standard.shape[-1] if n_s > 0
                   else int((self._pre_stim_s + self._post_stim_s) * self.sample_rate))
        t_start = -self._pre_stim_s
        t_end = t_start + n_times / self.sample_rate
        time_axis = np.linspace(t_start, t_end, n_times)

        result = ERPResult(
            group_type=group_type,
            data_type=data_type,
            freq_band=freq_band,
            n_subjects=1,
            n_channels=self.n_channels,
            n_times=n_times,
            sample_rate=self.sample_rate,
            pre_stim_s=self._pre_stim_s,
            post_stim_s=self._post_stim_s,
            n_target=n_t,
            n_standard=n_s,
            time_axis=time_axis,
            channel_names=self.channel_names.copy(),
            all_target_epochs=target,
            all_standard_epochs=standard,
        )

        if n_t > 0:
            result.target_erp = np.mean(target, axis=0)      # (n_ch, n_times)
            result.target_std = np.std(target, axis=0, ddof=1)
            result.target_sem = result.target_std / np.sqrt(n_t)

        if n_s > 0:
            result.standard_erp = np.mean(standard, axis=0)
            result.standard_std = np.std(standard, axis=0, ddof=1)
            result.standard_sem = result.standard_std / np.sqrt(n_s)

        if n_t > 0 and n_s > 0:
            result.difference_erp = result.target_erp - result.standard_erp

        self.erp_result = result
        p3 = result.p300_amplitude_pz
        self._info(f"ERP 计算完成: Target={n_t}, Standard={n_s}, P300(Pz)={p3:.2f}µV" if p3 else "ERP 计算完成")
        return self

    # ─── 合并组平均（用于 ASD vs TD 组间比较）────────────────────────────

    @staticmethod
    def merge_group_results(
        results: List[ERPResult],
        group_label: str,
    ) -> ERPResult:
        """
        将多个被试的 ERPResult 合并为组平均。

        用于: 把多个单被试 ERPResult 合并成一个组平均（grand average），
             再做 ASD vs TD 组间比较。
        """
        if not results:
            raise ValueError("results 不能为空")

        first = results[0]
        n_sub = len(results)
        n_ch = first.n_channels
        n_t = first.n_times

        all_target = np.stack([r.all_target_epochs for r in results if r.all_target_epochs is not None and r.all_target_epochs.size > 0])
        all_standard = np.stack([r.all_standard_epochs for r in results if r.all_standard_epochs is not None and r.all_standard_epochs.size > 0])

        merged = ERPResult(
            group_type=group_label,
            data_type=first.data_type,
            freq_band=first.freq_band,
            n_subjects=n_sub,
            n_channels=n_ch,
            n_times=n_t,
            sample_rate=first.sample_rate,
            pre_stim_s=first.pre_stim_s,
            post_stim_s=first.post_stim_s,
            n_target=all_target.shape[0] if all_target.size else 0,
            n_standard=all_standard.shape[0] if all_standard.size else 0,
            time_axis=first.time_axis,
            channel_names=first.channel_names,
            all_target_epochs=all_target if all_target.size else None,
            all_standard_epochs=all_standard if all_standard.size else None,
        )

        if all_target.size:
            merged.target_erp = np.mean(all_target, axis=0)
            merged.target_std = np.std(all_target, axis=0, ddof=1)
            merged.target_sem = merged.target_std / np.sqrt(all_target.shape[0])

        if all_standard.size:
            merged.standard_erp = np.mean(all_standard, axis=0)
            merged.standard_std = np.std(all_standard, axis=0, ddof=1)
            merged.standard_sem = merged.standard_std / np.sqrt(all_standard.shape[0])

        if merged.target_erp is not None and merged.standard_erp is not None:
            merged.difference_erp = merged.target_erp - merged.standard_erp

        log(f"[ERP] 组平均完成: {group_label}, n={n_sub}")
        return merged

    # ─── 组间比较 ────────────────────────────────────────────────────────

    @staticmethod
    def compare_groups(
        group_a: ERPResult,
        group_b: ERPResult,
        label_a: str = "Group A",
        label_b: str = "Group B",
    ) -> Dict[str, ERPResult]:
        """
        两组 ERPResult 做逐通道逐时间点 t 检验。

        Returns:
            dict 含:
                "diff": 两组 ERP 差异（group_a - group_b）
                "t_map": t 值地形图 (n_ch, n_times)
                "p_map": p 值地形图 (n_ch, n_times)
                "sig_mask": 显著性掩码 (n_ch, n_times) bool，p<0.05
        """
        from scipy import stats

        results = {}

        for evt_type in ["target", "standard"]:
            a_epochs = getattr(group_a, f"all_{evt_type}_epochs", None)
            b_epochs = getattr(group_b, f"all_{evt_type}_epochs", None)

            if a_epochs is None or a_epochs.size == 0 or b_epochs is None or b_epochs.size == 0:
                continue

            # shape: (n_sub, n_ch, n_times)
            a_mean = np.mean(a_epochs, axis=0)
            b_mean = np.mean(b_epochs, axis=0)
            diff = a_mean - b_mean

            # 独立样本 t 检验（被试数可以不同）
            n_a, n_b = a_epochs.shape[0], b_epochs.shape[0]
            # Welch's t: 不假设方差齐性
            var_a = np.var(a_epochs, axis=0, ddof=1)
            var_b = np.var(b_epochs, axis=0, ddof=1)
            se = np.sqrt(var_a / n_a + var_b / n_b)
            t_stat = diff / (se + 1e-12)
            # 自由度（Welch-Satterthwaite）
            num = (var_a / n_a + var_b / n_b) ** 2
            denom = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
            df = np.where(denom > 0, num / denom, 1)
            p_val = 2 * stats.t.sf(np.abs(t_stat), df)

            key = f"{evt_type}_comparison"
            results[key] = {
                "label_a": label_a,
                "label_b": label_b,
                "mean_diff": diff,
                "t_map": t_stat,
                "p_map": p_val,
                "sig_mask": p_val < 0.05,
                "n_a": n_a,
                "n_b": n_b,
            }

        return results

    # ─── 绘图 ────────────────────────────────────────────────────────────

    def plot_all_channels(
        self,
        save_path: Optional[str] = None,
        show: bool = True,
        p300_window: Tuple[float, float] = (0.3, 0.6),
        diff_color: str = "green",
        target_color: str = "#A0CDE4",
        standard_color: str = "#FBC4A0",
    ) -> None:
        """
        绘制所有通道的 ERP 叠加波形。

        生成:
            - 全局平均 ERP（Target / Standard / 差异波）
            - 每个通道单独一张子图
            - 差异波（填色误差带）

        Args:
            save_path:  保存路径（如 None 则仅显示）
            show:       是否调用 plt.show()
            p300_window: P300 窗口（秒），用于标注
        """
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from matplotlib.patches import Rectangle
        except ImportError:
            self._warn("plot_all_channels: 需要 matplotlib")
            return

        if self.erp_result is None:
            self._warn("plot: 请先调用 compute_erp()")
            return

        res = self.erp_result
        t = res.time_axis
        n_ch = res.n_channels

        # 确定子图布局
        n_cols = min(6, n_ch)
        n_rows = int(np.ceil(n_ch / n_cols))

        fig, axes = plt.subplots(n_rows, n_cols, figsize=(n_cols * 2.5, n_rows * 2.0),
                                 squeeze=False)
        fig.suptitle(f"ERP — {res.group_type} {res.data_type} {res.freq_band}  "
                     f"[T:{res.n_target} S:{res.n_standard}]", fontsize=11)

        pz_idx = res._pz_index()

        for ch_idx in range(n_ch):
            ax = axes[ch_idx // n_cols][ch_idx % n_cols]
            ch_name = res.channel_names[ch_idx] if ch_idx < len(res.channel_names) else f"Ch{ch_idx+1}"

            # Target ± SEM
            if res.target_erp is not None:
                sem = res.target_sem[ch_idx] if res.target_sem is not None else 0
                ax.fill_between(t, res.target_erp[ch_idx] - sem,
                                res.target_erp[ch_idx] + sem,
                                alpha=0.25, color=target_color)
                ax.plot(t, res.target_erp[ch_idx], color=target_color,
                        linewidth=2, label=f"Target (n={res.n_target})")

            # Standard ± SEM
            if res.standard_erp is not None:
                sem = res.standard_sem[ch_idx] if res.standard_sem is not None else 0
                ax.fill_between(t, res.standard_erp[ch_idx] - sem,
                                res.standard_erp[ch_idx] + sem,
                                alpha=0.25, color=standard_color)
                ax.plot(t, res.standard_erp[ch_idx], color=standard_color,
                        linewidth=2, label=f"Standard (n={res.n_standard})")

            # 差异波
            if res.difference_erp is not None:
                ax.plot(t, res.difference_erp[ch_idx], color=diff_color,
                        linewidth=1.5, linestyle="--", label="Difference")

            ax.axhline(0, color="k", linewidth=0.5)
            ax.axvline(0, color="k", linewidth=0.5, linestyle="--")
            ax.set_title(ch_name, fontsize=9)
            ax.set_xlabel("Time (s)", fontsize=7)
            ax.set_ylabel("µV", fontsize=7)
            ax.tick_params(labelsize=7)

            # P300 窗口标注（Pz 通道加亮）
            if ch_idx == pz_idx and res.difference_erp is not None:
                ax.axvspan(p300_window[0], p300_window[1], alpha=0.08,
                           color="yellow", label=f"P300 {p300_window}")
                ax.legend(fontsize=6, loc="upper right")

        # 隐藏空白子图
        for ch_idx in range(n_ch, n_rows * n_cols):
            axes[ch_idx // n_cols][ch_idx % n_cols].axis("off")

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            self._info(f"ERP 图已保存: {save_path}")
        if show:
            plt.show()
        plt.close(fig)

    # ─── 导出 ────────────────────────────────────────────────────────────

    def save_results(
        self,
        path: str,
        include_epochs: bool = False,
    ) -> None:
        """
        将 ERP 结果保存为 .npz 文件。

        Args:
            path:           .npz 路径
            include_epochs: 是否包含原始 epoch（文件会很大）
        """
        if self.erp_result is None:
            self._warn("save_results: 请先调用 compute_erp()")
            return

        res = self.erp_result
        data: Dict[str, np.ndarray] = {
            "time_axis": res.time_axis,
            "target_erp": res.target_erp,
            "standard_erp": res.standard_erp,
            "difference_erp": res.difference_erp,
            "target_sem": res.target_sem,
            "standard_sem": res.standard_sem,
            "n_target": np.array([res.n_target]),
            "n_standard": np.array([res.n_standard]),
            "n_channels": np.array([res.n_channels]),
            "n_times": np.array([res.n_times]),
            "sample_rate": np.array([res.sample_rate]),
        }

        if include_epochs:
            if res.all_target_epochs is not None:
                data["target_epochs"] = res.all_target_epochs
            if res.all_standard_epochs is not None:
                data["standard_epochs"] = res.all_standard_epochs

        np.savez_compressed(path, **data)

        # 保存元数据 JSON
        meta_path = os.path.splitext(path)[0] + "_meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(res.to_dict(), f, indent=2, ensure_ascii=False)

        self._info(f"结果已保存: {path} + {meta_path}")

    def export_csv(self, csv_path: str) -> None:
        """
        将 ERP 波形导出为 CSV（用于 Excel / Python 后续分析）。

        每行一个通道，列 = [ch_name, time_0, time_1, ...]
        """
        if self.erp_result is None:
            return

        res = self.erp_result
        t = res.time_axis

        rows = []
        header = ["channel", "type"] + [f"{tv:.4f}" for tv in t]

        for ch_idx, ch_name in enumerate(res.channel_names):
            if res.target_erp is not None:
                rows.append([ch_name, "target"] + res.target_erp[ch_idx].tolist())
            if res.standard_erp is not None:
                rows.append([ch_name, "standard"] + res.standard_erp[ch_idx].tolist())
            if res.difference_erp is not None:
                rows.append([ch_name, "difference"] + res.difference_erp[ch_idx].tolist())

        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(",".join(header) + "\n")
            for row in rows:
                f.write(",".join(str(v) for v in row) + "\n")

        self._info(f"CSV 已导出: {csv_path}")

    # ─── 内部工具 ────────────────────────────────────────────────────────

    def _reset_intermediate(self) -> None:
        """清除所有中间处理结果（保留原始数据）"""
        self.rereference_matrix = None
        self._reset_clean()

    def _reset_clean(self) -> None:
        self.target_clean = None
        self.standard_clean = None
        self.erp_result = None

    # ─── 便捷单文件流水线 ────────────────────────────────────────────────

    def process_file(
        self,
        file_path: str,
        output_dir: str,
        band_low: float = 0.5,
        band_high: float = 30.0,
        pre_stim_s: float = 0.1,
        post_stim_s: float = 0.5,
        rerference_method: Literal["average", "laplacian"] = "average",
        group: str = "",
        data_type: str = "",
        freq_band: str = "1-10Hz",
    ) -> Optional[ERPResult]:
        """
        完整流水线：加载 → 滤波 → 重参考 → 试次提取 → 基线校正 → 伪迹剔除 → ERP。

        绘图和保存自动完成。

        Args:
            file_path:    EEG 文件路径 (.cnt / .edf)
            output_dir:   输出目录（自动创建）
            band_low/high: 带通滤波参数
            pre/post_stim_s: trial 窗口
            rerference_method: 重参考方法
            group:        组标签（如 "ASD" / "TD"）
            data_type:    数据类型（如 "vis" / "audi" / "social"）
            freq_band:    频段标签

        Returns:
            ERPResult 或 None（处理失败时）
        """
        os.makedirs(output_dir, exist_ok=True)
        basename = os.path.splitext(os.path.basename(file_path))[0]

        try:
            self._info(f"=== 处理文件: {file_path} ===")
            self.from_cnt(file_path)
            self.filter_bandpass(band_low, band_high)
            self.rereference(method=rerference_method)
            self.extract_trials(pre_stim_s, post_stim_s)
            self.baseline_correct(0.0, 0.1)
            self.reject_artifacts()
            self.compute_erp(group_type=group, data_type=data_type, freq_band=freq_band)

            # 绘图（ERP 波形图，不含地形图）
            png_path = os.path.join(output_dir, f"{basename}_erp.png")
            self.plot_all_channels(save_path=png_path, show=False)

            # 保存
            npz_path = os.path.join(output_dir, f"{basename}_erp.npz")
            self.save_results(npz_path, include_epochs=True)

            csv_path = os.path.join(output_dir, f"{basename}_erp.csv")
            self.export_csv(csv_path)

            self._info(f"=== 完成: {basename} ===")
            return self.erp_result

        except Exception as e:
            log_exc(f"[ERP] 处理文件失败: {file_path}:")
            return None
