"""
VR 视频显示组件（第一步：显示视频帧）
在 PyQt5 中显示来自 Unity VR 的视频画面

功能：
- 接收视频帧（numpy 数组）
- 转换为 QImage 并在 QLabel 中显示
- 实时更新画面
"""

import numpy as np
from PyQt5 import QtCore, QtWidgets, QtGui
from typing import Optional


class VRVideoWidget(QtWidgets.QWidget):
    """
    VR 视频显示组件
    
    使用 QLabel 显示视频帧，自动适应窗口大小
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setMinimumSize(640, 480)
        
        # 布局
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 视频显示标签
        self.video_label = QtWidgets.QLabel(self)
        self.video_label.setAlignment(QtCore.Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: #2D3748; color: #C8D5E8; font-size: 13px;")
        self.video_label.setText("等待视频流...")
        layout.addWidget(self.video_label)
        
        # 当前视频帧（numpy 数组）
        self.current_frame: Optional[np.ndarray] = None
        
        # 注视点/注视框（归一化坐标 0~1）
        self._gaze_point: Optional[tuple] = None  # (x, y)
        self._gaze_rect: Optional[tuple] = None  # (x, y, w, h)
        self._gaze_target_id: Optional[int] = None
        self._gaze_smoothing_alpha = 0.2  # EMA 平滑系数

        # 更新定时器（用于定期刷新显示）
        self.update_timer = QtCore.QTimer(self)
        self.update_timer.timeout.connect(self._update_display)
        self.update_timer.start(33)  # 约 30 FPS
        
        # 统计信息
        self.frame_count = 0
    
    def update_frame(self, frame: np.ndarray):
        """
        更新视频帧
        
        参数:
            frame: (H, W, 3) RGB 格式的 numpy 数组
        """
        if frame is None:
            return
        
        self.current_frame = frame.copy()
        self.frame_count += 1

    def update_gaze_point(self, x: float, y: float) -> None:
        """更新注视点（归一化坐标 0~1）"""
        new_point = (x, 1.0 - y)
        if self._gaze_point is None:
            self._gaze_point = new_point
            return

        gx, gy = self._gaze_point
        nx, ny = new_point
        alpha = self._gaze_smoothing_alpha
        self._gaze_point = (gx + alpha * (nx - gx), gy + alpha * (ny - gy))

    def update_gaze_target_id(self, target_id: Optional[int]) -> None:
        """更新注视目标 ID"""
        self._gaze_target_id = target_id

    def update_gaze_rect(self, x: float, y: float, w: float, h: float) -> None:
        """更新注视框（归一化坐标 0~1）"""
        self._gaze_rect = (x, y, w, h)
    
    def _update_display(self):
        """更新显示（由定时器触发）"""
        if self.current_frame is None:
            return
        
        try:
            # 转换为 QImage
            height, width, channel = self.current_frame.shape
            
            # numpy 数组是 RGB 格式，QImage 需要 RGB888
            bytes_per_line = channel * width
            q_image = QtGui.QImage(
                self.current_frame.data,
                width,
                height,
                bytes_per_line,
                QtGui.QImage.Format_RGB888
            )
            
            # 缩放适应标签大小（保持宽高比）
            label_size = self.video_label.size()
            scaled_image = q_image.scaled(
                label_size,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation
            )
            
            # 在缩放后的图像上叠加注视点/注视框
            painter = QtGui.QPainter(scaled_image)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

            if self._gaze_point is not None:
                gx, gy = self._gaze_point
                gx = min(max(gx, 0.0), 1.0)
                gy = min(max(gy, 0.0), 1.0)
                px = int(gx * scaled_image.width())
                py = int(gy * scaled_image.height())
                radius = max(6, int(min(scaled_image.width(), scaled_image.height()) * 0.01))
                pen = QtGui.QPen(QtGui.QColor(255, 0, 0), 2)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QPoint(px, py), radius, radius)

            if self._gaze_rect is not None:
                rx, ry, rw, rh = self._gaze_rect
                rx = min(max(rx, 0.0), 1.0)
                ry = min(max(ry, 0.0), 1.0)
                rw = min(max(rw, 0.0), 1.0)
                rh = min(max(rh, 0.0), 1.0)
                rect_x = int(rx * scaled_image.width())
                rect_y = int(ry * scaled_image.height())
                rect_w = int(rw * scaled_image.width())
                rect_h = int(rh * scaled_image.height())
                pen = QtGui.QPen(QtGui.QColor(0, 255, 0), 2)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawRect(rect_x, rect_y, rect_w, rect_h)

            # 叠加目标ID文本
            if self._gaze_target_id is not None:
                target_map = {
                    1: "柜子",
                    2: "蝴蝶",
                    3: "画板",
                    4: "灯",
                    5: "梯子",
                    6: "壁画",
                    7: "玩偶熊",
                    8: "球",
                }
                if self._gaze_target_id > 0:
                    target_name = target_map.get(self._gaze_target_id, f"物体{self._gaze_target_id}")
                    text = f"目标: {self._gaze_target_id} ({target_name})"
                else:
                    text = "目标: -"
                painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 0), 2))
                painter.setFont(QtGui.QFont("Arial", 14, QtGui.QFont.Bold))
                painter.drawText(10, 24, text)

            painter.end()
            
            # 设置到标签
            self.video_label.setPixmap(QtGui.QPixmap.fromImage(scaled_image))
            
        except Exception:
            pass
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            'frame_count': self.frame_count,
            'has_frame': self.current_frame is not None,
            'frame_shape': self.current_frame.shape if self.current_frame is not None else None
        }
