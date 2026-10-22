"""
EEGPixelWidget — 像素级 EEG 波形绘制组件。

对标 C++ DrawMap() 算法（Collect_Data/Thread.cpp lines 1038-1230）：
  - 纯 QWidget + QImage/QPainter，不依赖 PyQtGraph
  - 双缓冲（double-buffering）避免闪烁
  - 数据单位：µV（Volt × 1e6）
  - 缩放公式：y_scal = 0.5 / nPlus  mV/pixel（nPlus 初始 16，范围 1-32768）
  - 每帧绘制一个像素列，X 从左→右，到达右边界后折回左边（循环覆盖）
  - 每秒画一条竖直灰色时间标记线
"""

from typing import List, Optional
import math

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets


# ============================================================================
# 标准 32 导联
# ============================================================================
STANDARD_32_CHANNELS: List[str] = [
    "Fp1", "Fpz", "Fp2",
    "F7", "F3", "Fz", "F4", "F8",
    "FC5", "FC1", "FC2", "FC6",
    "T7", "C3", "Cz", "C4", "T8",
    "M1", "CP5", "CP1", "CP2", "CP6", "M2",
    "P7", "P3", "Pz", "P4", "P8",
    "POz", "O1", "Oz", "O2",
]


# ============================================================================
# 主类
# ============================================================================
class EEGPixelWidget(QtWidgets.QWidget):
    """
    像素级 EEG 波形显示器。

    继承自 QWidget，使用双 QImage 缓冲 + QPainter 逐像素/逐线段绘制，
    模拟 C++ DrawMap() 的滚动绘图逻辑。
    """

    # 绘图常量（对标 C++ Y_UNIT_mV）
    Y_UNIT_MV: float = 0.5          # mV 刻度（Y 轴全高代表的电压幅值）
    N_PLUS_MIN: float = 1.0
    N_PLUS_MAX: float = 32768.0
    N_PLUS_DEFAULT: float = 16.0

    # 颜色配置
    COLOR_BG: QtGui.QColor = QtGui.QColor(255, 255, 255)   # 白色背景
    COLOR_WAVEFORM: QtGui.QColor = QtGui.QColor(0, 0, 180)  # 深蓝波形
    COLOR_GRID: QtGui.QColor = QtGui.QColor(220, 220, 220)  # 通道分隔线
    COLOR_TIMEMARK: QtGui.QColor = QtGui.QColor(180, 180, 180)  # 时间标记线
    COLOR_TEXT: QtGui.QColor = QtGui.QColor(80, 80, 80)     # 时间文字

    # 模拟数据默认采样率
    DEFAULT_SAMPLE_RATE: int = 1000

    def __init__(
        self,
        channel_names: Optional[List[str]] = None,
        sample_rate: int = 1000,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)

        # 通道配置
        self.channel_names: List[str] = (
            list(channel_names) if channel_names else list(STANDARD_32_CHANNELS)
        )
        self.channel_count: int = len(self.channel_names)
        self.sample_rate: int = sample_rate

        # 缩放因子（对标 C++ gm_nPlus）
        self._n_plus: float = self.N_PLUS_DEFAULT
        # y_scal = 0.5 / nPlus  mV/pixel
        self._y_scal: float = self.Y_UNIT_MV / self._n_plus

        # 绘制状态
        self._x_pos: int = 0                        # 当前 X 位置（0 到 width-1）
        self._sample_counter: int = 0                # 已绘制样本计数
        self._time_counter: int = 0                 # 已绘制秒数（用于时间标记）

        # 上一帧每个通道的 Y 像素坐标（用于画线段连续性）
        self._prev_y: np.ndarray = np.zeros(self.channel_count, dtype=float)

        # 双缓冲
        self._buffer1: Optional[QtGui.QImage] = None
        self._buffer2: Optional[QtGui.QImage] = None
        # _back: 正在绘制的缓冲（清空旧数据）
        # _display: 已完成绘制、正在显示的缓冲
        self._back: Optional[QtGui.QImage] = None
        self._display: Optional[QtGui.QImage] = None

        # 左侧通道标签区宽度
        self._label_width: int = 80

        # 模拟数据（调试用）
        self._mock_enabled: bool = False
        self._mock_phase: float = 0.0

        # 使能抗锯齿（可关闭提升性能）
        self._antialiasing: bool = False

        # 内部计时（用于模拟数据）
        self._mock_t0_ms: int = 0
        self._mock_last_ms: int = 0
        self._start_timestamp: Optional[int] = None

        # 定时器回调
        self._timer.timeout.connect(self._on_timer)
        self._timer.start(50)   # 约 20 fps

        # 初始化缓冲（必须放在最后，等 widget 有尺寸后再分配）
        self._realloc_buffers()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def update_data(self, vals: np.ndarray) -> None:
        """
        接收新数据并绘制。

        Args:
            vals: shape (n_samples, n_channels) 的 numpy 数组，单位 µV。
        """
        if vals.size == 0:
            return

        # 确保 vals 是二维数组
        if vals.ndim == 1:
            vals = vals.reshape(-1, 1)

        n_samples, n_channels = vals.shape

        # 如果通道数变化，重新初始化
        if n_channels != self.channel_count:
            self.channel_count = n_channels
            self._prev_y = np.zeros(self.channel_count, dtype=float)
            self._realloc_buffers()

        # 逐样本绘制
        for i in range(n_samples):
            self._draw_column(self._x_pos, vals[i])
            self._x_pos += 1

            # 到达右边界时折回
            w = self.width() - self._label_width
            if self._x_pos >= w:
                self._x_pos = 0

        self._sample_counter += n_samples

        # 交换缓冲（双缓冲：在显示缓冲上 blit 当前缓冲）
        self._swap_buffers()

        # 触发重绘（显示新缓冲）
        self.update()

    def reset(self) -> None:
        """清空缓冲，重置状态。"""
        self._x_pos = 0
        self._sample_counter = 0
        self._time_counter = 0
        self._prev_y = np.zeros(self.channel_count, dtype=float)
        self._realloc_buffers()
        self.update()

    def set_n_plus(self, value: float) -> None:
        """设置缩放因子（对标 C++ gm_nPlus）。"""
        self._n_plus = float(np.clip(value, self.N_PLUS_MIN, self.N_PLUS_MAX))
        self._y_scal = self.Y_UNIT_MV / self._n_plus

    def get_n_plus(self) -> float:
        return self._n_plus

    def set_waveform_color(self, color: QtGui.QColor) -> None:
        self.COLOR_WAVEFORM = QtGui.QColor(color)
        self.update()

    def set_background_color(self, color: QtGui.QColor) -> None:
        self.COLOR_BG = QtGui.QColor(color)
        self._realloc_buffers()
        self.update()

    def set_antialiasing(self, enabled: bool) -> None:
        self._antialiasing = bool(enabled)

    def enable_mock_data(self, enabled: bool = True) -> None:
        """开启/关闭模拟数据模式。"""
        self._mock_enabled = bool(enabled)
        self._mock_phase = 0.0
        self._mock_t0_ms = 0
        self._mock_last_ms = 0
        self._start_timestamp = None
        print(f"[EEGPixelWidget] 模拟数据 {'开启' if enabled else '关闭'}")

    def update_sample_rate(self, sample_rate: int) -> None:
        """更新采样率。"""
        self.sample_rate = max(1, int(sample_rate))

    def update_channel_count(self, channel_count: int) -> None:
        """动态更新通道数。"""
        self.channel_count = channel_count
        self.channel_names = [f"Ch{i+1}" for i in range(channel_count)]
        self._prev_y = np.zeros(self.channel_count, dtype=float)
        self._realloc_buffers()
        self.update()

    def update_channels(self, channel_names: List[str]) -> None:
        """动态更新通道名列表。"""
        self.channel_names = list(channel_names)
        self.channel_count = len(channel_names)
        self._prev_y = np.zeros(self.channel_count, dtype=float)
        self._realloc_buffers()
        self.update()

    # ------------------------------------------------------------------
    # 内部绘制
    # ------------------------------------------------------------------

    def _realloc_buffers(self) -> None:
        """在 widget 尺寸已知时分配双缓冲。"""
        w = max(1, self.width())
        h = max(1, self.height())

        # 像素缓冲：RGB32（每像素 4 字节）
        # 使用 QImage.Format_RGB32，背景色填充
        self._buffer1 = QtGui.QImage(
            w, h,
            QtGui.QImage.Format_RGB32
        )
        self._buffer2 = QtGui.QImage(
            w, h,
            QtGui.QImage.Format_RGB32
        )

        # 填充背景色
        self._fill_buffer_bg(self._buffer1)
        self._fill_buffer_bg(self._buffer2)

        # 初始化绘图缓冲
        self._back = self._buffer1
        self._display = self._buffer2

    def _fill_buffer_bg(self, buf: Optional[QtGui.QImage]) -> None:
        """用背景色填充缓冲。"""
        if buf is None:
            return
        buf.fill(self.COLOR_BG)

    def _swap_buffers(self) -> None:
        """交换当前缓冲与显示缓冲。"""
        self._back, self._display = self._display, self._back

    def _draw_column(self, x: int, sample_data: np.ndarray) -> None:
        """
        在指定 X 位置绘制一列像素（对标 C++ DrawMap 单列逻辑）。

        步骤：
        1. 擦除 x 位置的旧列（填背景色）
        2. 如果需要时间标记线，绘制灰色竖线
        3. 对每个通道：
           - 计算 Y 基线：y_base = (j + 0.5) * channel_height
           - 计算 Y 偏移：y_offset = sample_data[j] * 1e-3 / y_scal
                              (µV → mV → 像素)
           - y_pixel = y_base + y_offset
           - 从 (x-1, prev_y) 到 (x, y_pixel) 画线段
           - 保存 prev_y[j] = y_pixel
        4. 更新 _sample_counter 和 _time_counter

        Args:
            x: 绘制列的 X 坐标（绝对值，0 到 width-1）
            sample_data: shape (n_channels,) 单位 µV
        """
        if self._back is None:
            return

        w = self._back.width()
        h = self._back.height()

        # 超出范围忽略
        if x < 0 or x >= w:
            return

        # 通道高度
        channel_h = h / max(1, self.channel_count)

        # 确定是否需要时间标记（每秒一次）
        need_timemark = False
        new_time_counter = self._sample_counter // max(1, self.sample_rate)
        if new_time_counter != self._time_counter:
            need_timemark = True
            self._time_counter = new_time_counter

        # ------- 擦除 x 列 -------
        self._clear_column(self._back, x, channel_h)

        # ------- 时间标记线 -------
        if need_timemark and x > 0:
            self._draw_timemark_line(self._back, x)

        # ------- 绘制每个通道 -------
        # 将 sample_data 展平为一维
        data = np.asarray(sample_data, dtype=float).ravel()
        if data.shape[0] < self.channel_count:
            data = np.pad(data, (0, self.channel_count - data.shape[0]))

        # 逐通道绘制
        for j in range(self.channel_count):
            # Y 基线 = 通道中心（通道 j 的像素范围: [j*ch_h, (j+1)*ch_h]，中心 = (j+0.5)*ch_h）
            y_base = (j + 0.5) * channel_h

            # Y 偏移（µV → mV → 像素）
            # C++: y_pixel = y_base + value_µV * 1e-3 / y_scal
            # 其中 y_scal = 0.5 / nPlus (mV/pixel)
            value_uv = float(data[j])
            y_offset = value_uv * 1e-3 / self._y_scal

            # 限幅：防止异常大值跑出通道范围
            half_ch = channel_h * 0.5
            y_offset = float(np.clip(y_offset, -half_ch, half_ch))

            y_pixel = y_base + y_offset

            # 确保在可见范围内
            y_pixel = float(np.clip(y_pixel, 0, h - 1))

            prev_y_j = float(self._prev_y[j])

            # 画线段（前一个 x 到当前 x）
            if x > 0:
                x_prev = x - 1
                # 确保前一个 Y 也在可见范围内
                y_prev_clip = float(np.clip(prev_y_j, 0, h - 1))
                self._draw_line(
                    self._back,
                    x_prev, int(y_prev_clip),
                    x, int(y_pixel),
                    self.COLOR_WAVEFORM
                )

            # 保存当前 Y 作为下一次的前一个 Y
            self._prev_y[j] = y_pixel

        # 更新样本计数
        self._sample_counter += 1

    def _clear_column(
        self,
        buf: Optional[QtGui.QImage],
        x: int,
        channel_h: float,
    ) -> None:
        """用背景色擦除一列。"""
        if buf is None:
            return
        h = buf.height()
        col_left = max(0, x)
        col_right = min(x + 1, buf.width())
        if col_left >= col_right:
            return
        # 用 QPainter.fillRect 比逐像素快 100 倍
        # 但 fillRect 填的是整个列，需要精确宽度 1 像素
        # 用 setPixel 逐行填充（最可靠）
        rgb = self.COLOR_BG.rgb()
        for y in range(h):
            buf.setPixel(col_left, y, rgb)

    def _draw_timemark_line(
        self,
        buf: Optional[QtGui.QImage],
        x: int,
    ) -> None:
        """在 x 位置画一条灰色竖直时间标记线（从顶部到底部）。"""
        if buf is None:
            return
        h = buf.height()
        col_left = max(0, x)
        col_right = min(x + 1, buf.width())
        if col_left >= col_right:
            return
        rgb = self.COLOR_TIMEMARK.rgb()
        for y in range(h):
            buf.setPixel(col_left, y, rgb)

    def _draw_line(
        self,
        buf: Optional[QtGui.QImage],
        x1: int, y1: int,
        x2: int, y2: int,
        color: QtGui.QColor,
    ) -> None:
        """在 QImage 上画一条线段（Bresenham 算法，逐像素写入）。"""
        if buf is None:
            return

        rgb = color.rgb()

        # Bresenham 直线算法（支持任意斜率）
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 <= x2 else -1
        sy = 1 if y1 <= y2 else -1
        err = dx - dy

        cx, cy = x1, y1
        w = buf.width()
        h = buf.height()

        while True:
            if 0 <= cx < w and 0 <= cy < h:
                buf.setPixel(cx, cy, rgb)

            if cx == x2 and cy == y2:
                break

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                cx += sx
            if e2 < dx:
                err += dx
                cy += sy

    def _on_timer(self) -> None:
        """定时器回调：生成模拟数据（当开启模拟模式且无真实数据时）。"""
        if not self._mock_enabled:
            return

        # 生成模拟 EEG 数据
        now_ms = int(QtCore.QTime.currentTime().msecsSinceStartOfDay())
        if self._mock_t0_ms == 0:
            self._mock_t0_ms = now_ms
        if self._mock_last_ms == 0:
            self._mock_last_ms = self._mock_t0_ms

        # 每帧 50ms 数据（50 samples @ 500Hz）
        dt_ms = 1   # 每 sample 间隔 1ms
        n = 50
        last_ms = self._mock_last_ms
        self._mock_last_ms = last_ms + n

        t_sec = (np.arange(n) + (last_ms - self._mock_t0_ms)) / 1000.0

        # 10Hz alpha 节律
        alpha = np.sin(2 * np.pi * 10.0 * t_sec) * 50.0   # 50 µV
        # 2Hz delta 慢波
        delta = np.sin(2 * np.pi * 2.0 * t_sec + self._mock_phase) * 30.0  # 30 µV

        noise = np.random.normal(0, 5.0, (n, self.channel_count))  # 5 µV 噪声

        # 枕叶通道（后部）alpha 较强
        spatial_alpha = np.zeros(self.channel_count)
        for i, name in enumerate(self.channel_names):
            name_u = name.upper()
            if name_u in {"O1", "O2", "OZ", "POZ", "PZ", "P3", "P4", "P7", "P8"}:
                spatial_alpha[i] = 1.0
            elif name_u in {"M1", "M2", "A1", "A2", "VREF", "REF", "GND"}:
                spatial_alpha[i] = 0.0
            else:
                spatial_alpha[i] = 0.3

        vals = noise + alpha[:, None] * spatial_alpha[None, :] + delta[:, None] * 0.5

        self._mock_phase += 0.1
        self.update_data(vals)

    # ------------------------------------------------------------------
    # Qt 事件
    # ------------------------------------------------------------------

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        """将显示缓冲 blit 到屏幕。"""
        if self._display is None:
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, self._antialiasing)

        # 绘制通道分隔线
        self._draw_channel_grid(painter)

        # Blit 显示缓冲
        img = self._display
        # 源区域（绘图区，不含左侧标签）
        src_rect = QtCore.QRect(
            self._label_width, 0,
            img.width() - self._label_width,
            img.height()
        )
        # 目标区域（对齐到标签右侧）
        dst_rect = QtCore.QRect(
            self._label_width, 0,
            self.width() - self._label_width,
            self.height()
        )
        painter.drawImage(dst_rect, img, src_rect)

        # 绘制通道标签
        self._draw_channel_labels(painter)

        # 绘制时间文字（底部）
        self._draw_time_text(painter)

        # 绘制缩放信息
        self._draw_scale_info(painter)

        # 绘制当前位置指示线（右侧边缘）
        self._draw_position_indicator(painter)

        painter.end()

    def _draw_channel_grid(self, painter: QtGui.QPainter) -> None:
        """绘制通道分隔水平线。"""
        h = self.height()
        w = self.width()
        channel_h = h / max(1, self.channel_count)

        painter.setPen(QtGui.QPen(self.COLOR_GRID, 1))
        for j in range(1, self.channel_count):
            y = int(j * channel_h)
            painter.drawLine(0, y, w, y)

        # 左侧标签区背景（轻微区分）
        painter.fillRect(
            0, 0, self._label_width, h,
            QtGui.QColor(245, 247, 250)
        )

    def _draw_channel_labels(self, painter: QtGui.QPainter) -> None:
        """绘制左侧通道名标签。"""
        h = self.height()
        w = self.width()
        channel_h = h / max(1, self.channel_count)

        font = QtGui.QFont("Consolas", max(7, int(channel_h * 0.4)))
        font.setStyleStrategy(QtGui.QFont.NoAntialias)
        painter.setFont(font)

        pen = QtGui.QPen(self.COLOR_TEXT)
        painter.setPen(pen)

        for j, name in enumerate(self.channel_names):
            y_center = (j + 0.5) * channel_h
            # 背景条
            painter.fillRect(
                0, int(y_center - channel_h * 0.5),
                self._label_width, int(channel_h),
                QtGui.QColor(245, 247, 250)
            )
            # 文字（居中对齐）
            painter.drawText(
                4,
                int(y_center - channel_h * 0.5),
                self._label_width - 8,
                int(channel_h),
                QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                name
            )

    def _draw_time_text(self, painter: QtGui.QPainter) -> None:
        """在底部绘制当前时间（MM:SS）。"""
        if self.sample_rate <= 0:
            return

        # 时间 = 样本数 / 采样率
        total_sec = self._sample_counter / self.sample_rate
        minutes = int(total_sec // 60)
        seconds = int(total_sec % 60)
        time_str = f"{minutes:02d}:{seconds:02d}"

        # 背景框
        painter.fillRect(
            self.width() - 70,
            self.height() - 22,
            70, 22,
            QtGui.QColor(230, 230, 230)
        )

        font = QtGui.QFont("Consolas", 10)
        painter.setFont(font)
        painter.setPen(QtGui.QPen(self.COLOR_TEXT))
        painter.drawText(
            self.width() - 68,
            self.height() - 20,
            68, 18,
            QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
            time_str
        )

    def _draw_scale_info(self, painter: QtGui.QPainter) -> None:
        """在左上角绘制缩放信息。"""
        n_plus = self._n_plus
        y_scal_mv = self._y_scal

        # 计算当前缩放下 ±100µV 对应的像素数
        sample_uv = 100.0
        y_offset_px = sample_uv * 1e-3 / y_scal_mv
        scale_str = f"±{sample_uv:.0f}µV / {y_offset_px:.1f}px  nPlus={n_plus:.0f}"

        font = QtGui.QFont("Consolas", 9)
        painter.setFont(font)
        painter.setPen(QtGui.QPen(self.COLOR_TEXT))
        painter.drawText(
            self._label_width + 4, 4,
            300, 16,
            QtCore.Qt.AlignLeft,
            scale_str
        )

    def _draw_position_indicator(self, painter: QtGui.QPainter) -> None:
        """在当前 X 位置画一条细指示线。"""
        if self._current_buffer is None:
            return
        # 指示线：当前位置 X（来自 _x_pos）用浅色竖线标记
        draw_x = self._x_pos + self._label_width
        if draw_x >= self.width():
            return
        painter.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200), 1))
        painter.drawLine(draw_x, 0, draw_x, self.height())

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        """Widget 尺寸变化时重新分配缓冲。"""
        super().resizeEvent(event)
        # 保存旧 X 位置（在新缓冲中等效映射）
        old_x = self._x_pos
        old_w = event.oldSize().width()
        new_w = event.size().width()

        # 重新分配双缓冲
        self._realloc_buffers()

        # 尝试在新缓冲中继续绘制
        # 如果旧缓冲有效，复制旧数据过来
        # 简化处理：reset
        self._x_pos = min(old_x, max(0, new_w - self._label_width - 1))
        self.update()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        """
        滚轮调整 n_plus（缩放因子）：
        - 滚轮上滚：n_plus /= 2（放大，即波形显示范围更宽）
        - 滚轮下滚：n_plus *= 2（缩小，即波形显示范围更窄）
        范围限制：1 ~ 32768
        """
        delta = event.angleDelta().y()
        if delta == 0:
            return

        if delta > 0:
            # 放大（n_plus 减小 → y_scal 增大 → 同等电压偏移像素更少）
            new_n_plus = self._n_plus / 2.0
        else:
            # 缩小（n_plus 增大 → y_scal 减小 → 同等电压偏移像素更多）
            new_n_plus = self._n_plus * 2.0

        # 限幅
        new_n_plus = float(np.clip(new_n_plus, self.N_PLUS_MIN, self.N_PLUS_MAX))

        if abs(new_n_plus - self._n_plus) > 0.5:
            self._n_plus = new_n_plus
            self._y_scal = self.Y_UNIT_MV / self._n_plus
            print(f"[EEGPixelWidget] nPlus={self._n_plus:.0f}, y_scal={self._y_scal:.6f} mV/px")
            self.update()

        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        """鼠标左键按下：暂停/恢复绘图（扩展用）。"""
        pass

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        """
        键盘快捷键：
        - R: 重置
        - M: 切换模拟数据
        - +/-: 调整 n_plus
        - G: 切换网格
        """
        key = event.key()
        if key == QtCore.Qt.Key_R:
            self.reset()
            print("[EEGPixelWidget] 已重置")
        elif key == QtCore.Qt.Key_M:
            self._mock_enabled = not self._mock_enabled
            self.enable_mock_data(self._mock_enabled)
        elif key == QtCore.Qt.Key_Plus or key == QtCore.Qt.Key_Equal:
            self.set_n_plus(self._n_plus / 2.0)
            print(f"[EEGPixelWidget] nPlus={self._n_plus:.0f}")
            self.update()
        elif key == QtCore.Qt.Key_Minus:
            self.set_n_plus(self._n_plus * 2.0)
            print(f"[EEGPixelWidget] nPlus={self._n_plus:.0f}")
            self.update()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def get_display_info(self) -> dict:
        """返回当前显示状态信息（调试用）。"""
        return {
            "channel_count": self.channel_count,
            "sample_rate": self.sample_rate,
            "n_plus": self._n_plus,
            "y_scal": self._y_scal,
            "x_pos": self._x_pos,
            "sample_counter": self._sample_counter,
            "time_sec": self._sample_counter / max(1, self.sample_rate),
            "mock_enabled": self._mock_enabled,
            "buffer_size": (
                f"{self._buffer1.width()}x{self._buffer1.height()}"
                if self._buffer1 else "None"
            ),
        }


