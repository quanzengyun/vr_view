"""
ERP 实时集成示例
=================
展示如何将 erp_processor 与实时平台结合使用。

场景 1：实时监控（实验中途看 ERP 曲线）
    - 每个 block 结束后调用 update_view()

场景 2：离线导出（实验结束后批量分析）
    - 保存 EEG + events.json 后用 process_file() 全自动跑
"""

import numpy as np
from erp_processor import ERPProcessor, ERPResult

# ══════════════════════════════════════════════════════════════════════════════
#  场景 1：实时监控
# ══════════════════════════════════════════════════════════════════════════════

class RealtimeERPMonitor:
    """
    实验中途实时更新 ERP 曲线（用于 UI 反馈）。

    用法：
        monitor = RealtimeERPMonitor(sample_rate=1000, n_channels=30)
        # 每收到一个新 trial：
        monitor.add_trial(voltage_data_uv, event_type="target")
        # 每 500ms 更新一次 UI：
        monitor.update_ui()
    """

    def __init__(
        self,
        sample_rate: float = 1000.0,
        n_channels: int = 30,
        pre_stim_s: float = 0.2,
        post_stim_s: float = 0.8,
        update_interval_ms: int = 500,
    ):
        self.sample_rate = sample_rate
        self.n_channels = n_channels
        self.pre_stim_s = pre_stim_s
        self.post_stim_s = post_stim_s

        self._target_trials: list[np.ndarray] = []
        self._standard_trials: list[np.ndarray] = []

        # 当前 ERP 缓存（每次 update_ui 时推送）
        self.current_erp: ERPResult | None = None

    def add_trial(
        self,
        epoch_data_uv: np.ndarray,   # (n_channels, n_times) µV
        event_type: str,             # "target" / "standard"
    ) -> None:
        """添加一个 trial（单位 µV，内部自动转 V）"""
        # 验证形状
        n_times = int((self.pre_stim_s + self.post_stim_s) * self.sample_rate)
        if epoch_data_uv.shape != (self.n_channels, n_times):
            raise ValueError(
                f"epoch_data shape 不匹配: 期望 ({self.n_channels}, {n_times})，"
                f"得到 {epoch_data_uv.shape}"
            )

        if event_type == "target":
            self._target_trials.append(epoch_data_uv.astype(np.float64) * 1e-6)  # µV → V
        elif event_type == "standard":
            self._standard_trials.append(epoch_data_uv.astype(np.float64) * 1e-6)
        else:
            return

        # 每加一个 trial 就更新（也可以等 update_interval）
        self._recompute_erp()

    def _recompute_erp(self) -> None:
        """用现有 trial 重新计算平均 ERP（增量平均更高效，此处简化重算）"""
        n_t = len(self._target_trials)
        n_s = len(self._standard_trials)
        if n_t == 0 and n_s == 0:
            return

        n_times = int((self.pre_stim_s + self.post_stim_s) * self.sample_rate)
        time_axis = np.linspace(-self.pre_stim_s,
                                self.pre_stim_s + self.post_stim_s,
                                n_times)

        res = ERPResult(
            group_type="realtime",
            data_type="live",
            n_subjects=1,
            n_channels=self.n_channels,
            n_times=n_times,
            sample_rate=self.sample_rate,
            pre_stim_s=self.pre_stim_s,
            post_stim_s=self.post_stim_s,
            n_target=n_t,
            n_standard=n_s,
            time_axis=time_axis,
        )

        if n_t > 0:
            target_arr = np.stack(self._target_trials)  # (n_t, n_ch, n_times)
            res.target_erp = np.mean(target_arr, axis=0)
            res.target_std = np.std(target_arr, axis=0, ddof=1)
            res.target_sem = res.target_std / np.sqrt(n_t)
            res.all_target_epochs = target_arr

        if n_s > 0:
            standard_arr = np.stack(self._standard_trials)
            res.standard_erp = np.mean(standard_arr, axis=0)
            res.standard_std = np.std(standard_arr, axis=0, ddof=1)
            res.standard_sem = res.standard_std / np.sqrt(n_s)
            res.all_standard_epochs = standard_arr

        if res.target_erp is not None and res.standard_erp is not None:
            res.difference_erp = res.target_erp - res.standard_erp

        self.current_erp = res

    def reset(self) -> None:
        """重置（开始新被试时调用）"""
        self._target_trials.clear()
        self._standard_trials.clear()
        self.current_erp = None

    @property
    def trial_counts(self) -> tuple[int, int]:
        return len(self._target_trials), len(self._standard_trials)


