"""
EEGView 内部子模块：录制生命周期管理。

原本在 EEGViewWidget 里的：
- start_record / add_record_event / stop_and_save_record / stop_and_save_record_async
- 配套的 _record_enabled / _record_subject / _record_packets / _record_events / _record_meta

抽出后 EEGViewWidget 通过组合方式持有 Recorder 实例。
"""

import threading
from typing import Callable, List, Optional

from PyQt5 import QtCore

from shared_buffer import DataPacket
from marker_session import MarkerSession

from .persister import save_record_to_files


class Recorder(QtCore.QObject):
    """
    录制生命周期：start / add_event / append_packets / stop_and_save(async)

    原始数据和事件由共享 MarkerSession 持有，绘图可以跳帧而不影响录制。
    持久化委托给 persister.save_record_to_files（EDF / CSV / events.json）。
    """
    saved = QtCore.pyqtSignal(object)

    def __init__(self, session=None) -> None:
        super().__init__()
        self.session = session if session is not None else MarkerSession()
        self._save_callback = None
        self.saved.connect(self._on_saved)
        self._enabled: bool = False
        self._subject: str = ""
        self._meta: dict = {}
        # 录制期参数（停止时快照给后台线程，避免主线程中途修改）
        self._channel_names: List[str] = []
        self._sample_rate: float = 1000.0
        self._amplitude_scale: float = 1e6
        self._filter_low_hz: float = 0.0
        self._filter_high_hz: float = 30.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def subject(self) -> str:
        return self._subject

    def start(
        self,
        subject: str,
        channel_names: List[str],
        sample_rate: float,
        amplitude_scale: float = 1e6,
        meta: Optional[dict] = None,
        filter_low_hz: float = 0.0,
        filter_high_hz: float = 30.0,
    ) -> None:
        """开启记录，清空历史并设置被试名及元信息。"""
        self._subject = subject.strip()
        self._meta = meta or {}
        self._meta["marker_schema"] = "sample_index_v1"
        self._channel_names = list(channel_names)
        self._sample_rate = float(sample_rate)
        self._amplitude_scale = float(amplitude_scale)
        self._filter_low_hz = float(filter_low_hz)
        self._filter_high_hz = float(filter_high_hz)
        self._enabled = True
        self.session.start(sample_rate)

    def add_event(self, event_type: str, channel: int, label: int, source: str = "vr") -> None:
        """记录一条 VR / 手动事件（带完整 label）。"""
        if not self._enabled:
            return
        return self.session.submit(label, event_type=event_type, channel=channel, source=source)

    def append_packets(self, packets: List[DataPacket]) -> None:
        """录制时由 EEGViewWidget._update_view 调用，把这一批 packet 追加。"""
        if self._enabled and packets and packets[0].sample_index is None:
            self.session.append_batch(packets)

    def reset(self) -> None:
        """丢弃当前录制缓存（reset_view 时清空）。"""
        self._enabled = False
        self.session.finish()

    # ---- 保存 ----

    def stop_and_save(self, save_dir: str) -> Optional[str]:
        """同步停止 + 保存（保留旧接口）。"""
        self.session.finish()
        self._enabled = False
        packets, events = self.session.snapshot()
        return save_record_to_files(
            packets=packets,
            events=events,
            channel_names=self._channel_names,
            sample_rate=self._sample_rate,
            amplitude_scale=self._amplitude_scale,
            save_dir=save_dir,
            subject=self._subject,
            meta=self._meta,
            filter_low_hz=self._filter_low_hz,
            filter_high_hz=self._filter_high_hz,
        )

    def stop_and_save_async(self, save_dir: str, on_done: Optional[Callable[[Optional[str]], None]] = None) -> None:
        """
        异步停止 + 保存 —— 防止 EDF 导出阻塞主线程导致 UI 卡死。
        停止接收后，在会话锁下取得数据和事件快照；完成后由 Qt 信号回到 GUI 线程。
        """
        self.session.finish()
        self._enabled = False
        packets_snapshot, events_snapshot = self.session.snapshot()
        self._save_callback = on_done
        channel_names = self._channel_names
        sample_rate = self._sample_rate
        amplitude_scale = self._amplitude_scale
        subject = self._subject
        meta = self._meta
        filter_low_hz = self._filter_low_hz
        filter_high_hz = self._filter_high_hz

        def _worker():
            try:
                csv_path = save_record_to_files(
                    packets=packets_snapshot,
                    events=events_snapshot,
                    channel_names=channel_names,
                    sample_rate=sample_rate,
                    amplitude_scale=amplitude_scale,
                    save_dir=save_dir,
                    subject=subject,
                    meta=meta,
                    filter_low_hz=filter_low_hz,
                    filter_high_hz=filter_high_hz,
                )
                self.saved.emit(csv_path)
            except Exception:
                import traceback as _tb
                _tb.print_exc()
                self.saved.emit(None)

        threading.Thread(target=_worker, daemon=True).start()

    @QtCore.pyqtSlot(object)
    def _on_saved(self, path):
        if self._save_callback is not None:
            callback, self._save_callback = self._save_callback, None
            callback(path)
