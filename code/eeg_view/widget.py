"""EEGViewWidget 主类。"""

import time
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from shared_buffer import SharedBuffer, DataPacket

try:
    from diag import log as _dlog
except Exception:  # diag 未安装时退回 print，保证模块仍可独立导入
    def _dlog(msg):
        print(msg, flush=True)

from .channel_meta import (
    STANDARD_32_CHANNELS, REGION_ORDER, ChannelItemDelegate,
    region_of, get_channel_color, get_default_gain,
)
from .filters import FilterState
from .mock_data import MockDataGenerator
from .recorder import Recorder
from .persister import save_record_to_files

__all__ = ["EEGViewWidget", "save_record_to_files", "STANDARD_32_CHANNELS"]


class EEGViewWidget(QtWidgets.QWidget):
    """32 通道 EEG 实时显示（心电监护仪式扫屏）。"""
    markerLocated = QtCore.pyqtSignal(object)

    def __init__(self, shared_buffer: SharedBuffer,
                 channel_names=None, parent=None, marker_session=None):
        super().__init__(parent)
        self.shared_buffer = shared_buffer
        self.channel_names = channel_names or STANDARD_32_CHANNELS
        self.channel_count = len(self.channel_names)
        self.offset_step = 1.0 / max(self.channel_count, 1)

        # 扫屏参数
        self.sample_rate = 1000.0
        self.window_sec = 10.0
        self._max_samples = int(self.window_sec * self.sample_rate)
        self._write_idx = 0
        self._last_key = 0.0
        self._wall_clock_t0_ms = None
        # X 轴起点（session 累计秒，每跨一个 window 滚一次）
        self._x_offset_sec = 0.0
        # X 轴刻度步长（秒）：2 → 02:00 / 02:02 / 02:04 ... 翻页式
        self.X_AXIS_STEP_SEC = 2.0
        self._paused = False

        # 显示
        # display_scale：把 "µV 数值" 转成 "绘制坐标单位" 的换算系数。
        # 公式: y_units = voltage(V) × amplitude_scale × display_scale × gain + offset
        # amplitude_scale = 1e6 把 V → µV（数值），所以 y_units = value_µV × display_scale × gain + offset
        # 目标：100µV × display_scale × 0.3 = 0.015（占通道 1/2 高度）
        # → display_scale = 0.015 / (100 × 0.3) = 5e-4
        # 留点余量取 1e-3：100µV → 0.03 units（占通道一半），200µV → 0.06，500µV → 0.15
        # 0~1 坐标能容纳到 6.7mV 都不会爆。
        self.display_scale = 1e-3
        self.amplitude_scale = 1e6
        self.filter_low_hz = 0.01
        self.filter_high_hz = 30.0

        # 子模块
        self.filter_state = FilterState(self.channel_count)
        self.recorder = Recorder(marker_session)
        self.mock = MockDataGenerator(self.channel_names, sample_rate=self.sample_rate,
                                      session=self.recorder.session)
        self._pending_marker_display = []

        self._init_ui()
        self._init_signals()

        # 数据 buffer：每通道一个固定大小 np 数组（C++ 风格）。
        # 初始填 0（不要用 NaN：pyqtgraph 会把 NaN 之间画成贯穿的垂直竖线，
        # 在放大到 µV 单位后尤其难看）。0 代表"基线/无信号"，自然贴在中线上。
        self._buf = np.zeros((self.channel_count, self._max_samples))

        # 定时刷新
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._update_view)
        self._timer.start(40)

        # 初始化 X 轴刻度（翻页式 mm:ss），避免默认刻度突兀
        self._refresh_x_labels()

    # ---------------- UI ----------------

    def _init_ui(self):
        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 左侧通道列表
        self.label_list = QtWidgets.QListWidget()
        self.label_list.setFixedWidth(110)
        self.label_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.label_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.label_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.label_list.setItemDelegate(ChannelItemDelegate(self.label_list))
        main_layout.addWidget(self.label_list)

        # 右侧绘图（主图 + 下方 marker 条）
        right_v = QtWidgets.QVBoxLayout()
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.setSpacing(0)
        main_layout.addLayout(right_v, 1)

        self.plot = pg.PlotWidget(background="#161D33")
        self.plot.getPlotItem().setContentsMargins(0, 0, 0, 0)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setMenuEnabled(False)
        self.plot.setXRange(0, self.window_sec, padding=0)
        self.plot.setYRange(0, 1, padding=0)
        self.plot.getPlotItem().getViewBox().setBackgroundColor("#161D33")
        right_v.addWidget(self.plot, 1)

        # marker 条：X 轴下方一窄条，Y 范围 [-0.6, 0.6]，X 与主图同步
        # 收到一个 marker 就画一个黄色方块 + label 数字
        self.marker_bar = pg.PlotWidget(background="#0F1424")
        self.marker_bar.setMouseEnabled(x=False, y=False)
        self.marker_bar.setMenuEnabled(False)
        self.marker_bar.hideButtons()
        self.marker_bar.setXRange(0, self.window_sec, padding=0)
        self.marker_bar.setYRange(-0.6, 0.6, padding=0)
        self.marker_bar.setFixedHeight(34)
        # 隐藏 X 轴标签（marker 条不重复打时间）
        self.marker_bar.getPlotItem().getAxis("bottom").setStyle(showValues=False)
        self.marker_bar.getPlotItem().getAxis("left").setStyle(showValues=False)
        # 不画边框
        self.marker_bar.getPlotItem().setContentsMargins(0, 0, 0, 0)
        # 顶部加一条细分隔线（视觉上把 marker 条与主图分开）
        sep = pg.InfiniteLine(pos=0.6, angle=0,
                              pen=pg.mkPen(color="#2A3553", width=1))
        sep.setZValue(-5)
        self.marker_bar.addItem(sep)
        right_v.addWidget(self.marker_bar)

        # marker 列表：(x, label, scatter_item, text_item)
        self._markers = []

        self._create_channels()
        self._install_grid()
        self._install_axis()

        # 两个 PlotWidget 分别布局；相同 XRange 并不保证同一时刻落在同一像素。
        # 标记栏预留与主图相同的 Y 轴宽度，随通道文字/字体变化自动同步。
        for side in ("left", "right"):
            self.plot.getAxis(side).geometryChanged.connect(self._sync_marker_axis_widths)
        self._sync_marker_axis_widths()
        self.marker_bar.setXLink(self.plot)

        self.plot.wheelEvent = self._on_plot_wheel  # type: ignore

    def _sync_marker_axis_widths(self):
        for side in ("left", "right"):
            width = self.plot.getAxis(side).width()
            axis = self.marker_bar.getAxis(side)
            if abs(axis.width() - width) > 0.01:
                axis.setWidth(width)

    def _install_axis(self):
        ax = self.plot.getAxis("bottom")
        ax.setLabel(text="时间", color="#8C97B4")
        ax.setStyle(tickFont=QtGui.QFont("Consolas", 8))
        # X 轴刻度在 _update_view 里动态设

    def _install_grid(self):
        # 通道分隔横线
        for i in range(1, self.channel_count):
            line = pg.InfiniteLine(
                pos=i * self.offset_step, angle=0,
                pen=pg.mkPen(color="#2A3553", width=1, style=QtCore.Qt.DashLine),
            )
            line.setZValue(-5)
            self.plot.addItem(line)

    def _create_channels(self):
        self.curves = []
        self.label_list.clear()
        for i, name in enumerate(self.channel_names):
            offset = 1.0 - (i + 0.5) / max(self.channel_count, 1)
            pen = pg.mkPen(color=get_channel_color(name), width=1)
            pen.setCosmetic(True)
            curve = self.plot.plot(pen=pen, name=name)
            curve.setProperty("localGain", get_default_gain(name))
            curve.setProperty("normOffset", offset)
            self.curves.append(curve)
            self.label_list.addItem(QtWidgets.QListWidgetItem(name))

        # Y 轴刻度（通道名）
        axis_left = self.plot.getAxis("left")
        axis_left.setTicks([[
            (float(self.curves[i].property("normOffset")), self.channel_names[i])
            for i in range(self.channel_count)
        ]])

    # ---------------- 公开 API ----------------

    def set_paused(self, paused): self._paused = bool(paused)

    def set_filter(self, sample_rate, low_hz, high_hz):
        if sample_rate > 0:
            self.sample_rate = float(sample_rate)
            self._max_samples = int(self.window_sec * self.sample_rate)
            # 与 __init__ 一致用 0 作基线（不要 NaN，会被 pyqtgraph 画成贯穿竖线）
            self._buf = np.zeros((self.channel_count, self._max_samples))
            self._write_idx = 0
        self.filter_low_hz = max(0.0, float(low_hz))
        self.filter_high_hz = max(0.0, float(high_hz))
        self.filter_state.reset(self.channel_count)

    def enable_mock_data(self, enabled=True, sample_rate=None):
        self.mock.set_enabled(enabled, sample_rate=sample_rate or self.sample_rate)

    def inject_mock_trigger(self, value, hold_ms=50):
        self.mock.inject_trigger(value, hold_ms=hold_ms)

    def add_marker(self, label, ts_ms=None):
        """在 X 轴下方画一个黄色方块 + label 数字。

        坐标语义（与主图 mm:ss 刻度轴严格同轴）：
          marker X = (ts_ms - t0) / 1000        # t0 = _wall_clock_t0_ms
        t0 在主图启动那一帧确定；mm:ss 刻度轴（_refresh_x_labels）与波形 X
        （t = _x_offset_sec + arange(N)/sample_rate）也都用这个起点，
        所以 marker 天然落在刻度轴的正确位置上。

        超出当前可见窗口 [offset, offset+window_sec] 的 marker 不画
        （画在 XRange 之外也看不见）。
        """
        if self._wall_clock_t0_ms is None:
            _dlog(f"[EEGMarker] 丢弃(t0未建立) label={label} ts_ms={ts_ms} "
                  f"last_key={getattr(self, '_last_key', None)!r}")
            return  # 主图还没启动，丢弃（避免起点错位）
        if ts_ms is None:
            ts_ms = int(time.time() * 1000)
        rel_x = (ts_ms - self._wall_clock_t0_ms) / 1000.0
        lo = float(self._x_offset_sec)
        hi = lo + float(self.window_sec)
        # 未入窗口（早于当前窗口起点）或超出当前窗口 → 丢弃（留痕，不再静默）
        if rel_x < lo or rel_x > hi - 0.02:
            _dlog(f"[EEGMarker] 丢弃(超出窗口) label={label} ts_ms={ts_ms} "
                  f"t0={self._wall_clock_t0_ms} rel_x={rel_x:.3f}s "
                  f"lo={lo:.1f} hi={hi:.1f} off_by={rel_x - lo:+.3f}s")
            return
        _dlog(f"[EEGMarker] 画出 label={label} rel_x={rel_x:.3f}s "
              f"窗口=[{lo:.1f},{hi:.1f}]")
        self._draw_marker(rel_x, label)

    def _draw_marker(self, x, label):
        # 黄色方块（pg.ScatterPlotItem 12px²）
        sp = pg.ScatterPlotItem(
            x=[float(x)], y=[0.0],
            size=14, symbol="s",
            brush=pg.mkBrush(255, 215, 0, 220),  # 金黄
            pen=pg.mkPen(180, 140, 0, 255, width=1),
        )
        sp.setZValue(20)
        self.marker_bar.addItem(sp)
        # 数字（label）放在方块正下方
        txt = pg.TextItem(text=str(int(label)), color="#FFE680",
                          anchor=(0.5, 1.0))
        txt.setPos(float(x), -0.15)
        txt.setZValue(21)
        font = QtGui.QFont("Consolas", 8, QtGui.QFont.Bold)
        txt.setFont(font)
        self.marker_bar.addItem(txt)
        # 顶部一根细分隔线（视觉强调）
        vline = pg.InfiniteLine(pos=float(x), angle=90,
                                pen=pg.mkPen(255, 215, 0, 100, width=1,
                                             style=QtCore.Qt.DashLine))
        vline.setZValue(15)
        self.marker_bar.addItem(vline)
        self._markers.append((sp, txt, vline))

    def _clear_markers(self):
        for items in self._markers:
            for it in items:
                self.marker_bar.removeItem(it)
        self._markers = []

    def start_record(self, subject, meta=None):
        self.recorder.start(subject, self.channel_names, self.sample_rate,
                            self.amplitude_scale, meta, self.filter_low_hz, self.filter_high_hz)

    def add_record_event(self, event_type, channel, label, source="vr"):
        self.recorder.add_event(event_type, channel, label, source=source)

    def stop_and_save_record(self, save_dir): return self.recorder.stop_and_save(save_dir)
    def stop_and_save_record_async(self, save_dir, on_done=None):
        self.recorder.stop_and_save_async(save_dir, on_done)

    def reset_view(self):
        self._wall_clock_t0_ms = None
        self._last_key = 0.0
        self._x_offset_sec = 0.0
        self._write_idx = 0
        self._buf.fill(0.0)
        for c in self.curves: c.clear()
        self.shared_buffer.drain()
        self.recorder.reset()
        self.filter_state.reset(self.channel_count)
        self._clear_markers()
        self._pending_marker_display = []

    def update_channels(self, names):
        self.channel_names = names
        self.channel_count = len(names)
        self.offset_step = 1.0 / max(self.channel_count, 1)
        # 与 __init__ 一致用 0 作基线（不要 NaN，会被 pyqtgraph 画成贯穿竖线）
        self._buf = np.zeros((self.channel_count, self._max_samples))
        self._write_idx = 0
        self._create_channels()
        self._install_grid()
        self.filter_state.reset(self.channel_count)
        self.mock.channel_names = names

    def update_channel_count(self, n):
        if n == self.channel_count: return
        self.update_channels([f"Ch{i+1}" for i in range(n)])

    def _init_signals(self):
        self.label_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.label_list.customContextMenuRequested.connect(self._show_context_menu)

    # ---------------- 主循环 ----------------

    def _update_view(self):
        now_ms = int(time.time() * 1000)
        packets = self.mock.generate_packets() if self.mock.enabled \
            else self.shared_buffer.snapshot(consumer_id="eeg_view")
        if not packets:
            self._display_located_markers()
            return
        if self.recorder.enabled:
            self.recorder.append_packets(packets)
        if self._wall_clock_t0_ms is None:
            first_index = getattr(packets[0], "sample_index", None)
            self._wall_clock_t0_ms = (packets[0].timestamp - first_index * 1000.0 / self.sample_rate
                                      if first_index is not None else packets[0].timestamp)
            # 从录制采样索引还原同一会话起点，避免墙钟与写指针分别定位。
            _dlog(f"[EEGMarker] t0 建立: now_ms={now_ms} "
                  f"pkt0_ts={int(packets[0].timestamp)} "
                  f"pktN_ts={int(packets[-1].timestamp)} "
                  f"pkt_span={(int(packets[-1].timestamp) - int(packets[0].timestamp)) / 1000.0:.3f}s "
                  f"=> t0={self._wall_clock_t0_ms} "
                  f"dev_now-t0={(now_ms - self._wall_clock_t0_ms) / 1000.0:+.3f}s")

        x = np.stack([p.voltage for p in packets])  # (n_pkt, n_ch)
        # _last_key: 用最新 packet 的 timestamp（sample_counter 时间轴），
        # 与环形 buffer 扫屏位置 / marker / X 刻度 同轴
        last_index = getattr(packets[-1], "sample_index", None)
        self._last_key = (last_index / self.sample_rate if last_index is not None else
                          (packets[-1].timestamp - self._wall_clock_t0_ms) / 1000.0)
        if not self._paused:
            self.filter_state.apply(x, self.sample_rate, self.filter_low_hz, self.filter_high_hz)

        # === X 轴偏移（wrap 后滚到下一个 window）===
        # offset_sec 是当前 XRange 起点（window_sec 的整数倍），
        # 每跨过一个 window 整边界，主图 / sweep / XRange / marker_bar 一起跟着滚一格。
        win = self.window_sec
        new_offset = int(self._last_key // win) * win
        wrapped = (new_offset != self._x_offset_sec)
        self._x_offset_sec = new_offset
        if wrapped:
            # 新周期：清掉旧 marker（深色窄条一并重置），让 marker 历史不跨窗堆叠
            self._clear_markers()

        # === 扫屏写指针（心电监护仪式：写一段、擦一段到基线、不画垂直断点）===
        n_pkt = x.shape[0]
        n_ch = min(x.shape[1], self.channel_count)
        N = self._max_samples

        for k in range(n_pkt):
            sample_index = getattr(packets[k], "sample_index", None)
            wi = self._write_idx if sample_index is None else sample_index % N
            # 写入当前位置
            self._buf[:n_ch, wi] = x[k, :n_ch]
            # 写入点之后 20 个点：填为基线（0 V），
            # 这样 pyqtgraph 不会因 NaN 把回扫尾迹画成贯穿顶/底的垂直竖线。
            # 视觉上就是"信号被一段平直的基线吃掉"，是传统心电监护仪的扫屏感。
            for b in range(1, 21):
                self._buf[:n_ch, (wi + b) % N] = 0.0
            # 推进写指针
            self._write_idx = (wi + 1) % N

        # === 画曲线（每通道独立增益 + 偏置，固定 display_scale，不自适应）===
        # t 用 session 绝对秒（与 marker / tick 一致），XRange 起点跟着 offset 走
        N = self._max_samples
        t = self._x_offset_sec + np.arange(N) / self.sample_rate
        amp = self.amplitude_scale  # V -> µV
        for i, curve in enumerate(self.curves):
            if i >= n_ch: break
            gain = float(curve.property("localGain") or 1.0)
            offset = float(curve.property("normOffset") or 0.5)
            y = self._buf[i] * amp * self.display_scale * gain + offset
            curve.setData(t, y)

        # === 扫屏位置（由环形 buffer 零值尾迹自然呈现）===
        # _last_key 已在 process 开头用 packet.timestamp 设置，与 X 轴 / marker / curve 同轴
        self.plot.setXRange(self._x_offset_sec,
                            self._x_offset_sec + self.window_sec, padding=0)
        # marker 条通过 setXLink 跟随主图，避免两处独立设置范围发生偏移。

        # === 录制 ===
        self.shared_buffer.ack(consumer_id="eeg_view")

        self._refresh_x_labels()
        self._display_located_markers()

    def _display_located_markers(self):
        # 此方法只由 GUI 定时器调用；网络/采集线程仅提交事件，不访问 Qt 绘图对象。
        for event in self.recorder.session.take_notifications():
            self._pending_marker_display.append(event)
            self.markerLocated.emit(event)
        if self._wall_clock_t0_ms is None:
            return
        lo, hi = self._x_offset_sec, self._x_offset_sec + self.window_sec
        future = []
        for event in self._pending_marker_display:
            x = event["sample_index"] / self.sample_rate
            if lo <= x < hi:
                self._draw_marker(x, event["label"])
            elif x >= hi:
                future.append(event)
            # 已跨过去的屏幕不再绘制，但事件仍完整保存在 session 和 EDF 中。
        self._pending_marker_display = future

    def _refresh_x_labels(self):
        """X 轴刻度：mm:ss 翻页式（步长 = X_AXIS_STEP_SEC，默认 2s）。

        - tick 起点对齐到 step 的整数倍（0/2/4/6 ... 或 10/12/14 ...）
        - 跨窗后 offset 变 → tick 自动从 12/14/16... 继续
        - 文本 = mm:ss（两位分 + 冒号 + 两位秒）
        """
        if self._wall_clock_t0_ms is None:
            self.plot.getAxis("bottom").setTicks([[]]); return
        step = float(getattr(self, "X_AXIS_STEP_SEC", 2.0))
        if step <= 0:
            step = 2.0
        win = float(self.window_sec)
        t_start = float(self._x_offset_sec)
        first = int(t_start // step) * step
        ticks = []
        x = first
        while x < t_start + win + 1e-9:
            if x >= t_start - 1e-9:
                s = int(round(x))
                ticks.append((float(x), f"{s // 60:02d}:{s % 60:02d}"))
            x += step
        self.plot.getAxis("bottom").setTicks([ticks])

    # ---------------- 选中 / 菜单 ----------------

    def _on_selection_changed(self):
        sel = {self.label_list.row(it) for it in self.label_list.selectedItems()}
        for i, curve in enumerate(self.curves):
            pen = curve.opts["pen"]
            color = pen.color()
            if i in sel:
                pen.setWidth(2); color.setAlpha(255)
            else:
                pen.setWidth(1); color.setAlpha(80)
            pen.setColor(color); curve.setPen(pen)

    def _show_context_menu(self, pos):
        if not self.label_list.itemAt(pos): return
        menu = QtWidgets.QMenu(self)
        if menu.exec_(self.label_list.mapToGlobal(pos)) == menu.addAction("隐藏通道"):
            for it in self.label_list.selectedItems(): it.setHidden(True)
        else:
            for i in range(self.label_list.count()):
                self.label_list.item(i).setHidden(False)

    # ---------------- 滚轮：Y 缩放/平移/调 gain ----------------

    def _on_plot_wheel(self, event):
        delta = event.angleDelta().y()
        if delta == 0: return
        vb = self.plot.getPlotItem().vb
        mods = event.modifiers()

        if not (mods & (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier)):
            # 缩放（以鼠标为中心）
            pos = event.position() if hasattr(event, "position") else event.pos()
            scene_pt = self.plot.mapToScene(QtCore.QPoint(int(pos.x()), int(pos.y())))
            mouse_y = vb.mapSceneToView(scene_pt).y()
            vr = vb.viewRect()
            factor = 0.9 if delta > 0 else 1.1
            new_h = max(0.05, min(vr.height() * factor, 5.0))
            t = mouse_y + (vr.top() - mouse_y) * (new_h / vr.height())
            b = mouse_y + (vr.bottom() - mouse_y) * (new_h / vr.height())
            vb.setYRange(b, t, padding=0); event.accept(); return

        if mods & QtCore.Qt.ControlModifier:
            # 平移
            vr = vb.viewRect()
            step = vr.height() * 0.3 * (1 if delta > 0 else -1)
            vb.setYRange(vr.bottom() + step, vr.top() + step, padding=0)
            event.accept(); return

        # Shift + 滚轮：选中通道调 gain
        sel = self.label_list.selectedItems()
        if not sel: return
        factor = 1.1 if delta > 0 else 0.9
        for it in sel:
            row = self.label_list.row(it)
            if row >= len(self.curves): continue
            curve = self.curves[row]
            cur = float(curve.property("localGain") or 1.0)
            new = float(max(0.05, min(cur * factor, 100.0)))
            curve.setProperty("localGain", new)
            off = float(curve.property("normOffset") or 0.5)
            x, y = curve.getData()
            if y is not None and y.size > 0:
                curve.setData(x, off + (y - off) * (new / cur))


# 兼容旧导入
def __getattr__(name):
    if name == "save_record_to_files": return save_record_to_files
    raise AttributeError(name)
