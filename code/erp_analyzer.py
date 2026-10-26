"""
实时 ERP (P300) 分析模块
基于 MATLAB 代码 a2_erp_raw.m 转换而来
"""

import numpy as np
from typing import List, Optional, Dict, Tuple
from collections import deque
from collections import Counter
from bisect import bisect_left
from dataclasses import dataclass
import threading

# DataPacket 类型仅作类型提示参考；本模块通过 duck typing 访问 .trigger/.voltage 字段
try:
    from shared_buffer import DataPacket  # noqa: F401
except ImportError:
    DataPacket = None


@dataclass
class EventMarker:
    """事件标记：target 或 standard 刺激"""
    timestamp_ms: int  # 事件发生的时间戳（毫秒）
    event_type: str     # 'target' 或 'standard'
    channel: Optional[int] = None  # 可选：特定通道


class ERPAnalyzer:
    """
    实时 ERP 分析器
    
    功能：
    - 接收事件标记（target/standard）
    - 分段数据（epoch）
    - 计算平均 ERP
    - 实时更新 P300 波形
    """
    
    def __init__(
        self,
        sample_rate: float = 1000.0,
        n_channels: int = 32,
        pre_stimulus_ms: int = 200,   # 刺激前时间 (ms)，与 offline_erp.py 保持一致
        post_stimulus_ms: int = 800,  # 刺激后时间 (ms)，与 offline_erp.py 保持一致
        baseline_correction: bool = True,  # 基线校正
    ):
        self.sample_rate = sample_rate
        self.n_channels = n_channels
        self.pre_stimulus_ms = pre_stimulus_ms
        self.post_stimulus_ms = post_stimulus_ms
        self.baseline_correction = baseline_correction
        
        # 计算时间窗口（样本数）
        self.pre_stimulus_samples = int(pre_stimulus_ms * sample_rate / 1000.0)
        self.post_stimulus_samples = int(post_stimulus_ms * sample_rate / 1000.0)
        self.epoch_length = self.pre_stimulus_samples + self.post_stimulus_samples
        
        # 时间轴（毫秒），范围 [ -pre_stimulus_ms,  +post_stimulus_ms )
        # 刺激（stimulus）始终在 0ms，与标准 ERP 惯例一致（负=刺激前，正=刺激后）
        self.time_axis_ms = np.arange(
            -pre_stimulus_ms,
            pre_stimulus_ms + post_stimulus_ms,
            step=1000.0 / sample_rate,
        )[:self.epoch_length]
        
        # 事件队列（线程安全）
        self.event_queue: deque = deque()
        self.event_lock = threading.Lock()
        
        # ERP 数据存储
        # target_epochs: List[np.ndarray]  # shape: (n_channels, epoch_length)
        # standard_epochs: List[np.ndarray]
        self.target_epochs: List[np.ndarray] = []
        self.standard_epochs: List[np.ndarray] = []
        
        # 平均 ERP（实时更新）
        self.target_erp: Optional[np.ndarray] = None  # shape: (n_channels, epoch_length)
        self.standard_erp: Optional[np.ndarray] = None
        self.p300_erp: Optional[np.ndarray] = None  # target - standard
        
        # 统计信息
        self.n_target_trials = 0
        self.n_standard_trials = 0
        self.rejected_events = []
        self.rejection_counts = Counter()
        self.preprocessor = None
        self._deferred_epochs = []
        self.trial_quality = []
        self.analysis_groups = []
        self.analysis_channel_names = []
        self._analysis_indices = None
        self.analysis_error = None

    def configure_analysis_groups(self, channel_names):
        """ICA/参考仍使用全部通道；四个ERP选项共享五个关键通道的质控试次集。"""
        definitions = [('Fz + FC1 + FC2 平均', ['Fz','FC1','FC2']),
                       ('Cz', ['Cz']), ('Pz', ['Pz']), ('Fz', ['Fz'])]
        lookup = {name.casefold(): i for i,name in enumerate(channel_names)}
        required = ['Fz','FC1','FC2','Cz','Pz']
        missing = [name for name in required if name.casefold() not in lookup]
        self.analysis_error = '缺少ERP分析通道：' + ', '.join(missing) if missing else None
        self.analysis_channel_names = required
        self._analysis_indices = None if missing else np.array([lookup[n.casefold()] for n in required])
        self.analysis_groups = [dict(label=label, channels=names,
            indices=[lookup[n.casefold()] for n in names if n.casefold() in lookup],
            available=all(n.casefold() in lookup for n in names)) for label,names in definitions]

    def configure_preprocessing(self, channel_names, config=None, fit_function=None):
        from erp_preprocessing import OnlineERPPreprocessor
        if self.preprocessor is not None:
            self.preprocessor.reset()  # 使上一记录的后台拟合结果失效。
        self.preprocessor = OnlineERPPreprocessor(channel_names, self.sample_rate, config, fit_function)

    def poll_preprocessing(self):
        if self.preprocessor is None or not self._deferred_epochs:
            return
        state = self.preprocessor.state
        if state not in {"ready", "error", "insufficient"}:
            return
        pending, self._deferred_epochs = self._deferred_epochs, []
        for event, epoch in pending:
            if state != "ready":
                self._record_rejection(event, "eye_model_unavailable")
                continue
            cleaned = self.preprocessor.clean_epoch_uv(epoch)
            outcome = self._store_epoch(event, cleaned)
            if outcome != "accepted":
                self._record_rejection(event, outcome)

    @property
    def preprocessing_pending(self):
        return self.preprocessor is not None and self.preprocessor.state in {"collecting", "fitting"}
    
    def add_event(self, event: EventMarker) -> None:
        """添加事件标记"""
        with self.event_lock:
            self.event_queue.append(event)
    
    def add_event_manual(self, timestamp_ms: int, event_type: str) -> None:
        """手动添加事件标记（便捷方法）"""
        self.add_event(EventMarker(timestamp_ms=timestamp_ms, event_type=event_type))
    
    def process_data_packet(
        self,
        timestamp_ms: int,
        voltage: np.ndarray,
        data_buffer: List[Tuple[int, np.ndarray]]
    ) -> None:
        """
        处理数据包，检查是否有事件需要分段

        Args:
            timestamp_ms: 当前数据包的时间戳
            voltage: 当前数据包的电压值 (n_channels,)
            data_buffer: 数据缓冲区，存储最近的数据包 [(timestamp_ms, voltage), ...]
        """
        if not data_buffer:
            return
        # 不按“事件已发生”出队；窗口不足时必须保留，并在后续数据到达后重试。
        # 遍历本轮全部事件，避免乱序通知让较晚事件阻塞已齐全的较早事件。
        with self.event_lock:
            events = list(self.event_queue)
            self.event_queue.clear()
        waiting = []
        for event in events:
            outcome = self._extract_epoch(event, data_buffer)
            if outcome == "pending":
                waiting.append(event)
            elif outcome != "accepted":
                self._record_rejection(event, outcome)
        with self.event_lock:
            self.event_queue.extendleft(reversed(waiting))
        self.poll_preprocessing()

    def _record_rejection(self, event, reason):
        self.rejected_events.append(dict(timestamp_ms=event.timestamp_ms,
                                         event_type=event.event_type, reason=reason))
        self.rejection_counts[reason] += 1

    def finish(self, data_buffer):
        """停止时先完成可用试次，再明确记录无法等到完整后窗的事件。"""
        if self.preprocessor is not None:
            self.preprocessor.finish_collection()
        if data_buffer:
            self.process_data_packet(data_buffer[-1][0], data_buffer[-1][1], data_buffer)
        with self.event_lock:
            remaining = list(self.event_queue)
            self.event_queue.clear()
        for event in remaining:
            self._record_rejection(event, "incomplete_post_stimulus_at_stop")
        self.poll_preprocessing()
    
    def _extract_epoch(
        self,
        event: EventMarker,
        data_buffer: List[Tuple[int, np.ndarray]]
    ) -> str:
        """
        从数据缓冲区中提取 epoch

        Args:
            event: 事件标记
            data_buffer: 数据缓冲区，按时间顺序排列
        """
        if not data_buffer:
            return "pending"

        # 找到事件时间点附近的数据
        event_idx = bisect_left(data_buffer, event.timestamp_ms, key=lambda item: item[0])
        if event_idx == len(data_buffer):
            return "pending"

        # 检查是否有足够的数据（前后都需要）
        if event_idx < self.pre_stimulus_samples:
            return "insufficient_pre_stimulus"
        if event_idx + self.post_stimulus_samples > len(data_buffer):
            return "pending"
        
        # 提取 epoch
        start_idx = event_idx - self.pre_stimulus_samples
        end_idx = event_idx + self.post_stimulus_samples
        
        segment = data_buffer[start_idx:end_idx]
        if any(np.asarray(voltage).shape != (self.n_channels,) for _, voltage in segment):
            return "channel_count_mismatch"
        times = np.array([ts for ts, _ in segment], dtype=float)
        if np.any(np.abs(np.diff(times) - 1000.0 / self.sample_rate) > 0.001):
            return "discontinuous_samples"
        epoch_data = np.stack([voltage for _, voltage in segment], axis=1).astype(float)
        if not np.all(np.isfinite(epoch_data)):
            return "nonfinite_samples"
        if self.preprocessor is not None:
            # 已完整提取的试次单独暂存，拟合期间不再依赖会滚动淘汰的缓冲区。
            self._deferred_epochs.append((event, epoch_data))
            return "accepted"
        return self._store_epoch(event, epoch_data)

    def _store_epoch(self, event, epoch_data):
        
        # 基线校正（使用 pre-stimulus 期间的平均值）
        if self.baseline_correction:
            baseline = np.mean(epoch_data[:, :self.pre_stimulus_samples], axis=1, keepdims=True)
            epoch_data = epoch_data - baseline
        if self.preprocessor is not None:
            # MATLAB的振幅与方差规则；此处数据已经是微伏，var使用N-1分母。
            if self.analysis_error:
                return 'analysis_channels_unavailable'
            indices = self._analysis_indices if self._analysis_indices is not None else np.arange(self.n_channels)
            quality_data = epoch_data[indices]
            peak_channel = int(indices[np.argmax(np.max(np.abs(quality_data), axis=1))])
            amplitude = float(np.max(np.abs(quality_data)))
            variance = float(np.var(quality_data, ddof=1))
            quality = dict(timestamp_ms=event.timestamp_ms, event_type=event.event_type,
                           max_amplitude_uv=amplitude, variance_uv2=variance,
                           peak_channel=self.preprocessor.channel_names[peak_channel], status='accepted')
            self.trial_quality.append(quality)
            if amplitude > self.preprocessor.config.amplitude_uv:
                quality['status'] = 'amplitude_artifact'
                return "amplitude_artifact"
            if variance > self.preprocessor.config.variance_uv2:
                quality['status'] = 'variance_artifact'
                return "variance_artifact"
        
        # 添加到对应的列表
        if event.event_type.lower() == 'target':
            self.target_epochs.append(epoch_data)
            self.n_target_trials += 1
        elif event.event_type.lower() == 'standard':
            self.standard_epochs.append(epoch_data)
            self.n_standard_trials += 1
        else:
            return "unsupported_event_type"

        # 更新平均 ERP
        self._update_average_erp()
        return "accepted"
    
    def _update_average_erp(self) -> None:
        """更新平均 ERP"""
        if self.target_epochs:
            # 计算 target 平均 ERP
            target_array = np.stack(self.target_epochs, axis=0)  # (n_trials, n_channels, epoch_length)
            self.target_erp = np.mean(target_array, axis=0)  # (n_channels, epoch_length)
        
        if self.standard_epochs:
            # 计算 standard 平均 ERP
            standard_array = np.stack(self.standard_epochs, axis=0)  # (n_trials, n_channels, epoch_length)
            self.standard_erp = np.mean(standard_array, axis=0)  # (n_channels, epoch_length)
        
        # 计算 P300 (target - standard)
        if self.target_erp is not None and self.standard_erp is not None:
            self.p300_erp = self.target_erp - self.standard_erp
    
    def get_erp_data(self, channel_idx: Optional[int] = None, group_idx: Optional[int] = None) -> Dict[str, np.ndarray]:
        """
        获取指定通道的 ERP 数据
        
        Args:
            channel_idx: 通道索引（None 表示所有通道的平均）
        
        Returns:
            dict: {
                'time': 时间轴（毫秒）,
                'target': target ERP,
                'standard': standard ERP,
                'p300': P300 (target - standard),
                'n_target': target 试次数,
                'n_standard': standard 试次数
            }
        """
        self.poll_preprocessing()
        result = {
            'time': self.time_axis_ms,
            'n_target': self.n_target_trials,
            'n_standard': self.n_standard_trials,
            'n_rejected': len(self.rejected_events),
            'unit': 'uV' if self.preprocessor is not None else 'V',
            'preprocessing_status': '' if self.preprocessor is None else self.preprocessor.status_text(),
        }
        with self.event_lock:
            result['n_pending'] = len(self.event_queue) + len(self._deferred_epochs)
        if self.analysis_error:
            result['preprocessing_status'] += '；' + self.analysis_error
        if group_idx is not None:
            group = self.analysis_groups[group_idx]
            result['analysis_group'] = group['label']
            if group['available']:
                for key, data in [('target',self.target_erp),('standard',self.standard_erp),('p300',self.p300_erp)]:
                    if data is not None:
                        result[key] = data[group['indices']].mean(axis=0)
            return result
        
        if channel_idx is not None:
            # 单个通道
            if self.target_erp is not None:
                result['target'] = self.target_erp[channel_idx, :]
            if self.standard_erp is not None:
                result['standard'] = self.standard_erp[channel_idx, :]
            if self.p300_erp is not None:
                result['p300'] = self.p300_erp[channel_idx, :]
        else:
            # 所有通道的平均
            if self.target_erp is not None:
                result['target'] = np.mean(self.target_erp, axis=0)
            if self.standard_erp is not None:
                result['standard'] = np.mean(self.standard_erp, axis=0)
            if self.p300_erp is not None:
                result['p300'] = np.mean(self.p300_erp, axis=0)
        
        return result
    
    def reset(self) -> None:
        """重置分析器"""
        with self.event_lock:
            self.event_queue.clear()
        self.target_epochs.clear()
        self.standard_epochs.clear()
        self.target_erp = None
        self.standard_erp = None
        self.p300_erp = None
        self.n_target_trials = 0
        self.n_standard_trials = 0
        self.rejected_events.clear()
        self.rejection_counts.clear()
        self._deferred_epochs.clear()
        self.trial_quality.clear()
        if self.preprocessor is not None:
            self.preprocessor.reset()

    def export_results(self, prefix):
        """保存已完成的在线结果和眼电模型，原始EDF/CSV不修改。"""
        import json
        self.poll_preprocessing()
        if self.preprocessing_pending:
            raise RuntimeError("眼电模型尚未就绪")
        shape = (0, self.n_channels, self.epoch_length)
        arrays = dict(time_ms=self.time_axis_ms,
                      target_epochs_uv=np.stack(self.target_epochs) if self.target_epochs else np.empty(shape),
                      standard_epochs_uv=np.stack(self.standard_epochs) if self.standard_epochs else np.empty(shape),
                      target_uv=self.target_erp if self.target_erp is not None else np.empty((0,)),
                      standard_uv=self.standard_erp if self.standard_erp is not None else np.empty((0,)),
                      difference_uv=self.p300_erp if self.p300_erp is not None else np.empty((0,)))
        if self.preprocessor is None:
            for name in ('target_epochs_uv','standard_epochs_uv','target_uv','standard_uv','difference_uv'):
                arrays[name] = arrays[name] * 1e6
        if self.preprocessor is not None:
            arrays.update(fir_taps=self.preprocessor.taps,
                          channel_names=np.asarray(self.preprocessor.channel_names))
        if self.analysis_groups and not self.analysis_error:
            indices = self._analysis_indices
            # ERP导出仅包含关键通道。ICA输入通道单独命名，不与分析维度混淆。
            if self.preprocessor is not None:
                arrays['ica_channel_names'] = np.asarray(self.preprocessor.channel_names)
            arrays['channel_names'] = np.asarray(self.analysis_channel_names)
            for key in ('target_epochs_uv','standard_epochs_uv'):
                arrays[key] = arrays[key][:,indices,:]
            for key in ('target_uv','standard_uv','difference_uv'):
                full = arrays[key]
                arrays['group_'+key] = (np.stack([full[g['indices']].mean(axis=0) for g in self.analysis_groups])
                                        if full.ndim == 2 else np.empty((0,self.epoch_length)))
                if full.ndim == 2:
                    arrays[key] = full[indices]
            arrays['analysis_group_labels'] = np.asarray([g['label'] for g in self.analysis_groups])
        if self.preprocessor is not None and self.preprocessor.model is not None:
            model = self.preprocessor.model
            arrays.update(ica_projection=model['projection'], ica_offset_v=model['offset'],
                          iclabel_probabilities=model['probabilities'],
                          excluded_components_zero_based=np.asarray(model['excluded'], dtype=int))
        np.savez_compressed(prefix + '.erp.npz', **arrays)
        metadata = dict(sample_rate=self.sample_rate, output_unit='uV', window_ms=[-self.pre_stimulus_ms, self.post_stimulus_ms],
                        window_endpoint='exclusive', baseline_ms=[-self.pre_stimulus_ms,0],
                        target_trials=self.n_target_trials, standard_trials=self.n_standard_trials,
                        rejected_events=self.rejected_events, rejection_counts=dict(self.rejection_counts),
                        trial_quality=self.trial_quality,
                        analysis_channels=self.analysis_channel_names,
                        analysis_groups=[dict(label=g['label'],channels=g['channels']) for g in self.analysis_groups],
                        quality_policy='shared_trial_mask_over_analysis_channels' if self.analysis_groups else 'all_channels',
                        analysis_error=self.analysis_error,
                        pending_events=len(self.event_queue)+len(self._deferred_epochs),
                        preprocessing=None if self.preprocessor is None else self.preprocessor.metadata())
        with open(prefix + '.erp.json', 'w', encoding='utf-8') as output:
            json.dump(metadata, output, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 基于硬件 trigger 通道的 epoch 提取（更精确）
    # ------------------------------------------------------------------
    def extract_epochs_from_trigger(
        self,
        packets,                    # List[DataPacket]
        trigger_to_event: dict,     # {trigger_value: 'target'/'standard', ...}
        eeg_view_widget=None,       # 可选：传入后用 view 的 display_data 缓存取样本
        get_voltage_fn=None,        # 可选：自定义取电压的函数 (i) -> np.ndarray
    ):
        """
        扫描 DataPacket 序列，检测 trigger 上升沿，根据 trigger 值映射到 target/standard。

        Args:
            packets: 时间顺序的 DataPacket 列表（含 trigger 字段）
            trigger_to_event: dict，key=trigger 整数（来自 EegoTrigger.marker_from_event），
                              value='target' / 'standard'
            eeg_view_widget: 可选，传入后用其 buffer 取真实 EEG 电压；不传则用
                             packets[i].voltage（电压为 V，已在 widget 显示时转换为 µV）
            get_voltage_fn: 可选，自定义 (packet_index) -> np.ndarray 电压获取函数，
                            优先级高于 eeg_view_widget

        Returns:
            (n_target, n_standard) 新增的 epoch 计数
        """
        if not packets:
            return 0, 0
        if self.preprocessor is not None:
            raise RuntimeError("启用预处理时必须通过软件事件队列分段，不能绕过眼电模型")

        added_target = 0
        added_standard = 0
        last_trigger = 0
        ring: list = []  # 临时 ring buffer：[(ts_ms, voltage), ...]

        def get_v(i: int):
            if get_voltage_fn is not None:
                return get_voltage_fn(i)
            if eeg_view_widget is not None and hasattr(eeg_view_widget, 'display_data'):
                return eeg_view_widget.display_data[i]
            return packets[i].voltage

        for i, p in enumerate(packets):
            cur_trigger = getattr(p, 'trigger', 0) or 0
            ring.append(p)
            # 保持 ring buffer 不超过 epoch 长度（避免内存爆）
            max_ring = self.epoch_length + 50
            if len(ring) > max_ring:
                ring = ring[-max_ring:]
                # 重新校准 ring 的索引起点
                ring_start = i - len(ring) + 1
            else:
                ring_start = i - len(ring) + 1

            # 检测 trigger 上升沿（0 -> 非0）
            if cur_trigger > 0 and last_trigger == 0:
                # marker 落在 ring 的末尾（也就是 ring[-1]）
                # 需要 ring 至少有 epoch_length 个样本；不足就等
                if len(ring) < self.epoch_length:
                    last_trigger = cur_trigger
                    continue
                # 取后 epoch_length 个样本
                seg = ring[-self.epoch_length:]
                # 找事件类型
                evt = trigger_to_event.get(cur_trigger)
                if evt is None:
                    last_trigger = cur_trigger
                    continue
                # 提取电压矩阵
                n_ch = self.n_channels
                epoch_data = np.zeros((n_ch, self.epoch_length), dtype=float)
                for k, pp in enumerate(seg):
                    v = pp.voltage
                    if len(v) == n_ch:
                        epoch_data[:, k] = v
                # 基线校正
                if self.baseline_correction:
                    base = np.mean(epoch_data[:, :self.pre_stimulus_samples], axis=1, keepdims=True)
                    epoch_data = epoch_data - base
                # 入库
                if evt == 'target':
                    self.target_epochs.append(epoch_data)
                    self.n_target_trials += 1
                    added_target += 1
                elif evt == 'standard':
                    self.standard_epochs.append(epoch_data)
                    self.n_standard_trials += 1
                    added_standard += 1
            last_trigger = cur_trigger

        if added_target or added_standard:
            self._update_average_erp()
        return added_target, added_standard