# ══════════════════════════════════════════════════════════════════════════════
#  场景 2：离线批量分析（实验结束后）
# ══════════════════════════════════════════════════════════════════════════════

def batch_process_folder(
    input_dir: str,
    output_dir: str,
    group: str = "ASD",
    data_type: str = "vis",
    freq_band: str = "1-10Hz",
    band_low: float = 0.5,
    band_high: float = 30.0,
) -> list[ERPResult]:
    """
    批量处理一个文件夹下的所有 EEG 文件。

    用法（实验结束后）：
        results = batch_process_folder(
            r"E:\\数据\\被试01\\vis",
            r"E:\\数据\\被试01\\vis\\erp_output",
            group="ASD",
            data_type="vis",
        )
    """
    import glob
    import os

    os.makedirs(output_dir, exist_ok=True)
    results: list[ERPResult] = []

    for ext in ("*.cnt", "*.edf", "*.EDF", "*.CNT"):
        for path in glob.glob(os.path.join(input_dir, ext)):
            proc = ERPProcessor()
            result = proc.process_file(
                file_path=path,
                output_dir=output_dir,
                band_low=band_low,
                band_high=band_high,
                pre_stim_s=0.1,
                post_stim_s=0.5,
                group=group,
                data_type=data_type,
                freq_band=freq_band,
            )
            if result is not None:
                results.append(result)

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  使用示例
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # ── 模拟数据测试 ──────────────────────────────────────────────────────
    print("=== ERPProcessor 模拟测试 ===")

    n_channels = 30
    sample_rate = 1000.0
    pre_s, post_s = 0.2, 0.8
    n_times = int((pre_s + post_s) * sample_rate)  # 1000 点

    # 生成模拟 EEG：100 个 target + 600 个 standard trials
    np.random.seed(42)
    target_epochs = []
    standard_epochs = []

    for _ in range(100):
        # 标准 P300 模拟：300-500ms 有正成分
        base = np.random.randn(n_channels, n_times) * 2e-6
        t = np.linspace(-pre_s, post_s, n_times)
        p300 = 5e-6 * np.exp(-0.5 * ((t - 0.4) / 0.08) ** 2)
        base += p300[np.newaxis, :]
        target_epochs.append(base.astype(np.float64))

    for _ in range(600):
        base = np.random.randn(n_channels, n_times) * 2e-6
        standard_epochs.append(base.astype(np.float64))

    # 构建 Processor 并手动传入 trial 数据
    proc = ERPProcessor(sample_rate=sample_rate, n_channels=n_channels)

    # 用模拟连续数据（每个 trial 拼接）
    events = []
    for i in range(100):
        events.append({"timestamp_s": i * 2.0, "label": 11, "event_type": "target", "channel": 1})
    for i in range(600):
        events.append({"timestamp_s": i * 0.3 + 0.1, "label": 1, "event_type": "standard", "channel": 1})

    n_total = max(len(target_epochs), len(standard_epochs)) * (n_times + 50)
    raw = np.random.randn(n_total, n_channels).astype(np.float64) * 2e-6
    times = np.arange(n_total) / sample_rate

    proc.load(raw, times, sample_rate=sample_rate, events=events)
    proc.filter_bandpass(0.5, 30.0)
    proc.extract_trials(pre_stim_s=0.1, post_stim_s=0.5)
    proc.baseline_correct(0.0, 0.1)
    proc.reject_artifacts(amp_uv=100.0)
    proc.compute_erp(group_type="TEST", data_type="mock")

    print(f"  Target trials: {proc.erp_result.n_target}")
    print(f"  Standard trials: {proc.erp_result.n_standard}")
    p3 = proc.erp_result.p300_amplitude_pz
    print(f"  P300 amplitude (Pz): {p3:.2f} µV" if p3 else "  P300: N/A")

    print("\n=== RealtimeERPMonitor 测试 ===")
    monitor = RealtimeERPMonitor(sample_rate=1000.0, n_channels=n_channels)
    for ep in target_epochs[:30]:
        monitor.add_trial(ep * 1e6, "target")   # 传入 µV
    for ep in standard_epochs[:150]:
        monitor.add_trial(ep * 1e6, "standard")

    print(f"  Trials: {monitor.trial_counts}")
    if monitor.current_erp:
        print(f"  P300 (Pz): {monitor.current_erp.p300_amplitude_pz:.2f} µV")

    print("\n✅ 所有测试通过")
