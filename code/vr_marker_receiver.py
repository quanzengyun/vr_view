"""
VR 标记接收器
接收来自 Unity VR 的 TCP 标记，并转换为 ERP 事件
"""

import socket
import threading
import time
from typing import Optional, Callable
from queue import Queue
from erp_analyzer import EventMarker
from vr_video_receiver import VRVideoReceiver
from diag import log, log_exc


class VRMarkerReceiver:
    """
    VR 标记接收器
    
    功能：
    - 监听 TCP 端口，接收 VR 发送的标记
    - 解析标记格式（"X1"-"X8" = Target, "G1"-"G8" = Standard）
    - 将标记转换为 ERP 事件并回调
    """
    
    def __init__(
        self,
        host: str = "0.0.0.0",  # 监听所有网络接口
        port: int = 12345,
        on_marker_received: Optional[Callable[[EventMarker], None]] = None
    ):
        self.host = host
        self.port = port
        self.on_marker_received = on_marker_received
        
        self.socket: Optional[socket.socket] = None
        self.is_running = False
        self.receiver_thread: Optional[threading.Thread] = None
        self.is_connected = False  # VR 客户端连接状态
        self.client_socket: Optional[socket.socket] = None  # 当前连接的客户端socket

        # 视频接收器（独立 TCP，端口 12346，与 trigger 完全分离）
        # 旧版本是把视频塞在 trigger 同一个 socket 里，现在拆开避免通信错乱 + 卡顿
        self.video_receiver = VRVideoReceiver(
            host="0.0.0.0",
            port=12346,
            on_frame=None,  # 业务回调由 set_video_frame_callback 设置
        )
        self._video_frame_callback: Optional[Callable] = None
        self._gaze_callback: Optional[Callable[[float, float], None]] = None
        self._gaze_rect_callback: Optional[Callable[[float, float, float, float], None]] = None
        self._gaze_target_id_callback: Optional[Callable[[int], None]] = None
        
        # 标记队列（线程安全）
        self.marker_queue: Queue = Queue()
        
        # 统计信息
        self.total_markers_received = 0
        self.target_count = 0
        self.standard_count = 0
        self.response_count = 0
        self._text_buffer = b""
    
    def start(self) -> bool:
        """启动 TCP 服务器"""
        if self.is_running:
            return False

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.socket.settimeout(1.0)

            self.is_running = True
            self.receiver_thread = threading.Thread(target=self._receiver_loop, daemon=True)
            self.receiver_thread.start()

            # 启动独立视频流接收（端口 12346，与 trigger 12345 完全分离）
            try:
                self.video_receiver.start()
                log("[VRMarker] 视频流接收器已启动 @ 0.0.0.0:12346")
            except Exception:
                log_exc("[VRMarker] 视频流接收器启动失败（trigger 不受影响）:")

            print(f"[VR Marker Receiver] 服务器启动成功，监听 {self.host}:{self.port}")
            return True
        except OSError as e:
            log(f"[VRMarker] 端口 {self.port} 已被占用: {e}")
            self.is_running = False
            return False
        except Exception as e:
            log_exc(f"[VRMarker] 启动失败 (port={self.port}):")
            self.is_running = False
            return False

    def set_video_frame_callback(self, callback: Optional[Callable]) -> None:
        """设置视频帧回调"""
        self._video_frame_callback = callback
        # 转发到独立视频接收器（字段名是 on_frame）
        self.video_receiver.on_frame = callback

    def set_gaze_callback(self, callback: Optional[Callable[[float, float], None]]) -> None:
        """设置注视点回调（归一化坐标 0~1）"""
        self._gaze_callback = callback

    def set_gaze_rect_callback(
        self,
        callback: Optional[Callable[[float, float, float, float], None]]
    ) -> None:
        """设置注视框回调（归一化坐标 0~1）"""
        self._gaze_rect_callback = callback

    def set_gaze_target_id_callback(self, callback: Optional[Callable[[int], None]]) -> None:
        """设置注视目标 ID 回调"""
        self._gaze_target_id_callback = callback
    
    def stop(self) -> None:
        """停止 TCP 服务器"""
        self.is_running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                log_exc("[VRMarker] stop() 关闭 socket 异常:")
            self.socket = None

        if self.receiver_thread:
            self.receiver_thread.join(timeout=2.0)

        # 关闭独立视频接收器
        try:
            self.video_receiver.stop()
        except Exception:
            log_exc("[VRMarker] 关闭 video_receiver 异常:")
    
    def _receiver_loop(self) -> None:
        """接收循环（在后台线程中运行）"""
        client_socket = None
        client_address = None

        def _socket_alive() -> bool:
            """检查监听 socket 是否还活着（stop() 会把 self.socket 置 None）"""
            if not self.is_running or self.socket is None:
                return False
            try:
                # fileno 抛 OSError(EBADF) 即 socket 已关
                self.socket.fileno()
                return True
            except OSError:
                return False

        while self.is_running:
            try:
                # 等待客户端连接
                if client_socket is None:
                    self.is_connected = False
                    self.client_socket = None
                    if not _socket_alive():
                        # stop() 已经关掉监听 socket，安静退出
                        break
                    try:
                        client_socket, client_address = self.socket.accept()
                        client_socket.settimeout(1.0)
                        self.client_socket = client_socket
                        self._text_buffer = b""
                        # 视频流走独立的 12346 socket，不再复用 trigger socket
                        self.is_connected = True
                        log(f"[VRMarker] 客户端已连接: {client_address}")
                    except socket.timeout:
                        continue
                    except OSError as e:
                        # 10038 / EBADF 等：socket 在 accept 期间被 stop() 关掉，正常退出
                        if not self.is_running or not _socket_alive():
                            break
                        log(f"[VRMarker] accept() OSError: {e}")
                        break
                    except Exception:
                        log_exc(f"[VRMarker] accept() 异常 (port={self.port}):")
                        continue
                
                # 接收数据
                try:
                    data = client_socket.recv(4096)
                    if not data:
                        # 连接断开
                        log(f"[VRMarker] 客户端断开连接: {client_address}")
                        client_socket.close()
                        client_socket = None
                        self.client_socket = None
                        self.is_connected = False
                        continue

                    # 记录收到的数据（用于调试，前100字节）
                    if len(data) <= 100:
                        log(f"[VRMarker] 收到数据 ({len(data)}B): {data!r}")
                    else:
                        log(f"[VRMarker] 收到数据 ({len(data)}B): {data[:100]!r}...")

                    # 处理接收到的数据
                    self._process_received_data(data)

                except socket.timeout:
                    continue
                except Exception:
                    log_exc(f"[VRMarker] 接收数据时出错 (client={client_address}):")
                    try:
                        client_socket.close()
                    except Exception:
                        pass
                    client_socket = None
                    self.client_socket = None
                    self.is_connected = False

            except Exception:
                log_exc("[VRMarker] 接收循环整体异常 (将尝试继续):")
                if client_socket:
                    try:
                        client_socket.close()
                    except Exception:
                        log_exc("[VRMarker] 关闭 client_socket 异常(整体异常分支):")
                    client_socket = None
                    self.client_socket = None
                    self.is_connected = False

        # 清理
        if client_socket:
            try:
                client_socket.close()
            except Exception:
                log_exc("[VRMarker] 关闭 client_socket 异常(清理):")
        self.is_connected = False
        self.client_socket = None
    
    def _process_received_data(self, data: bytes) -> None:
        """处理接收到的数据（仅 trigger 文本，视频走独立 socket）"""
        if not data:
            return

        # 视频流走独立 12346 socket，这里不再处理视频字节
        # 按文本解析标记（忽略非UTF-8字节）
        try:
            lines = self._extract_messages(data)
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # 解析标记格式
                marker = self._parse_marker(line)
                if marker:
                    # 在 TCP 接收线程里设置一次时间戳（不要在调用方再次覆盖）
                    # 使用 wall-clock ms；与 EEG 采样的对齐误差由 ERP 分析器
                    # 通过 trigger 通道或 sample_counter 处理。
                    timestamp_ms = int(time.time() * 1000)
                    marker.timestamp_ms = timestamp_ms
                    marker.received_monotonic_ns = time.monotonic_ns()

                    # 添加到队列
                    self.marker_queue.put(marker)

                    # 统计
                    self.total_markers_received += 1
                    if marker.event_type.lower() == 'target':
                        self.target_count += 1
                    elif marker.event_type.lower() == 'standard':
                        self.standard_count += 1
                    elif marker.event_type.lower() == 'response':
                        self.response_count = getattr(self, 'response_count', 0) + 1

                    # 回调
                    if self.on_marker_received:
                        try:
                            self.on_marker_received(marker)
                        except Exception:
                            log_exc(f"[VRMarker] on_marker_received 回调异常 (marker={marker!r}):")
        except Exception:
            log_exc(f"[VRMarker] 标记解析总异常 (data={data!r}):")

    def _extract_messages(self, data):
        """TCP 无消息边界：X/G 标签固定两字节，不能依赖一次 recv 恰好一条。

        PICO.cs 的 X/G 消息本身没有换行，故完整标签立即交付，不等刺激结束后的换行。
        其它眼动/状态文本按行处理，残片保留到下一次接收。
        """
        self._text_buffer += data
        lines = []
        while self._text_buffer:
            self._text_buffer = self._text_buffer.lstrip(b" \r\n\t")
            buf = self._text_buffer
            if not buf:
                break
            if buf[:1] in (b"X", b"G"):
                if len(buf) < 2:
                    break
                if buf[1:2] in b"12345678":
                    lines.append(buf[:2].decode("ascii"))
                    self._text_buffer = buf[2:]
                    continue
            if buf[:1] == b"Y" and (len(buf) == 1 or buf[1:2] in (b"\n", b"\r", b"\t", b"X", b"G", b"Y")):
                lines.append("Y")
                self._text_buffer = buf[1:]
                continue
            if b"\n" not in buf:
                break
            line, self._text_buffer = buf.split(b"\n", 1)
            lines.append(line.decode("utf-8", errors="ignore"))
        if len(self._text_buffer) > 1024 * 1024:
            log("[VRMarker] 超长未结束消息已丢弃")
            self._text_buffer = b""
        return lines
    
    def _parse_marker(self, line: str) -> Optional[EventMarker]:
        """
        解析标记字符串

        格式（与 vrcode/PICO.cs 247-252 + connect/V2.m 102-127 一致）：
        - "X1" - "X8": Target 刺激（blockCue 命中的物体）
        - "G1" - "G8": Standard 刺激（其余物体）
        - "Y": 被试按键反应
        - "BLOCK_X_EARLY_EXIT_AT_Ys": Block 提前退出标记（忽略）
        - 其他: 眼动数据或按键数据（忽略）
        """
        # 标记格式约定：
        #   X1-X8  = 目标刺激 (target)    -> 串口打标 11, 22, 33, ..., 88
        #   G1-G8  = 标准刺激 (standard)  -> 串口打标 1, 2, 3, ..., 8
        #   Y      = 被试按键反应 (response) -> 串口打标 89
        #   000.../888...  = 眼动 / 按键数据 (忽略)
        line = line.strip()

        # 目标刺激: X1-X8
        if line.startswith('X') and len(line) == 2:
            try:
                channel = int(line[1])
                if 1 <= channel <= 8:
                    return EventMarker(timestamp_ms=0, event_type='target', channel=channel)
            except ValueError:
                pass

        # 标准刺激: G1-G8
        if line.startswith('G') and len(line) == 2:
            try:
                channel = int(line[1])
                if 1 <= channel <= 8:
                    return EventMarker(timestamp_ms=0, event_type='standard', channel=channel)
            except ValueError:
                pass
        
        # Block 提前退出标记（忽略）
        if 'BLOCK' in line and 'EARLY_EXIT' in line:
            return None

        # Y 响应标记（被试按键反应）—— 与 V2.m 一致，写串口 89
        if line == 'Y' or line.startswith('Y\t') or line.startswith('Y\n'):
            return EventMarker(timestamp_ms=0, event_type='response', channel=0)
        
        # 眼动注视点（归一化坐标）：GAZE:x,y
        if line.startswith("GAZE:"):
            payload = line[5:]
            parts = payload.split(",")
            if len(parts) == 2:
                try:
                    x = float(parts[0])
                    y = float(parts[1])
                    if self._gaze_callback:
                        self._gaze_callback(x, y)
                except ValueError:
                    pass
            return None

        # 眼动注视框（归一化坐标）：GAZE_RECT:x,y,w,h
        if line.startswith("GAZE_RECT:"):
            payload = line[10:]
            parts = payload.split(",")
            if len(parts) == 4:
                try:
                    x = float(parts[0])
                    y = float(parts[1])
                    w = float(parts[2])
                    h = float(parts[3])
                    if self._gaze_rect_callback:
                        self._gaze_rect_callback(x, y, w, h)
                except ValueError:
                    pass
            return None

        # 注视目标 ID：GAZE_ID:n
        if line.startswith("GAZE_ID:"):
            payload = line[8:]
            try:
                target_id = int(payload)
                if self._gaze_target_id_callback:
                    self._gaze_target_id_callback(target_id)
            except ValueError:
                pass
            return None

        # 其他数据（眼动、按键等，忽略）
        return None
    
    def get_markers(self) -> list:
        """获取队列中的所有标记（非阻塞）"""
        markers = []
        while not self.marker_queue.empty():
            try:
                markers.append(self.marker_queue.get_nowait())
            except Exception:
                log_exc("[VRMarker] marker_queue.get_nowait() 异常:")
                break
        return markers
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            'total_markers': self.total_markers_received,
            'target_count': self.target_count,
            'standard_count': self.standard_count,
            'is_running': self.is_running,
            'queue_size': self.marker_queue.qsize()
        }


