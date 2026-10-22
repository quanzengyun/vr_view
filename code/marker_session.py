"""线程安全的软件接收事件与录制采样位置。硬件触发不参与标签推断。"""
import collections
import threading
import time

VALID_LABELS = frozenset(range(1, 9)) | {11, 22, 33, 44, 55, 66, 77, 88, 89}


def encode_marker(event_type, channel):
    kind = str(event_type).lower()
    if kind in {"response", "y"}:
        return 89
    if 1 <= int(channel or 0) <= 8:
        return int(channel) * 11 if kind == "target" else int(channel) if kind == "standard" else 0
    return 0


class MarkerSession:
    """以 PC 接收时刻定位软件事件；首批数据接收时估算采样时钟锚点。

    不宣称此时刻等于 VR 实际刺激呈现时刻。保留接收时刻、定位时刻和状态，
    多个事件可以指向同一样本，不能从单根 TRIG 通道重建这类事件。
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._active = False
        self._accepting = False
        self._packets = []
        self._events = []
        self._notifications = collections.deque()
        self._pending = []
        self._anchor_ns = None
        self._anchor_ms = None
        self.sample_rate = 1000.0

    @property
    def active(self):
        with self._lock:
            return self._active

    def start(self, sample_rate):
        if sample_rate <= 0:
            raise ValueError("采样率必须大于 0")
        with self._lock:
            self.sample_rate = float(sample_rate)
            self._packets = []
            self._events = []
            self._pending = []
            self._notifications.clear()
            self._anchor_ns = self._anchor_ms = None
            self._active = True
            self._accepting = True

    def submit(self, label, event_type="", channel=0, source="vr",
               received_ms=None, received_monotonic_ns=None):
        if int(label) not in VALID_LABELS:
            return None
        received_ms = time.time_ns() / 1e6 if received_ms is None else received_ms
        received_monotonic_ns = (time.monotonic_ns() if received_monotonic_ns is None
                                 else received_monotonic_ns)
        with self._lock:
            if not self._active or not self._accepting:
                return None
            event = dict(event_id=len(self._events) + 1, label=int(label),
                         event_type=event_type, channel=int(channel or 0), source=source,
                         timestamp_ms=float(received_ms),
                         received_monotonic_ns=int(received_monotonic_ns),
                         timing_basis="pc_receive_estimated_sample_clock", status="pending",
                         sample_index=None, sample_counter=None)
            self._events.append(event)
            self._pending.append(event)
            self._resolve_pending()
            return dict(event)

    def append_batch(self, packets, received_ms=None, received_monotonic_ns=None):
        if not packets:
            return
        received_ms = time.time_ns() / 1e6 if received_ms is None else received_ms
        received_monotonic_ns = (time.monotonic_ns() if received_monotonic_ns is None
                                 else received_monotonic_ns)
        with self._lock:
            if not self._active:
                return
            if self._anchor_ns is None:
                # 首批末样本约对应数据返回时刻；不把整批样本时间放到未来。
                span_ms = (len(packets) - 1) * 1000.0 / self.sample_rate
                self._anchor_ns = int(received_monotonic_ns - span_ms * 1e6)
                self._anchor_ms = float(received_ms) - span_ms
            first = len(self._packets)
            for k, packet in enumerate(packets):
                packet.sample_index = first + k
                packet.timestamp = self._anchor_ms + packet.sample_index * 1000.0 / self.sample_rate
                packet.trigger = 0  # 软件可视化通道；原始硬件值在 hardware_trigger 中。
            self._packets.extend(packets)
            self._resolve_pending()

    def _resolve_pending(self):
        if self._anchor_ns is None:
            return
        remaining = []
        for event in self._pending:
            index = int(round((event["received_monotonic_ns"] - self._anchor_ns)
                              * self.sample_rate / 1e9))
            if index < 0:
                event.update(status="before_first_sample", reason="事件早于首个保留样本")
            elif index >= len(self._packets):
                remaining.append(event)
            else:
                packet = self._packets[index]
                event.update(status="located", sample_index=index,
                             sample_counter=packet.sample_counter,
                             sample_time_ms=packet.timestamp,
                             onset_sec=index / self.sample_rate,
                             alignment_error_ms=packet.timestamp - event["timestamp_ms"])
                # 同一样本的完整事件由事件列表保留，TRIG 仅容纳其中一个标签。
                if packet.trigger == 0:
                    packet.trigger = event["label"]
                self._notifications.append(dict(event))
        self._pending = remaining

    def take_notifications(self):
        with self._lock:
            result = list(self._notifications)
            self._notifications.clear()
            return result

    def finish(self):
        with self._lock:
            self._active = False
            self._accepting = False
            self._resolve_pending()
            for event in self._pending:
                event.update(status="after_last_sample" if self._packets else "no_samples",
                             reason="停止录制时尚无对应的采样数据")
            self._pending.clear()

    def stop_accepting(self):
        with self._lock:
            self._accepting = False

    def snapshot(self):
        with self._lock:
            return list(self._packets), [dict(event) for event in self._events]

    def packets_since(self, index):
        with self._lock:
            return list(self._packets[index:])
