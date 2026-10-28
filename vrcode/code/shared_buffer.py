import collections
import itertools
import threading
from dataclasses import dataclass
from typing import Deque, List, Optional


@dataclass
class DataPacket:
    """One multi-channel sample with timestamp (ms) and voltages list.

    新增字段（保持向后兼容，缺省值为 0）：
    - trigger: 软件标签显示值；完整事件列表允许同一样本包含多个事件。
    - hardware_trigger: SDK 返回的原始硬件触发值；None 表示无此通道。
    - sample_index: 本次录制中的连续采样位置，独立于硬件计数器初值。
    - sample_counter: eego 放大器内部采样计数器（递增整数），
              用于检测丢包、与 VR 端 wall-clock 对齐偏差等。
    """
    timestamp: int
    voltage: List[float]
    trigger: int = 0
    sample_counter: int = 0
    hardware_trigger: Optional[float] = None
    sample_index: Optional[int] = None


class SharedBuffer:
    """
    Thread-safe bounded queue similar to C++ m_sharedBuffer.

    支持多个订阅者：每个订阅者独立维护读指针，互不干扰。
    生产者写入新数据时，所有订阅者都能在新一轮 snapshot() 中读到。

    用法：
        buf.push_many(packets)
        packets = buf.snapshot("eeg_view")
        ... 处理 ...
        buf.ack("eeg_view")
    """

    def __init__(self, max_capacity: int = 5000) -> None:
        if max_capacity <= 0:
            raise ValueError("max_capacity must be positive")
        self.max_capacity = max_capacity
        self.data_queue: Deque[DataPacket] = collections.deque()
        self.mutex = threading.Lock()
        # Absolute sequence numbers survive eviction from the bounded queue.
        self._start_sequence = 0
        self._next_sequence = 0
        self._consumer_cursors: dict = {}
        self._snapshot_ends: dict = {}

    def push_many(self, packets: List[DataPacket]) -> None:
        with self.mutex:
            for pkt in packets:
                self.data_queue.append(pkt)
                self._next_sequence += 1
            while len(self.data_queue) > self.max_capacity:
                self.data_queue.popleft()
                self._start_sequence += 1

    def drain(self) -> List[DataPacket]:
        """Clear buffered data and outstanding snapshots for all consumers."""
        with self.mutex:
            items = list(self.data_queue)
            self.data_queue.clear()
            self._start_sequence = self._next_sequence
            self._consumer_cursors.clear()
            self._snapshot_ends.clear()
            return items

    def snapshot(self, consumer_id: str = "default", max_packets: Optional[int] = None) -> List[DataPacket]:
        """Return unread retained packets for one consumer.

        A positive max_packets intentionally selects only the newest packets;
        recording consumers must omit it to avoid skipping retained data.
        Call ack after processing, before taking this consumer's next snapshot.
        """
        with self.mutex:
            cursor = max(self._start_sequence,
                         self._consumer_cursors.get(consumer_id, self._start_sequence))
            end = self._next_sequence
            if max_packets is not None and max_packets > 0:
                cursor = max(cursor, end - max_packets)
            self._snapshot_ends[consumer_id] = end
            return list(itertools.islice(self.data_queue,
                                       cursor - self._start_sequence,
                                       end - self._start_sequence))

    def ack(self, consumer_id: str = "default") -> None:
        """Acknowledge only the snapshot, never data pushed during processing."""
        with self.mutex:
            end = self._snapshot_ends.pop(consumer_id, None)
            if end is not None:
                self._consumer_cursors[consumer_id] = end
