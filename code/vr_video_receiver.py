"""
VR 视频接收器（独立 TCP，端口 12346）
对应 PICO.cs 视频流协议：
  [4B 魔数 'V','R','V','F'] [4B seq][4B len] [len B JPEG]
特点：
  - 独立 TCP 连接（不和 trigger 12345 串）
  - 自带丢帧：PC 解码太慢时跳过中间帧，永远显示最新
  - 30 fps 目标，640x360 JPEG
  - OpenCV 显示窗口：左上是视频，右上是 trigger 状态
"""

import io
import socket
import struct
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

try:
    from PIL import Image
except ImportError:
    raise ImportError("需要安装 pillow: pip install pillow")

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False
    print("[VRVideo] 未安装 opencv，将不弹窗显示（仍会解码）")


MAGIC = b'VRVF'
HEADER_SIZE = 12  # 4 magic + 4 seq + 4 len


class VRVideoReceiver:
    """VR 视频流接收器（端口 12346，独立 TCP）"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 12346,
        on_frame: Optional[Callable[[np.ndarray, int], None]] = None,
    ):
        self.host = host
        self.port = port
        self.on_frame = on_frame

        self.is_running = False
        self._server_sock: Optional[socket.socket] = None
        self._client_sock: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._recv_thread: Optional[threading.Thread] = None

        # 统计
        self.total_frames = 0
        self.dropped_frames = 0
        self.last_seq = -1
        self.fps_counter = 0
        self.fps_timer = time.time()
        self.current_fps = 0.0

        # 最新帧（GUI 直接读）
        self.latest_frame: Optional[np.ndarray] = None
        self.latest_seq: int = -1
        self._lock = threading.Lock()

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        if self.is_running:
            return False
        self.is_running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        return True

    def stop(self):
        self.is_running = False
        for s in (self._client_sock, self._server_sock):
            try:
                if s: s.close()
            except Exception:
                pass
        self._client_sock = None
        self._server_sock = None

    # ---------- 网络 ----------
    def _accept_loop(self):
        """接受 VR 端连接"""
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self.host, self.port))
            self._server_sock.listen(1)
            print(f"[VRVideo] 监听 {self.host}:{self.port} ...")
        except Exception as e:
            print(f"[VRVideo] 绑定失败: {e}")
            self.is_running = False
            return

        while self.is_running:
            try:
                self._server_sock.settimeout(1.0)
                client, addr = self._server_sock.accept()
                print(f"[VRVideo] VR 端已连接: {addr}")
                # 关闭旧连接
                if self._client_sock:
                    try: self._client_sock.close()
                    except Exception: pass
                self._client_sock = client
                # 启接收线程
                if self._recv_thread and self._recv_thread.is_alive():
                    # 让旧的循环自然退出
                    pass
                self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
                self._recv_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.is_running:
                    print(f"[VRVideo] accept 异常: {e}")
                break

    def _recv_loop(self):
        sock = self._client_sock
        if not sock:
            return
        buf = b''
        sock.settimeout(0.5)
        while self.is_running and self._client_sock is sock:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    print("[VRVideo] VR 端断开，等待重连...")
                    break
                buf += chunk
            except socket.timeout:
                continue
            except Exception as e:
                if self.is_running:
                    print(f"[VRVideo] recv 异常: {e}")
                break

            # 解析：只要能解析出完整帧就解析
            while len(buf) >= HEADER_SIZE:
                # 检查 magic
                if buf[:4] != MAGIC:
                    # 找下一个 magic
                    idx = buf.find(MAGIC, 1)
                    if idx == -1:
                        # magic 都不全，保留尾巴 3 字节
                        buf = buf[-3:]
                        break
                    buf = buf[idx:]
                    continue
                # 读 seq + len
                # 注意：PICO.cs 用小端写入（C# 显式 byte-by-byte），所以这里也用小端解析
                seq, length = struct.unpack('<II', buf[4:12])
                # 防御：长度异常就跳一字节找下一个 magic
                if length <= 0 or length > 5_000_000:
                    print(f"[VRVideo] 异常帧长度 {length}，跳过")
                    buf = buf[4:]
                    continue
                need = HEADER_SIZE + length
                if len(buf) < need:
                    break  # 等更多
                jpg = buf[HEADER_SIZE:need]
                buf = buf[need:]

                # 解码
                frame = self._decode(jpg)
                if frame is None:
                    self.dropped_frames += 1
                    continue

                # 丢帧统计
                if self.last_seq >= 0 and seq > self.last_seq + 1:
                    self.dropped_frames += (seq - self.last_seq - 1)
                self.last_seq = seq
                self.total_frames += 1
                self.fps_counter += 1
                now = time.time()
                if now - self.fps_timer >= 1.0:
                    self.current_fps = self.fps_counter / (now - self.fps_timer)
                    self.fps_counter = 0
                    self.fps_timer = now

                with self._lock:
                    self.latest_frame = frame
                    self.latest_seq = seq

                if self.on_frame:
                    try:
                        self.on_frame(frame, seq)
                    except Exception:
                        pass

    # ---------- 解码 ----------
    def _decode(self, jpg: bytes) -> Optional[np.ndarray]:
        try:
            img = Image.open(io.BytesIO(jpg)).convert('RGB')
            return np.array(img)
        except Exception:
            return None

    # ---------- GUI 辅助 ----------
    def get_latest(self):
        with self._lock:
            return self.latest_frame, self.latest_seq

    def get_stats(self) -> dict:
        return {
            'total_frames': self.total_frames,
            'dropped_frames': self.dropped_frames,
            'last_seq': self.last_seq,
            'fps': round(self.current_fps, 1),
        }


# ----------------- 独立运行 -----------------
def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--port', type=int, default=12346)
    p.add_argument('--no-show', action='store_true', help='不弹窗（仅后台接收）')
    args = p.parse_args()

    recv = VRVideoReceiver(host=args.host, port=args.port)
    if not recv.start():
        print("[VRVideo] 启动失败")
        return
    print("[VRVideo] 等待 VR 连接... (Ctrl+C 退出)")

    if args.no_show or not HAVE_CV2:
        try:
            while True:
                time.sleep(1.0)
                s = recv.get_stats()
                print(f"[VRVideo] fps={s['fps']} total={s['total_frames']} drop={s['dropped_frames']} last_seq={s['last_seq']}")
        except KeyboardInterrupt:
            pass
        finally:
            recv.stop()
        return

    # 弹窗模式
    win = "VR Live (12346)  -  Q quit,  S screenshot"
    last_log = time.time()
    try:
        while True:
            frame, seq = recv.get_latest()
            if frame is None:
                # 没有帧时显示占位
                placeholder = np.zeros((360, 640, 3), dtype=np.uint8)
                cv2.putText(placeholder, "Waiting for VR...", (180, 180),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                show = placeholder
            else:
                show = frame

            # 叠加 FPS 信息
            s = recv.get_stats()
            cv2.putText(show, f"fps={s['fps']} seq={seq} drop={s['dropped_frames']}",
                        (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.imshow(win, cv2.cvtColor(show, cv2.COLOR_RGB2BGR))

            key = cv2.waitKey(30) & 0xFF
            if key == ord('q') or key == 27:
                break
            if key == ord('s'):
                fn = f"vr_screenshot_{int(time.time())}.jpg"
                cv2.imwrite(fn, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if frame is not None else show)
                print(f"[VRVideo] 已保存 {fn}")

            now = time.time()
            if now - last_log >= 2.0:
                last_log = now
                print(f"[VRVideo] fps={s['fps']} total={s['total_frames']} drop={s['dropped_frames']} last_seq={s['last_seq']}")
    except KeyboardInterrupt:
        pass
    finally:
        recv.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
