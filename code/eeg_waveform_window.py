"""
独立 EEG 波形窗口（Standalone EEG Waveform Window）

- 32 导联纵向堆叠，每通道一行，Y 轴为 µV 刻度
- 左列为通道名，右列为 pyqtgraph 波形图
- 顶部工具栏：增益缩放、滤波设置、显示/隐藏通道、滚动锁定
"""

from typing import List, Optional
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from shared_buffer import SharedBuffer, DataPacket
from biostyle import apply_theme, style_plot, PLOT, BORDER, MUTED
from eeg_view.channel_meta import get_channel_color


# 标准 32 导联
STANDARD_32_CHANNELS: List[str] = [
    "Fp1", "Fpz", "Fp2",
    "F7", "F3", "Fz", "F4", "F8",
    "FC5", "FC1", "FC2", "FC6",
    "T7", "C3", "Cz", "C4", "T8",
    "M1", "CP5", "CP1", "CP2", "CP6", "M2",
    "P7", "P3", "Pz", "P4", "P8",
    "POz", "O1", "Oz", "O2",
]

CHANNEL_COLORS: List[QtGui.QColor] = [get_channel_color(name) for name in STANDARD_32_CHANNELS]


class EEGWaveformWindow(QtWidgets.QWidget):
    """
    独立 EEG 波形窗口。
    左侧固定宽度显示通道名 + µV 刻度，右侧 pyqtgraph 显示波形。
    """

    def __init__(
        self,
        shared_buffer: SharedBuffer,
        channel_names: Optional[List[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.shared_buffer = shared_buffer
        self.channel_names = channel_names or STANDARD_32_CHANNELS
        self.channel_count = max(1, len(self.channel_names))

        # 绘图参数
        self._window_sec = 5.0        # 时间窗口宽度（秒）
        self._history_sec = 10.0       # 数据历史保留时长
        self._amplitude_scale = 1e6    # V -> µV
        self._gain = 1.0              # 全局增益
        self._paused = False

        # 滤波
        self.sample_rate = 1000.0
        self.filter_low_hz = 1.0
        self.filter_high_hz = 30.0
        self._hp_state = np.zeros(self.channel_count, dtype=float)
        self._hp_prev_x = np.zeros(self.channel_count, dtype=float)
        self._lp_state = np.zeros(self.channel_count, dtype=float)

        # 时间戳
        self._start_timestamp: Optional[int] = None
        self._last_key = 0.0

        # 通道可见性
        self._hidden_channels: set = set()

        # 重入保护标志（在 _init_ui 之前设置，避免初次 setXRange/setYRange 触发回调）
        self._in_range_change = False

        self._init_ui()
        self._init_timer()

    # -------------------- UI Setup -------------------- #

    def _init_ui(self) -> None:
        apply_theme(self)
        self.setWindowTitle("EEG 波形")
        self.setMinimumSize(1000, 700)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ---- 工具栏 ----
        toolbar = self._make_toolbar()
        main_layout.addLayout(toolbar)

        # ---- 主绘图区 ----
        content_layout = QtWidgets.QHBoxLayout()
        content_layout.setSpacing(0)

        # 左侧：通道名 + µV 刻度
        self._label_widget = QtWidgets.QWidget()
        self._label_layout = QtWidgets.QVBoxLayout(self._label_widget)
        self._label_layout.setContentsMargins(4, 4, 4, 4)
        self._label_layout.setSpacing(0)
        self._label_widget.setFixedWidth(90)
        self._label_widget.setStyleSheet(f"background: {PLOT}; border-right: 1px solid {BORDER};")

        # 右侧：pyqtgraph 绘图区
        self.plot = pg.PlotWidget(background=PLOT)
        style_plot(self.plot)
        self.plot.showGrid(x=True, y=True)
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.setXRange(0, self._window_sec)
        self.plot.setMenuEnabled(False)
        self.plot.setDownsampling(False)
        self.plot.setClipToView(False)
        self.plot.sigRangeChanged.connect(self._on_range_changed)

        content_layout.addWidget(self._label_widget)
        content_layout.addWidget(self.plot, 1)

        main_layout.addLayout(content_layout)

        # 创建通道行
        self._channel_rows: List[dict] = []
        self._build_channel_rows()

    def _make_toolbar(self) -> QtWidgets.QLayout:
        layout = QtWidgets.QHBoxLayout()
        layout.setSpacing(8)

        # 增益
        lbl = QtWidgets.QLabel("增益:")
        lbl.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl)

        self._gain_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._gain_slider.setRange(1, 20)
        self._gain_slider.setValue(10)
        self._gain_slider.setMaximumWidth(150)
        self._gain_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self._gain_slider.setTickInterval(1)
        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        layout.addWidget(self._gain_slider)

        self._gain_label = QtWidgets.QLabel("×1.0")
        self._gain_label.setMinimumWidth(40)
        layout.addWidget(self._gain_label)

        layout.addSpacing(16)

        # 滤波
        lbl2 = QtWidgets.QLabel("滤波 (Hz):")
        lbl2.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl2)

        self._hp_combo = QtWidgets.QComboBox()
        self._hp_combo.addItems(["0", "0.5", "1", "2", "5", "10"])
        self._hp_combo.setCurrentText("1")
        self._hp_combo.currentTextChanged.connect(self._on_filter_changed)
        layout.addWidget(self._hp_combo)

        lbl3 = QtWidgets.QLabel("–")
        layout.addWidget(lbl3)

        self._lp_combo = QtWidgets.QComboBox()
        self._lp_combo.addItems(["10", "20", "30", "50", "70", "100", "200"])
        self._lp_combo.setCurrentText("30")
        self._lp_combo.currentTextChanged.connect(self._on_filter_changed)
        layout.addWidget(self._lp_combo)

        layout.addSpacing(16)

        # 时间窗口
        lbl4 = QtWidgets.QLabel("窗口:")
        lbl4.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl4)

        self._win_combo = QtWidgets.QComboBox()
        self._win_combo.addItems(["2", "5", "10", "20"])
        self._win_combo.setCurrentText("5")
        self._win_combo.currentTextChanged.connect(self._on_window_changed)
        layout.addWidget(self._win_combo)
        layout.addWidget(QtWidgets.QLabel("秒"))

        layout.addStretch()

        # 暂停
        self._pause_btn = QtWidgets.QPushButton("暂停")
        self._pause_btn.setCheckable(True)
        self._pause_btn.clicked.connect(self._on_pause_toggled)
        layout.addWidget(self._pause_btn)

        # 全部显示
        btn_show = QtWidgets.QPushButton("显示全部")
        btn_show.clicked.connect(self._show_all)
        layout.addWidget(btn_show)

        return layout

    def _build_channel_rows(self) -> None:
        """构建每通道的一行：标签 + 刻度 + 曲线"""
        # 清除旧组件
        while self._label_layout.count():
            item = self._label_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for curve in getattr(self, '_curves', []):
            self.plot.removeItem(curve)
        self._curves: List[pg.PlotDataItem] = []
        self._channel_rows.clear()

        # Y 轴范围：±100 µV × 增益 × 通道数（用重入保护避免回调循环）
        self._in_range_change = True
        try:
            y_max = self.channel_count * 100.0 * self._gain
            self.plot.setYRange(-20, y_max)
        finally:
            self._in_range_change = False

        for i, name in enumerate(self.channel_names):
            # Y 轴居中位置（从下往上编号 0=底部）
            y_center = (i + 0.5) * 100.0 * self._gain

            # 左侧标签行
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(2, 1, 2, 1)
            row_layout.setSpacing(2)

            # 通道名
            ch_label = QtWidgets.QLabel(name)
            ch_label.setFixedWidth(36)
            ch_label.setAlignment(QtCore.Qt.AlignCenter)
            ch_label.setStyleSheet(
                f"color: {CHANNEL_COLORS[i % len(CHANNEL_COLORS)].name()};"
                "font-weight: bold; font-size: 11px;"
            )
            row_layout.addWidget(ch_label)

            # µV 刻度（居中显示）
            scale_label = QtWidgets.QLabel("0 µV")
            scale_label.setFixedWidth(48)
            scale_label.setAlignment(QtCore.Qt.AlignCenter)
            scale_label.setStyleSheet(f"color: {MUTED}; font-size: 9px;")
            row_layout.addWidget(scale_label)

            self._label_layout.addWidget(row_widget)

            # 绘制曲线（中心在 y_center）
            color = CHANNEL_COLORS[i % len(CHANNEL_COLORS)]
            curve = self.plot.plot(
                pen=pg.mkPen(color=color, width=1),
                name=name
            )
            curve.setProperty("y_center", y_center)
            curve.setProperty("channel_index", i)
            self._curves.append(curve)

            self._channel_rows.append({
                "name": name,
                "y_center": y_center,
                "scale_label": scale_label,
                "ch_label": ch_label,
                "curve": curve,
            })

        self._label_layout.addStretch()

    # -------------------- Timer -------------------- #

    def _init_timer(self) -> None:
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._update)
        self._timer.start(80)  # ~12.5 FPS

    # -------------------- Update -------------------- #

    def _update(self) -> None:
        # 使用 snapshot() + ack()，与主窗口 EEGView 共享 buffer 而不抢数据
        packets: List[DataPacket] = self.shared_buffer.snapshot(consumer_id="waveform_window")
        if not packets:
            return

        # 首包时间戳
        if self._start_timestamp is None:
            self._start_timestamp = packets[0].timestamp

        # 跳过时间戳倒退的包（SDK 重连或缓冲残留）
        valid_mask = np.array(
            [(p.timestamp - self._start_timestamp) >= 0 for p in packets]
        )
        packets = [p for p, m in zip(packets, valid_mask) if m]
        if not packets:
            return

        ts = np.array(
            [(p.timestamp - self._start_timestamp) / 1000.0 for p in packets],
            dtype=float
        )
        vals = np.stack([p.voltage for p in packets])  # (N, C) 单位：mV

        if ts.size > 0:
            self._last_key = float(ts[-1])

        if self._paused:
            return

        # V -> µV
        x = vals * 1e3

        # 滤波
        if self.sample_rate > 0:
            dt = 1.0 / self.sample_rate
            if self.filter_low_hz > 0 and self.filter_low_hz < (self.sample_rate / 2):
                alpha_hp = np.exp(-2 * np.pi * self.filter_low_hz * dt)
                hp_out = np.empty_like(x)
                prev = self._hp_state.copy()
                prev_x = self._hp_prev_x.copy()
                for n in range(x.shape[0]):
                    for ch in range(x.shape[1]):
                        prev[ch] = alpha_hp * (prev[ch] + x[n, ch] - prev_x[ch])
                        hp_out[n, ch] = prev[ch]
                        prev_x[ch] = x[n, ch]
                self._hp_state = prev
                self._hp_prev_x = prev_x
                x = hp_out

            if self.filter_high_hz > 0 and self.filter_high_hz < (self.sample_rate / 2):
                alpha_lp = np.exp(-2 * np.pi * self.filter_high_hz * dt)
                lp_out = np.empty_like(x)
                prev = self._lp_state.copy()
                for n in range(x.shape[0]):
                    for ch in range(x.shape[1]):
                        prev[ch] = alpha_lp * prev[ch] + (1 - alpha_lp) * x[n, ch]
                        lp_out[n, ch] = prev[ch]
                self._lp_state = prev
                x = lp_out

        # 绘制每条曲线
        for i, curve in enumerate(self._curves):
            if i >= x.shape[1]:
                break
            if i in self._hidden_channels:
                curve.setVisible(False)
                continue
            curve.setVisible(True)
            y_center = self._channel_rows[i]["y_center"]
            y_segment = y_center + x[:, i] * self._gain

            x_old, y_old = curve.getData()
            if x_old is None or len(x_old) == 0:
                x_new, y_new = ts, y_segment
            else:
                x_new = np.concatenate([x_old, ts])
                y_new = np.concatenate([y_old, y_segment])

            # 裁剪历史
            if x_new.size > 0:
                t_max = x_new[-1]
                keep = x_new >= (t_max - self._history_sec)
                x_new = x_new[keep]
                y_new = y_new[keep]

            curve.setData(x_new, y_new)

            # 更新刻度标签
            v_min = float(np.min(y_new)) if y_new.size > 0 else 0
            v_max = float(np.max(y_new)) if y_new.size > 0 else 0
            v_peak = max(abs(v_min - y_center), abs(v_max - y_center))
            self._channel_rows[i]["scale_label"].setText(f"±{v_peak:.0f} µV")

        # 标记本消费者已读完
        self.shared_buffer.ack(consumer_id="waveform_window")

        # 滑动时间窗口（用重入保护避免触发 sigRangeChanged 递归）
        if self._last_key > self._window_sec:
            self._in_range_change = True
            try:
                x_start = self._last_key - self._window_sec
                self.plot.setXRange(x_start, self._last_key)
            finally:
                self._in_range_change = False
        if self._last_key > self._window_sec:
            x_start = self._last_key - self._window_sec
            self.plot.setXRange(x_start, self._last_key)

    # -------------------- Slots -------------------- #

    def update_channels(self, channel_names: List[str]) -> None:
        """外部调用：更新通道列表"""
        if not channel_names:
            return
        self.channel_names = channel_names
        self.channel_count = len(channel_names)
        # 重置滤波状态
        self._hp_state = np.zeros(self.channel_count, dtype=float)
        self._hp_prev_x = np.zeros(self.channel_count, dtype=float)
        self._lp_state = np.zeros(self.channel_count, dtype=float)
        self._build_channel_rows()

    def set_filter(self, sample_rate: float, low_hz: float, high_hz: float) -> None:
        self.sample_rate = sample_rate
        self.filter_low_hz = low_hz
        self.filter_high_hz = high_hz
        self._hp_state = np.zeros(self.channel_count, dtype=float)
        self._hp_prev_x = np.zeros(self.channel_count, dtype=float)
        self._lp_state = np.zeros(self.channel_count, dtype=float)

    def reset_view(self) -> None:
        self._start_timestamp = None
        self._last_key = 0.0
        for curve in self._curves:
            curve.setData([], [])

    def _on_gain_changed(self, val: int) -> None:
        self._gain = val / 10.0
        self._gain_label.setText(f"×{self._gain:.1f}")
        # 重算 Y 轴（用重入保护）
        self._in_range_change = True
        try:
            y_max = self.channel_count * 100.0 * self._gain
            self.plot.setYRange(-20, y_max)
        finally:
            self._in_range_change = False
        # 更新各通道中心
        for i, row in enumerate(self._channel_rows):
            y_center = (i + 0.5) * 100.0 * self._gain
            row["y_center"] = y_center
            row["curve"].setProperty("y_center", y_center)

    def _on_filter_changed(self) -> None:
        self.filter_low_hz = float(self._hp_combo.currentText())
        self.filter_high_hz = float(self._lp_combo.currentText())
        self._hp_state = np.zeros(self.channel_count, dtype=float)
        self._hp_prev_x = np.zeros(self.channel_count, dtype=float)
        self._lp_state = np.zeros(self.channel_count, dtype=float)

    def _on_window_changed(self, val: str) -> None:
        self._window_sec = float(val)

    def _on_pause_toggled(self, checked: bool) -> None:
        self._paused = checked
        self._pause_btn.setText("继续" if checked else "暂停")

    def _on_range_changed(self) -> None:
        """禁止用户拖动 X 轴到负值"""
        if self._in_range_change:
            return
        self._in_range_change = True
        try:
            x_range = self.plot.viewRange()[0]
            if x_range[0] < 0:
                self.plot.setXRange(0, x_range[1] - x_range[0])
        finally:
            self._in_range_change = False

    def _show_all(self) -> None:
        self._hidden_channels.clear()
        for i in range(self.channel_count):
            if i < len(self._curves):
                self._curves[i].setVisible(True)
