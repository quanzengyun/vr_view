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
        pre_stimulus_ms: int = 100,
        post_stimulus_ms: int = 500,
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
        self._target_sum = None
        self._standard_sum = None
        
        # 统计信息
        self.n_target_trials = 0
        self.n_standard_trials = 0
        self.rejected_events = []
        self.rejection_counts = Counter()
        self.preprocessor = None
        self.trial_quality = []
        self.analysis_groups = []
        self.analysis_channel_names = []
        self._analysis_indices = None
        self.analysis_error = None

    def configure_analysis_groups(self, channel_names):
        """坏导插值和重参考仍使用全部通道；四个ERP选项共享五个关键通道的质控试次集。"""
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

    def configure_preprocessing(self, channel_names, config=None, model_function=None):
        from erp_preprocessing import OnlineERPPreprocessor
        if self.preprocessor is not None:
            self.preprocessor.reset()  # 使上一记录的后台初始化结果失效。
        self.preprocessor = OnlineERPPreprocessor(channel_names, self.sample_rate, config, model_function)

    @property
    def preprocessing_pending(self):
        return self.preprocessor is not None and self.preprocessor.state in {"collecting", "processing"}
    
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

    def _record_rejection(self, event, reason):
        self.rejected_events.append(dict(timestamp_ms=event.timestamp_ms,
                                         event_type=event.event_type, reason=reason))
        self.rejection_counts[reason] += 1

    def finish(self, data_buffer):
        """停止时先完成可用试次，再明确记录无法等到完整后窗的事件。"""
        if self.preprocessor is not None:
            self.preprocessor.finish_collection()
            if self.preprocessor.continuous_output and self.preprocessing_pending:
                return  # MainWindow drains the cleaned continuous backlog after initialization.
        if data_buffer:
            self.process_data_packet(data_buffer[-1][0], data_buffer[-1][1], data_buffer)
        with self.event_lock:
            remaining = list(self.event_queue)
            self.event_queue.clear()
        for event in remaining:
            reason = ('preprocessing_unavailable' if self.preprocessor is not None
                      and self.preprocessor.state != 'ready' else 'incomplete_post_stimulus_at_stop')
            self._record_rejection(event, reason)
    
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
            epoch_data *= 1e6  # Continuous preprocessing already performed in volts.
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
            if self._target_sum is None:
                self._target_sum = epoch_data.copy()
            else:
                self._target_sum += epoch_data
            self.target_erp = self._target_sum / self.n_target_trials
        elif event.event_type.lower() == 'standard':
            self.standard_epochs.append(epoch_data)
            self.n_standard_trials += 1
            if self._standard_sum is None:
                self._standard_sum = epoch_data.copy()
            else:
                self._standard_sum += epoch_data
            self.standard_erp = self._standard_sum / self.n_standard_trials
        else:
            return "unsupported_event_type"

        if self.target_erp is not None and self.standard_erp is not None:
            self.p300_erp = self.target_erp - self.standard_erp
        return "accepted"
    
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
        result = {
            'time': self.time_axis_ms,
            'n_target': self.n_target_trials,
            'n_standard': self.n_standard_trials,
            'n_rejected': len(self.rejected_events),
            'unit': 'uV' if self.preprocessor is not None else 'V',
            'preprocessing_status': '' if self.preprocessor is None else self.preprocessor.status_text(),
        }
        with self.event_lock:
            result['n_pending'] = len(self.event_queue)
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
        self._target_sum = None
        self._standard_sum = None
        self.n_target_trials = 0
        self.n_standard_trials = 0
        self.rejected_events.clear()
        self.rejection_counts.clear()
        self.trial_quality.clear()
        if self.preprocessor is not None:
            self.preprocessor.reset()

    def export_results(self, prefix):
        """保存已完成的在线结果和坏导插值矩阵，原始EDF/CSV不修改。"""
        import json
        if self.preprocessing_pending:
            raise RuntimeError("坏导插值初始化尚未就绪")
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
            # ERP导出仅包含关键通道。预处理输入通道单独命名，不与分析维度混淆。
            if self.preprocessor is not None:
                arrays['preprocessing_channel_names'] = np.asarray(self.preprocessor.channel_names)
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
            arrays['bad_channel_interpolation'] = model['interpolation']
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
                        pending_events=len(self.event_queue),
                        preprocessing=None if self.preprocessor is None else self.preprocessor.metadata())
        with open(prefix + '.erp.json', 'w', encoding='utf-8') as output:
            json.dump(metadata, output, ensure_ascii=False, indent=2)