# ============================================================================
# 单元测试（standalone）
# ============================================================================
if __name__ == "__main__":
    import sys
    import threading
    import random

    app = QtWidgets.QApplication(sys.argv)

    # 创建一个共享 buffer（模拟 real-time 数据）
    class FakeSharedBuffer:
        """模拟 SharedBuffer，往里 push DataPacket。"""
        def __init__(self):
            self.data = []
            self.lock = threading.Lock()

        def push(self, packet):
            with self.lock:
                self.data.append(packet)

        def pop_all(self):
            with self.lock:
                d = list(self.data)
                self.data.clear()
                return d

    buf = FakeSharedBuffer()

    # 创建 widget
    widget = EEGPixelWidget(
        channel_names=STANDARD_32_CHANNELS,
        sample_rate=1000,
    )
    widget.resize(1200, 600)
    widget.show()
    widget.enable_mock_data(True)  # 开启模拟数据

    def producer_loop():
        """在后台线程中模拟数据推送。"""
        import time
        t0 = time.time()
        phase = 0.0
        while True:
            # 每 20ms 推送一批（50 samples）
            now = time.time()
            t = now - t0
            n = 50
            t_arr = np.linspace(t - 0.05, t, n)

            alpha = np.sin(2 * np.pi * 10 * t_arr) * 50.0  # 50 µV
            delta = np.sin(2 * np.pi * 2 * t_arr + phase) * 30.0
            noise = np.random.normal(0, 5, (n, 32))

            vals = noise + alpha[:, None] * 0.5 + delta[:, None] * 0.3
            widget.update_data(vals)

            phase += 0.1
            time.sleep(0.02)

    th = threading.Thread(target=producer_loop, daemon=True)
    th.start()

    sys.exit(app.exec_())
