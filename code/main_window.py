import os
import sys
import time
import traceback
from typing import Optional, List

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

# 统一诊断日志：必须在所有业务模块 import 之前装载
from diag import install_global_hook, log, log_exc, section, debug
install_global_hook()
section("程序启动")

from data_worker import DataWorker, EemagineSDK
from eeg_view_widget import EEGViewWidget
from eeg_waveform_window import EEGWaveformWindow
from erp_view_widget import ERPViewWidget
from erp_analyzer import ERPAnalyzer, EventMarker
from erp_preprocessing import ERPPreprocessingConfig
from impedance_widget import ImpedanceWidget
from impedance_worker import ImpedanceWorker
from shared_buffer import DataPacket
from marker_session import encode_marker
from vr_marker_receiver import VRMarkerReceiver
from vr_video_widget import VRVideoWidget
from gaze_trajectory_widget import GazeTrajectoryWidget
from biostyle import apply_theme, SUCCESS, ERROR


class MainWindow(QtWidgets.QMainWindow):
    startAcquisition = QtCore.pyqtSignal()
    stopAcquisition = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stopping = False
        self._erp_next_index = 0
        self.setWindowTitle("EEG VR Viewer")
        self.resize(1400, 800)

        self._init_data_worker()
        self._init_ui()
        self._init_vr_receiver()
        self._init_ui_timer()

    def _init_data_worker(self):
        self._data_thread = QtCore.QThread(self)
        self._data_worker = DataWorker(
            sample_rate=1000,
            dll_path=r"E:\VR展示\eego-SDK.dll"
        )
        self._data_worker.moveToThread(self._data_thread)
        self._data_thread.started.connect(self._data_worker.initializeSDK)
        self._data_worker.sdkConnected.connect(self._on_sdk_connected)
        self._data_worker.channelNamesAvailable.connect(self._on_channels_available)
        self._data_worker.errorOccurred.connect(self._on_error)
        self.startAcquisition.connect(self._data_worker.startRead)
        self.stopAcquisition.connect(self._data_worker.stopRead)
        self._data_worker.acquisitionStopped.connect(self._on_acquisition_stopped)
        self._data_thread.start()

        # 阻抗 worker 共享同一个 EemagineSDK 实例
        # （SDK 同时只允许一个 stream，EEG/阻抗互斥）
        self._impedance_thread: Optional[QtCore.QThread] = None
        self._impedance_worker: Optional[ImpedanceWorker] = None

    def _init_ui(self):
        central = QtWidgets.QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)
        apply_theme(self)

        root_layout = QtWidgets.QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._create_sidebar()
        self._create_main_content()

        root_layout.addWidget(self.toggleContainer)
        root_layout.addWidget(self.mainContainer, 1)

        self._init_sidebar_animation()

    def _create_sidebar(self):
        # 采集参数、实验控制和连接状态。
        self.sideBarWidget = QtWidgets.QWidget()
        self.sideBarWidget.setObjectName("sideBarWidget")
        # 宽度约束在 _init_sidebar_animation 中设置，此处先给一个临时宽度
        self.sideBarWidget.setMinimumWidth(240)
        layout = QtWidgets.QVBoxLayout(self.sideBarWidget)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(12)

        layout.addWidget(self._make_param_group())
        layout.addWidget(self._make_ctrl_group())
        layout.addWidget(self._make_status_group())
        layout.addStretch()

        # 折叠/展开切换按钮：独立容器，固定宽度，始终可见
        self.toggleButton = QtWidgets.QPushButton("◀")
        self.toggleButton.setObjectName("toggleButton")
        self.toggleButton.setFixedSize(18, 48)
        self.toggleButton.setCursor(QtCore.Qt.PointingHandCursor)
        self.toggleButton.clicked.connect(self._toggle_sidebar)

        # 把 toggleButton 和 sideBar 装入一个水平容器：按钮固定 18px 在最左
        self.toggleContainer = QtWidgets.QWidget()
        toggle_layout = QtWidgets.QHBoxLayout(self.toggleContainer)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(0)
        toggle_layout.addWidget(self.toggleButton, 0, QtCore.Qt.AlignVCenter)
        toggle_layout.addWidget(self.sideBarWidget)

    def _make_param_group(self) -> QtWidgets.QGroupBox:
        """采集参数组：采样率 / 滤波带 / 显示窗长。

        数值含义（与 eego SDK 实际硬件一致）：
          - 采样率: 1000Hz (eego 放大器默认)
          - 滤波:  0.01-30Hz （带通：去除直流漂移 + 高频噪声；保留 P300 频段）
        """
        group = self._group_box("采集参数")
        form = QtWidgets.QFormLayout(group)
        # 比例 1:3 → label 最小化，field 区最大（容纳 4 个 label+2 combo）
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(6)

        # 采样率：默认 1000Hz（与 DataWorker / eego SDK 一致）
        self.combo_sampling = self._combo(["500", "1000", "2000"], "1000")
        form.addRow("采样率:", self.combo_sampling)
        self.combo_sampling.currentIndexChanged.connect(self._on_sampling_changed)

        # 滤波带：默认 0.01Hz - 30Hz（P300 ERP 标准带通）
        # UI 极简版：把"低/高"和"Hz"合并标签，去掉多余 label；4 个小 label 改成短符号
        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.setSpacing(3)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        self.combo_band_low = self._combo(
            ["0", "0.01", "0.1", "0.5", "1", "3", "5"], "0.01"
        )
        self.combo_band_high = self._combo(
            ["15", "20", "30", "40", "50", "70"], "30"
        )
        # combo 内部 padding 砍掉，让短数值不撑宽
        for w in (self.combo_band_low, self.combo_band_high):
            w.setMinimumWidth(54)
            w.setMaximumWidth(64)
            w.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        # 标签"低/高"做成竖排更省位（用 "/" 上下两字符），单位 Hz 放行首
        sep_lbl = QtWidgets.QLabel("/")
        sep_lbl.setAlignment(QtCore.Qt.AlignCenter)
        sep_lbl.setFixedWidth(6)
        filter_layout.addWidget(self.combo_band_low)
        filter_layout.addWidget(sep_lbl)
        filter_layout.addWidget(self.combo_band_high)
        filter_layout.addWidget(QtWidgets.QLabel("Hz"))
        filter_layout.addStretch(1)
        # 把 "滤波(Hz):" 缩短成 "滤波:"
        form.addRow("滤波:", filter_layout)
        self.combo_band_low.currentIndexChanged.connect(self._on_filter_changed)
        self.combo_band_high.currentIndexChanged.connect(self._on_filter_changed)
        self.check_empty_acquisition = QtWidgets.QCheckBox("空采调试（非实验）")
        self.check_empty_acquisition.setToolTip("保留真实采集与VR打标，在线ERP跳过坏导检测和试次剔除")
        self.check_empty_acquisition.toggled.connect(self._on_experiment_mode_changed)
        form.addRow(self.check_empty_acquisition)
        return group

    def _make_ctrl_group(self) -> QtWidgets.QGroupBox:
        group = self._group_box("实验控制")

        btn_layout = QtWidgets.QHBoxLayout()
        self.pushButton_start = self._button("开始实验", "primary", checkable=True)
        btn_layout.addWidget(self.pushButton_start)

        self.statusLabel = QtWidgets.QLabel("状态: 空闲")
        self.statusLabel.setProperty("role", "status")
        self.statusLabel.setAlignment(QtCore.Qt.AlignCenter)

        self.pushButton_import = self._button("导入数据", "secondary")

        # 阻抗检测：弹出独立窗口
        self.pushButton_impedance = self._button("阻抗检测", "secondary")
        self.pushButton_impedance.setToolTip(
            "打开脑电帽阻抗检测窗口\n（用于检查电极与头皮接触质量）"
        )
        self.pushButton_impedance.clicked.connect(self._open_impedance_dialog)

        # VR 视频流显隐切换（dockwidget 替代原先的独立波形窗口思路）
        self.pushButton_toggle_vr = self._button("隐藏 VR 视频", "warn")
        self.pushButton_toggle_vr.setCheckable(True)
        self.pushButton_toggle_vr.setChecked(False)

        self.pushButton_toggle_gaze = self._button("隐藏注视轨迹", "secondary")
        self.pushButton_toggle_gaze.setCheckable(True)
        self.pushButton_toggle_gaze.setChecked(True)
        self.pushButton_toggle_gaze.toggled.connect(self._on_toggle_gaze_dock)

        layout = QtWidgets.QVBoxLayout(group)
        layout.setSpacing(10)
        layout.addLayout(btn_layout)
        layout.addWidget(self.statusLabel)
        layout.addWidget(self.pushButton_import)
        layout.addWidget(self.pushButton_impedance)
        layout.addWidget(self.pushButton_toggle_vr)
        layout.addWidget(self.pushButton_toggle_gaze)

        self.pushButton_start.toggled.connect(self._on_start_toggled)
        self.pushButton_import.clicked.connect(self._on_import)
        self.pushButton_toggle_vr.toggled.connect(self._on_toggle_vr_dock)
        return group

    def _make_status_group(self) -> QtWidgets.QGroupBox:
        group = self._group_box("连接状态")

        self.vr_status_indicator = QtWidgets.QLabel("●")
        self.vr_status_indicator.setProperty("role", "dot")
        self.vr_status_label = QtWidgets.QLabel("未连接")
        self.vr_status_label.setProperty("role", "dim")

        self.eeg_status_indicator = QtWidgets.QLabel("●")
        self.eeg_status_indicator.setProperty("role", "dot")
        self.eeg_status_label = QtWidgets.QLabel("未连接")
        self.eeg_status_label.setProperty("role", "dim")

        layout = QtWidgets.QVBoxLayout(group)
        layout.addLayout(self._status_row(
            "VR:", self.vr_status_indicator, self.vr_status_label
        ))
        layout.addLayout(self._status_row("EEG:", self.eeg_status_indicator, self.eeg_status_label))
        return group

    def _status_row(self, label: str, *widgets) -> QtWidgets.QHBoxLayout:
        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(QtWidgets.QLabel(label))
        for w in widgets:
            layout.addWidget(w)
        layout.addStretch()
        return layout

    def _create_main_content(self):
        self.erp_analyzer = ERPAnalyzer(
            sample_rate=1000.0,
            n_channels=32,
            pre_stimulus_ms=100,
            post_stimulus_ms=500,
        )
        self._erp_buffer = []
        self._erp_max_size = 10000

        self.mainContainer = QtWidgets.QWidget()
        self.mainContainer.setObjectName("mainContainer")
        main_layout = QtWidgets.QVBoxLayout(self.mainContainer)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.eegView = EEGViewWidget(self._data_worker.shared_buffer, parent=self.mainContainer,
                                     marker_session=self._data_worker.markers)
        self.eegView.markerLocated.connect(self._on_marker_located)
        self.erp_analyzer.configure_preprocessing(self.eegView.channel_names, self._erp_preprocessing_config())
        self.erp_analyzer.configure_analysis_groups(self.eegView.channel_names)
        self.erpView = ERPViewWidget(self.erp_analyzer, parent=self.mainContainer)
        self.erpView.set_channel_list(self.eegView.channel_names)
        self.erpView.setMinimumHeight(200)

        self.vr_video_widget = VRVideoWidget(self)
        video_panel, self.vr_dock = self._make_docked_panel(
            "VR 视频流", self.vr_video_widget, "vr_video_dock")
        self.vr_dock.visibilityChanged.connect(self._on_vr_dock_visibility)
        self.toggle_vr_action = self.vr_dock.toggleViewAction()
        self.toggle_vr_action.setText("VR 视频流")

        self.gaze_trajectory_widget = GazeTrajectoryWidget(self)
        gaze_panel, self.gaze_dock = self._make_docked_panel(
            "注视轨迹", self.gaze_trajectory_widget, "gaze_trajectory_dock")
        self.gaze_dock.visibilityChanged.connect(self._on_gaze_dock_visibility)

        # 两行共享高度分界，左右分界同步；右侧面板仍可独立浮动。
        self.top_panels = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.bottom_panels = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        for row, left, right in ((self.top_panels, self.erpView, video_panel),
                                 (self.bottom_panels, self.eegView, gaze_panel)):
            row.setChildrenCollapsible(False)
            row.setHandleWidth(6)
            left.setMinimumWidth(500)
            right.setMinimumWidth(320)
            row.addWidget(left)
            row.addWidget(right)
            row.setStretchFactor(0, 3)
            row.setStretchFactor(1, 2)
            row.setSizes([700, 420])
        self.top_panels.splitterMoved.connect(
            lambda *_: self.bottom_panels.setSizes(self.top_panels.sizes()))
        self.bottom_panels.splitterMoved.connect(
            lambda *_: self.top_panels.setSizes(self.bottom_panels.sizes()))
        self.panel_rows = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.panel_rows.setChildrenCollapsible(False)
        self.panel_rows.setHandleWidth(6)
        self.panel_rows.addWidget(self.top_panels)
        self.panel_rows.addWidget(self.bottom_panels)
        self.panel_rows.setStretchFactor(0, 1)
        self.panel_rows.setStretchFactor(1, 1)
        self.panel_rows.setSizes([350, 430])
        main_layout.addWidget(self.panel_rows)
        for plot in (self.erpView.plot, self.eegView.plot):
            plot.getViewBox().sigResized.connect(self._align_waveform_axes)
        QtCore.QTimer.singleShot(0, self._align_waveform_axes)

    def _align_waveform_axes(self):
        """让 ERP 和 EEG 的波形绘图区左边界位于同一竖线。"""
        positions = []
        for plot in (self.erpView.plot, self.eegView.plot):
            point = plot.mapFromScene(plot.getViewBox().sceneBoundingRect().topLeft())
            positions.append(plot.viewport().mapTo(self.mainContainer, point).x())
        delta = positions[1] - positions[0]
        if abs(delta) > 1:
            axis = self.erpView.plot.getAxis("left")
            axis.setWidth(max(0, axis.width() + delta))

    def _make_docked_panel(self, title, widget, name):
        host = QtWidgets.QMainWindow(self.mainContainer)
        host.setContentsMargins(0, 0, 0, 0)
        dock = QtWidgets.QDockWidget(title, host)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                         | QtWidgets.QDockWidget.DockWidgetFloatable
                         | QtWidgets.QDockWidget.DockWidgetClosable)
        host.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        return host, dock

    def _init_sidebar_animation(self):
        self._sidebar_collapsed = False
        self._anim_group = QtCore.QParallelAnimationGroup(self)
        # 同时驱动 minimumWidth 和 maximumWidth，允许平滑收缩到 0
        self.sideBarWidget.setMinimumWidth(0)
        self.sideBarWidget.setMaximumWidth(240)
        self.sideBarWidget.resize(240, self.sideBarWidget.height())

        self._anim_side = QtCore.QPropertyAnimation(self.sideBarWidget, b"minimumWidth")
        self._anim_side.setDuration(200)
        self._anim_side.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._anim_group.addAnimation(self._anim_side)
        self._anim_side_max = QtCore.QPropertyAnimation(self.sideBarWidget, b"maximumWidth")
        self._anim_side_max.setDuration(200)
        self._anim_side_max.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._anim_group.addAnimation(self._anim_side_max)

    def _init_vr_receiver(self):
        # 更新电脑本地配置；头显运行时自动发现电脑尚未实现，见 README 待办。
        try:
            from vr_config import auto_discover_and_update
            self.vr_config = auto_discover_and_update()
            port = int(self.vr_config['trigger_port'])
            log(f"[MainWindow] VR config 生效: PC={self.vr_config['pc_ip']} port={port}")
        except Exception:
            log_exc("[MainWindow] vr_config 初始化异常，使用默认 12345:")
            self.vr_config = None
            port = 12345

        self.vr_marker_receiver = VRMarkerReceiver(
            host="0.0.0.0", port=port, on_marker_received=self._on_vr_marker
        )
        self.vr_marker_receiver.set_video_frame_callback(self._on_vr_frame)
        self.vr_marker_receiver.set_gaze_callback(self._on_vr_gaze)
        self.vr_marker_receiver.set_gaze_rect_callback(self._on_vr_gaze_rect)
        self.vr_marker_receiver.set_gaze_target_id_callback(self._on_vr_gaze_id)
        if not self.vr_marker_receiver.start():
            log("[VRMarker] 启动失败：端口可能被占用 或 VRMarkerReceiver 内部异常")
            QtWidgets.QMessageBox.warning(self, "VR连接错误", "VR 标记接收器启动失败，可能端口被占用。")

        # 硬件 trigger（可选；没有串口打标器时自动降级为软件 marker）
        try:
            from eego_trigger import init_trigger
            self.eego_trigger = init_trigger(port="COM5", baudrate=57600)
            if not self.eego_trigger.enabled:
                log("[MainWindow] 硬件 trigger 未启用，将仅依赖 EEG 流中的软件 marker")
        except Exception:
            log_exc("[MainWindow] eego_trigger 初始化异常:")
            self.eego_trigger = None

    def _init_ui_timer(self):
        self._ui_timer = QtCore.QTimer(self)
        self._ui_timer.timeout.connect(self._process_erp)
        self._ui_timer.timeout.connect(self._update_vr_status)
        self._ui_timer.timeout.connect(self._update_eeg_status)
        self._ui_timer.start(100)

        # 与 UI 下拉默认值（采样率 1000Hz、滤波 0.01-30Hz）保持一致
        self.current_sampling_rate = 1000
        self.current_band_low = 0.01
        self.current_band_high = 30.0
        self.save_dir = r"E:\VR展示\Save"

    # ---- Helper methods ----

    def _group_box(self, title: str) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox(title)
        # 样式由 biostyle 统一管理。
        return group

    def _combo(self, items: list, default: str = None) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.addItems(items)
        if default:
            combo.setCurrentText(default)
        # 样式由 MAIN_QSS 统一管理
        return combo

    def _button(self, text: str, role: str = "secondary", checkable: bool = False) -> QtWidgets.QPushButton:
        """创建使用统一主题的操作按钮。"""
        btn = QtWidgets.QPushButton(text)
        btn.setCheckable(checkable)
        btn.setProperty("role", role if role else "")
        return btn

    # ---- Sidebar animation ----

    def _toggle_sidebar(self):
        """折叠/展开左侧栏；切换按钮始终可见"""
        self._sidebar_collapsed = not self._sidebar_collapsed
        start_w = self.sideBarWidget.width()
        if self._sidebar_collapsed:
            self._anim_side.setStartValue(start_w)
            self._anim_side.setEndValue(0)
            self._anim_side_max.setStartValue(start_w)
            self._anim_side_max.setEndValue(0)
            self.toggleButton.setText("▶")
        else:
            self._anim_side.setStartValue(start_w)
            self._anim_side.setEndValue(240)
            self._anim_side_max.setStartValue(start_w)
            self._anim_side_max.setEndValue(240)
            self.toggleButton.setText("◀")
        self._anim_group.start()

    # ---- Signal handlers ----

    def _erp_preprocessing_config(self):
        # 开关只改变在线分析策略，不改变放大器输入和原始EDF/CSV保存内容。
        return ERPPreprocessingConfig(empty_acquisition_debug=self.check_empty_acquisition.isChecked())

    def _on_experiment_mode_changed(self, checked):
        self.erp_analyzer.reset()
        self.erp_analyzer.configure_preprocessing(self.eegView.channel_names, self._erp_preprocessing_config())
        self.erpView._update_plot()

    def _on_start_toggled(self, checked: bool):
        if checked:
            # ---- 开始实验 ----
            # 弹窗前先检查"采集中"状态（防止重复 start）
            subject, ok = QtWidgets.QInputDialog.getText(self, "被试名称", "请输入被试名字:")
            if not ok or not subject.strip():
                self.pushButton_start.blockSignals(True)
                self.pushButton_start.setChecked(False)
                self.pushButton_start.blockSignals(False)
                return
            subject = subject.strip()

            # 1) 配置硬件 / 滤波 / ERP（先于实际读数据，避免一开始几个 sample 没滤波）
            self._data_worker.set_sample_rate(self.current_sampling_rate)
            self.eegView.set_filter(self.current_sampling_rate, self.current_band_low, self.current_band_high)
            self.erp_analyzer.reset()
            self._erp_buffer.clear()
            self._erp_next_index = 0

            # 1b) 预热 _erp_buffer：用 shared_buffer 里已有的 packets 填满
            # 这样实验开始瞬间来的 marker 不会因 buffer 不够 pre-stimulus 被丢弃。
            pre_fill = []
            if self.erp_analyzer.preprocessor is None:
                with self._data_worker.shared_buffer.mutex:
                    for pkt in self._data_worker.shared_buffer.data_queue:
                        pre_fill.append((pkt.timestamp, np.array(pkt.voltage, dtype=float)))
                self._erp_buffer.extend(pre_fill)
            # 保留最近的 epoch_length 个 sample（避免内存无限增长）
            epoch_len = self.erp_analyzer.epoch_length
            if len(self._erp_buffer) > epoch_len * 2:
                self._erp_buffer = self._erp_buffer[-epoch_len * 2:]

            # 2) UI 清屏 + 启动绘制
            self.eegView.reset_view()
            self.eegView.set_paused(False)        # 解禁画图（之前若暂停过）

            # 3) 开始记录（写盘 + 录 trigger 通道）
            self.gaze_trajectory_widget.start_recording()
            self.eegView.start_record(subject, meta={
                "sampling_rate": self.current_sampling_rate,
                "filter_low_hz": self.current_band_low,
                "filter_high_hz": self.current_band_high,
                "acquisition_mode": "mock" if self.eegView.mock.enabled else "hardware",
                "experiment_mode": "empty_acquisition_debug" if self.check_empty_acquisition.isChecked() else "experiment",
                "hardware_counter_present": self._data_worker._sample_counter_pos is not None and not self.eegView.mock.enabled,
            })

            # 4) 最后才真正启动硬件读取
            self.pushButton_start.setText("结束实验")
            self.check_empty_acquisition.setEnabled(False)
            self.combo_sampling.setEnabled(False)
            self.statusLabel.setText("状态: 模拟采集中..." if self.eegView.mock.enabled else "状态: 采集中...")
            if self.eegView.mock.enabled:
                self.eegView.enable_mock_data(True, self.current_sampling_rate)
            else:
                self._data_worker.request_start()
                self.startAcquisition.emit()
        else:
            self._stopping = True
            self.pushButton_start.setEnabled(False)
            self.statusLabel.setText("状态: 正在停止并保存...")
            self._data_worker.request_stop()
            if self.eegView.mock.enabled:
                self.eegView.mock.enabled = False
                self._data_worker.markers.finish()
                self._on_acquisition_stopped()

    @QtCore.pyqtSlot()
    def _on_acquisition_stopped(self):
        if not self.eegView.recorder.enabled:
            return
        self.gaze_trajectory_widget.stop_recording()
        # 先等采集线程退出，再处理最后一批显示/事件通知并取得完整录制快照。
        self._stopping = True
        self.pushButton_start.blockSignals(True)
        self.pushButton_start.setChecked(False)
        self.pushButton_start.blockSignals(False)
        self.pushButton_start.setEnabled(False)
        self.pushButton_start.setText("开始实验")
        self.eegView._update_view()
        self.eegView.set_paused(True)
        # 结束按钮已经取消选中，仍需处理最后一批 EEG 与刚定位的事件通知。
        self._process_erp(force=True)
        self.erp_analyzer.finish(self._erp_buffer)
        debug(f"[ERP] 采集停止，ERP当前: target={self.erp_analyzer.n_target_trials}, "
            f"standard={self.erp_analyzer.n_standard_trials}, "
            f"未纳入={dict(self.erp_analyzer.rejection_counts)}")

        def saved(path):
            if getattr(self, '_closing', False):
                return
            # 原始数据先保存；后台坏导模型就绪后回放剩余连续数据，再导出ERP。
            # 因此分析初始化较慢不阻塞原始数据落盘，导出也不会使用半完成的模型。
            if path and self.erp_analyzer.preprocessing_pending:
                self.statusLabel.setText("状态: 原始数据已保存，等待坏导插值初始化完成...")
                QtCore.QTimer.singleShot(200, lambda: saved(path))
                return
            self._process_erp(force=True)
            self.erp_analyzer.finish(self._erp_buffer)
            analysis_error = None
            gaze_error = None
            if path:
                prefix = path[:-12] if path.endswith('.events.json') else os.path.splitext(path)[0]
                try:
                    self.gaze_trajectory_widget.save_csv(prefix + '.gaze.csv')
                except OSError as exc:
                    gaze_error = str(exc)
                    log_exc("[注视轨迹] CSV 保存失败；原始脑电数据已保存")
                try:
                    self.erp_analyzer.export_results(prefix)
                except Exception as exc:
                    analysis_error = str(exc)
                    log_exc("[ERP] 分析结果保存失败；原始数据已保存")
            self._stopping = False
            self.pushButton_start.setEnabled(True)
            self.combo_sampling.setEnabled(True)
            self.check_empty_acquisition.setEnabled(True)
            preprocessor = self.erp_analyzer.preprocessor
            if not path:
                self.statusLabel.setText("状态: 保存失败，请检查日志")
            elif analysis_error:
                self.statusLabel.setText("状态: 原始数据已保存，ERP结果导出失败")
            elif gaze_error:
                self.statusLabel.setText("状态: 脑电数据已保存，注视轨迹CSV保存失败")
            elif preprocessor is not None and preprocessor.state != 'ready':
                self.statusLabel.setText("状态: 原始数据已保存，ERP预处理不可用")
            else:
                self.statusLabel.setText("状态: 已停止，ERP结果已保存")
            log(f"[ERP] 最终结果: target={self.erp_analyzer.n_target_trials}, "
                f"standard={self.erp_analyzer.n_standard_trials}, "
                f"未纳入={dict(self.erp_analyzer.rejection_counts)}")
            if path:
                QtWidgets.QMessageBox.information(self, "保存完成", f"记录已保存至:\n{path}")
        self.eegView.stop_and_save_record_async(self.save_dir, on_done=saved)

    def _on_sampling_changed(self):
        self.current_sampling_rate = int(self.combo_sampling.currentText())
        self._data_worker.set_sample_rate(self.current_sampling_rate)

    def _on_filter_changed(self):
        self.current_band_low = float(self.combo_band_low.currentText())
        self.current_band_high = float(self.combo_band_high.currentText())
        self.eegView.set_filter(self.current_sampling_rate, self.current_band_low, self.current_band_high)

    def _on_sdk_connected(self, channel_count: int, sample_rate: int):
        print(f"[SDK] 连接成功: {channel_count} 通道, {sample_rate} Hz")
        self.eeg_status_indicator.setStyleSheet(f"font: bold 16px; color: {SUCCESS};")
        self.eeg_status_label.setText(f"已连接 ({channel_count}通道)")
        self.current_sampling_rate = sample_rate
        idx = self.combo_sampling.findText(str(sample_rate))
        if idx >= 0:
            self.combo_sampling.blockSignals(True)
            self.combo_sampling.setCurrentIndex(idx)
            self.combo_sampling.blockSignals(False)
        # 关键：把真实 sample_rate 同步到 eegView（之前漏掉，eegView 还停在 500Hz，
        # 1000Hz 硬件会被当成 500Hz，2 秒就整屏清屏一次）
        if hasattr(self, 'eegView'):
            self.eegView.set_filter(sample_rate, self.current_band_low, self.current_band_high)
            # 真硬件连上时禁用 mock（_on_error fallback 时才会 enable）
            self.eegView.enable_mock_data(False)
            # 复位 ring + 时间，避免历史 mock 数据残留
            self.eegView.reset_view()
        # 同步到独立波形窗口（如有）
        if hasattr(self, '_eeg_waveform_window') and self._eeg_waveform_window is not None:
            try:
                if hasattr(self._eeg_waveform_window, 'set_sample_rate'):
                    self._eeg_waveform_window.set_sample_rate(sample_rate)
            except Exception as e:
                print(f"[独立波形窗口] 设置采样率失败: {e}")

    def _open_impedance_dialog(self) -> None:
        """
        打开独立的阻抗检测窗口（QDialog）。
        - 复用实例，避免反复创建销毁
        - 打开时：如果 EEG 在采集，先停止（SDK 同时只允许一个流）
        - 关闭时：停止阻抗 worker
        """
        # 懒创建弹窗
        if not hasattr(self, "_impedance_dialog") or self._impedance_dialog is None:
            self._impedance_dialog = ImpedanceWidget(self)
            self._impedance_dialog.setModal(False)
            # 弹窗关闭 → 停止 worker（无论用户点 X 还是点关闭按钮）
            self._impedance_dialog.finished.connect(self._on_impedance_dialog_closed)

        # 检查 SDK 是否就绪
        sdk = self._data_worker._sdk
        if sdk is None or not sdk._initialized:
            QtWidgets.QMessageBox.warning(
                self, "阻抗检测",
                "放大器尚未连接，无法启动阻抗检测。\n请先连接 eego 放大器。"
            )
            return

        # 如果 EEG 在采集中 → 自动停止
        if self.pushButton_start.isChecked():
            self.pushButton_start.blockSignals(True)
            self.pushButton_start.setChecked(False)
            self.pushButton_start.blockSignals(False)
            self._on_start_toggled(False)

        # 启动后台 worker，把数据送进弹窗
        self._start_impedance_worker()
        # 显示弹窗（如果已最小化，恢复）
        if self._impedance_dialog.isMinimized():
            self._impedance_dialog.showNormal()
        else:
            self._impedance_dialog.show()
        self._impedance_dialog.raise_()
        self._impedance_dialog.activateWindow()
        print("[MainWindow] 阻抗检测窗口已打开")

    def _on_impedance_dialog_closed(self, *args) -> None:
        """弹窗被关闭（X 或关闭按钮）时清理 worker。"""
        self._stop_impedance_worker()
        print("[MainWindow] 阻抗检测窗口已关闭")

    def _start_impedance_worker(self) -> None:
        if self._impedance_worker is not None:
            return
        sdk = self._data_worker._sdk
        if sdk is None:
            return
        self._impedance_thread = QtCore.QThread(self)
        self._impedance_worker = ImpedanceWorker(sdk)
        self._impedance_worker.moveToThread(self._impedance_thread)
        # 把数据送进弹窗（不弹窗时不连接）
        if hasattr(self, "_impedance_dialog") and self._impedance_dialog is not None:
            self._impedance_worker.impedanceData.connect(
                self._impedance_dialog.update_impedance
            )
            self._impedance_worker.sdkStateChanged.connect(
                self._impedance_dialog.set_sdk_connected
            )
            self._impedance_worker.errorOccurred.connect(
                lambda msg: QtWidgets.QMessageBox.warning(self, "阻抗检测错误", msg)
            )
        self._impedance_thread.started.connect(self._impedance_worker.start)
        self._impedance_thread.start()
        print("[MainWindow] 阻抗 worker 已启动")

    def _stop_impedance_worker(self) -> None:
        if self._impedance_worker is None:
            return
        self._impedance_worker.stop()
        if self._impedance_thread is not None:
            self._impedance_thread.quit()
            self._impedance_thread.wait(2000)
        self._impedance_worker = None
        self._impedance_thread = None
        print("[MainWindow] 阻抗 worker 已停止")

    @QtCore.pyqtSlot(list)
    def _on_channels_available(self, channel_names: list) -> None:
        """
        SDK 连接后，DataWorker 会发出真正参与业务（显示+分析）的通道名列表。
        这里更新 EEGView 与 ERPAnalyzer 的通道配置。
        """
        if not channel_names:
            return
        debug(f"[通道配置] UI 收到 {len(channel_names)} 个通道: {channel_names}")
        self._active_channel_names = channel_names
        # EEGView：重建通道标签/曲线
        self.eegView.update_channels(channel_names)
        # 同步真实 sample_rate 到 eegView（防御性：万一 _on_sdk_connected 先于 _on_channels_available）
        if self.current_sampling_rate and self.current_sampling_rate > 0:
            self.eegView.set_filter(self.current_sampling_rate, self.current_band_low, self.current_band_high)
        # 同步到独立波形窗口
        if hasattr(self, '_eeg_waveform_window') and self._eeg_waveform_window is not None:
            self._eeg_waveform_window.update_channels(channel_names)
        # ERPAnalyzer：重置以匹配新的通道数（保留已有事件定义）
        try:
            self.erp_analyzer.n_channels = len(channel_names)
            self.erp_analyzer.epoch_length = (
                self.erp_analyzer.pre_stimulus_samples + self.erp_analyzer.post_stimulus_samples
            )
            self.erp_analyzer.reset()
            self.erp_analyzer.configure_preprocessing(channel_names, self._erp_preprocessing_config())
            self.erp_analyzer.configure_analysis_groups(channel_names)
            self.erpView.set_channel_list(channel_names)
            debug(f"[ERPAnalyzer] 已重置为 {len(channel_names)} 通道")
        except Exception as e:
            print(f"[ERPAnalyzer] 重置失败: {e}")

    @QtCore.pyqtSlot(str)
    def _on_error(self, message: str):
        self.eeg_status_indicator.setStyleSheet(f"font: bold 16px; color: {ERROR};")
        self.eeg_status_label.setText("连接错误")
        print(f"[EEG] 放大器连接错误: {message}")
        if hasattr(self, "eegView") and not self.eegView.recorder.enabled:
            self.eegView.enable_mock_data(True)
            self.eeg_status_label.setText("模拟数据（非硬件采集）")
        # 采集中断由 acquisitionStopped 收尾保存，不能在一次真实录制中混入模拟数据。

    @QtCore.pyqtSlot(object)
    def _on_marker_located(self, event):
        # 软件事件由MarkerSession定位到样本后才入ERP队列；不用网络到达时刻直接切段。
        if event["event_type"] in {"target", "standard"}:
            self.erp_analyzer.add_event(EventMarker(event["sample_time_ms"],
                                                    event["event_type"], event["channel"]))

    def _process_erp(self, force=False):
        if not force and not self.pushButton_start.isChecked():
            return
        # 独立采样游标避免重复消费；ERP读取录制会话，不依赖绘图是否跳帧。
        packets = self._data_worker.markers.packets_since(self._erp_next_index)
        if packets:
            self._erp_next_index = packets[-1].sample_index + 1
        preprocessor = self.erp_analyzer.preprocessor
        frames = (preprocessor.process_batch(packets) if preprocessor is not None else
                  [(p.timestamp, np.array(p.voltage, dtype=float)) for p in packets])
        # Calibration may release a long backlog; process bounded chunks so early
        # events are extracted before their data rolls out of the display buffer.
        for offset in range(0, len(frames), 1000):
            chunk = frames[offset:offset+1000]
            self._erp_buffer.extend(chunk)
            del self._erp_buffer[:-self._erp_max_size]
            timestamp, arr = chunk[-1]
            self.erp_analyzer.process_data_packet(timestamp, arr, self._erp_buffer)
        # 通知可能晚于最后一批数据；无新样本时也要重试已经齐全的事件。
        if not packets and self._erp_buffer:
            ts, arr = self._erp_buffer[-1]
            self.erp_analyzer.process_data_packet(ts, arr, self._erp_buffer)

    def _on_toggle_vr_dock(self, checked: bool):
        """侧栏按钮：显示/隐藏 VR 视频流 dock 窗口"""
        if self.vr_dock.isVisible() != checked:
            self.vr_dock.setVisible(checked)
        self.pushButton_toggle_vr.setText("隐藏 VR 视频" if checked else "显示 VR 视频")

    def _on_toggle_gaze_dock(self, checked: bool):
        self.gaze_dock.setVisible(checked)

    def _on_gaze_dock_visibility(self, visible: bool):
        button = self.pushButton_toggle_gaze
        blocker = QtCore.QSignalBlocker(button)
        button.setChecked(visible)
        button.setText("隐藏注视轨迹" if visible else "显示注视轨迹")
        del blocker

    def _on_vr_dock_visibility(self, visible: bool):
        """dock 隐藏时（用户直接关闭）也要同步按钮状态"""
        if self.pushButton_toggle_vr.isChecked() != visible:
            blocker = self.pushButton_toggle_vr.blockSignals(True)
            self.pushButton_toggle_vr.setChecked(visible)
            self.pushButton_toggle_vr.setText("隐藏 VR 视频" if visible else "显示 VR 视频")
            self.pushButton_toggle_vr.blockSignals(blocker)

    def _on_import(self):
        os.makedirs(self.save_dir, exist_ok=True)
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择数据文件", self.save_dir,
            "数据文件 (*.csv *.edf *.cnt);;EDF (*.edf);;CSV (*.csv);;ANT CNT (*.cnt);;All Files (*.*)"
        )
        if not path:
            return
        try:
            from shared_buffer import DataPacket
            ext = os.path.splitext(path)[1].lower()
            if ext == '.cnt':
                self._import_cnt(path)
            elif ext == '.edf':
                self._import_edf(path)
            else:
                self._import_csv(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "导入失败", f"读取文件失败: {e}")

    def _import_cnt(self, path: str):
        from cnt_reader import read_cnt_file
        data, sample_rate, channel_names, start_time_ms = read_cnt_file(path)

        sr_str = str(int(sample_rate))
        idx = self.combo_sampling.findText(sr_str)
        if idx >= 0:
            self.combo_sampling.blockSignals(True)
            self.combo_sampling.setCurrentIndex(idx)
            self.combo_sampling.blockSignals(False)
            self.current_sampling_rate = int(sample_rate)
            self._data_worker.set_sample_rate(int(sample_rate))

        if len(channel_names) != len(self.eegView.channel_names):
            self.eegView.update_channels(channel_names)

        n_samples = data.shape[0]
        ts = np.arange(n_samples) / sample_rate

        self.eegView.reset_view()
        self.eegView.set_paused(False)
        self.eegView._start_timestamp = start_time_ms
        self.eegView._last_key = ts[-1] if ts.size > 0 else 0
        self.eegView.set_filter(sample_rate, self.current_band_low, self.current_band_high)

        progress = QtWidgets.QProgressDialog("正在导入...", "取消", 0, 100, self)
        progress.setWindowModality(QtCore.Qt.WindowModal)

        batch_size = 1000
        for batch_idx, start_idx in enumerate(range(0, n_samples, batch_size)):
            if progress.wasCanceled():
                break
            end_idx = min(start_idx + batch_size, n_samples)
            # CNT 文件里存的是 Volt；直接推入 buffer，eeg_view 端用 amplitude_scale 放大到 µV
            batch_data = data[start_idx:end_idx, :]
            timestamps_ms = ((ts[start_idx:end_idx] * 1000)).astype(np.int64)
            packets = [DataPacket(timestamp=t, voltage=batch_data[j, :].tolist())
                      for j, t in enumerate(timestamps_ms)]
            self._data_worker.shared_buffer.push_many(packets)
            self.eegView._update_view()
            progress.setValue(int((batch_idx + 1) * 100 / ((n_samples + batch_size - 1) // batch_size)))
            QtWidgets.QApplication.processEvents()

        progress.close()
        self.eegView._update_layout()
        self.statusLabel.setText(f"状态: 已导入 CNT ({len(channel_names)}通道)")

    def _import_csv(self, path: str):
        import json
        with open(path, encoding="utf-8") as f:
            names = f.readline().strip().split(",")
        data = np.atleast_2d(np.loadtxt(path, delimiter=",", skiprows=1))
        auxiliary = {"timestamp_ms", "trigger", "sample_counter", "sample_index", "hardware_trigger"}
        eeg_columns = [i for i,name in enumerate(names) if name not in auxiliary]
        if not eeg_columns or data.shape[1] != len(names):
            raise ValueError("文件格式错误")
        ts = data[:, names.index("timestamp_ms")] / 1000.0
        vals_v = data[:, eeg_columns] / self.eegView.amplitude_scale
        rate = None
        sidecar = os.path.splitext(path)[0] + ".events.json"
        if os.path.exists(sidecar):
            with open(sidecar, encoding="utf-8") as f:
                rate = json.load(f).get("sample_rate_hz")
        if not rate:
            positive_steps = np.diff(ts)
            positive_steps = positive_steps[positive_steps > 0]
            rate = 1.0 / np.median(positive_steps) if len(positive_steps) else 1000.0
        self._start_replay(ts, vals_v, float(rate), os.path.basename(path),
                           [names[i] for i in eeg_columns])

    def _replay_tick(self):
        """EDF/CSV 实时回放：每 100ms 推入下一批数据到 buffer。"""
        if not hasattr(self, "_replay_state") or self._replay_state is None:
            return
        st = self._replay_state
        if st["cursor"] >= st["n_samples"]:
            self._replay_timer.stop()
            self.statusLabel.setText(f"状态: 回放完成 {st['source']}")
            self._replay_state = None
            return
        end = min(st["cursor"] + st["batch_size"], st["n_samples"])
        ts = st["ts"]
        vals_v = st["vals_v"]
        timestamps_ms = (ts[st["cursor"]:end] * 1000).astype(np.int64)
        packets = [
            DataPacket(timestamp=int(t), voltage=vals_v[j, :].tolist())
            for j, t in enumerate(timestamps_ms)
        ]
        self._data_worker.shared_buffer.push_many(packets)
        st["cursor"] = end

    def _start_replay(self, ts: np.ndarray, vals_v: np.ndarray,
                      sample_rate: float, source_label: str,
                      channel_names: List[str]) -> None:
        """启动 EDF/CSV/CNT 共用的实时回放（按 sample_rate 切批）。"""
        n_samples = vals_v.shape[0]
        if n_samples <= 0:
            self.statusLabel.setText(f"状态: 已导入 {source_label} (空文件)")
            return
        self.eegView.reset_view()
        self.eegView.set_paused(False)
        self.eegView._start_timestamp = 0
        self.eegView.set_filter(sample_rate,
                                self.current_band_low, self.current_band_high)
        # 通道数变更要同步
        if len(channel_names) != len(self.eegView.channel_names):
            self.eegView.update_channels(channel_names)
        self.eegView._last_key = 0.0
        self._replay_state = {
            "ts": ts,
            "vals_v": vals_v,
            "cursor": 0,
            "batch_size": max(1, int(sample_rate // 10)),
            "n_samples": n_samples,
            "source": source_label,
        }
        if not hasattr(self, "_replay_timer") or self._replay_timer is None:
            self._replay_timer = QtCore.QTimer(self)
            self._replay_timer.timeout.connect(self._replay_tick)
        self._replay_timer.start(100)
        self.statusLabel.setText(
            f"状态: 正在回放 {source_label} ({n_samples} 样本)")

    def _import_edf(self, path: str):
        """读取 EDF+ 文件并启动回放。"""
        try:
            import mne
        except ImportError:
            raise ImportError("需要安装 mne 库: pip install mne")
        raw = mne.io.read_raw_edf(path, preload=True, verbose=False)
        channel_names = [name for name in raw.ch_names if name not in {"TRIG", "HW_TRIG"}]
        # MNE 已将 EEG 解码为 V；不能因触发通道幅值大而再除一次 1e6。
        data_v = raw.get_data(picks=channel_names).T
        sample_rate = float(raw.info["sfreq"])
        if sample_rate <= 0:
            raise ValueError(f"EDF 采样率无效: {sample_rate}")
        n = data_v.shape[0]
        ts = np.arange(n) / sample_rate
        self._start_replay(ts, data_v, sample_rate,
                           os.path.basename(path), channel_names)

    def _on_vr_marker(self, marker: EventMarker):
        """TCP 接收线程：只提交线程安全事件和可选串口发送，不访问 Qt 控件。"""
        label = encode_marker(marker.event_type, marker.channel)
        event = self._data_worker.markers.submit(
            label, event_type=marker.event_type, channel=marker.channel or 0, source="vr",
            received_ms=marker.timestamp_ms,
            received_monotonic_ns=getattr(marker, "received_monotonic_ns", None))
        if event is not None:
            trigger = getattr(self, "eego_trigger", None)
            if trigger is not None:
                trigger.send(label)

    def _on_vr_frame(self, frame: np.ndarray, seq: Optional[int] = None):
        """Video receiver supplies (RGB frame, sequence); sequence is optional for callers."""
        try:
            self.vr_video_widget.update_frame(frame)
        except Exception:
            self._swallow_video_err("frame")

    def _on_vr_gaze(self, x: float, y: float):
        self.gaze_trajectory_widget.update_gaze_point(x, y)
        try:
            self.vr_video_widget.update_gaze_point(x, y)
        except Exception:
            self._swallow_video_err("gaze")

    def _on_vr_gaze_rect(self, x: float, y: float, w: float, h: float):
        try:
            self.vr_video_widget.update_gaze_rect(x, y, w, h)
        except Exception:
            self._swallow_video_err("gaze_rect")

    def _on_vr_gaze_id(self, target_id: int):
        try:
            self.vr_video_widget.update_gaze_target_id(target_id)
        except Exception:
            self._swallow_video_err("gaze_id")

    def _swallow_video_err(self, kind: str) -> None:
        """视频/眼动回调高频率（每帧）调用，错误节流到 5s 一次。"""
        import time as _t
        now = _t.time()
        last = getattr(self, '_last_video_err_t', {}).get(kind, 0.0)
        if now - last < 5.0:
            return
        if not hasattr(self, '_last_video_err_t'):
            self._last_video_err_t = {}
        self._last_video_err_t[kind] = now
        log_exc(f"[VRVideo] {kind} 回调异常（5s 内同类错误仅记录一次）:")

    def _update_vr_status(self):
        if not hasattr(self, 'vr_marker_receiver'):
            return
        recv = getattr(self.vr_marker_receiver, 'is_connected', False)
        if not recv:
            self.gaze_trajectory_widget.break_trace()
        if recv:
            color = SUCCESS
            text = "已连接"
        else:
            color = ERROR
            text = "未连接"
        if not hasattr(self, '_last_vr_status') or self._last_vr_status != (color, text):
            self._last_vr_status = (color, text)
            self.vr_status_indicator.setStyleSheet(f"font: bold 16px; color: {color};")
            self.vr_status_label.setText(text)
            self.vr_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _update_eeg_status(self):
        """EEG 状态栏显示"已连接(1000Hz, 32ch, 0.01-30Hz)"。
        没连上时显示当前 UI 选择，方便用户核对。
        """
        if not hasattr(self, 'eeg_status_indicator'):
            return
        if not getattr(self, '_data_worker', None):
            return
        sdk = getattr(self._data_worker, '_sdk', None)
        connected = bool(sdk and getattr(sdk, '_initialized', False))

        # 探测 SDK 上的真实采样率 / 通道数（不同版本字段名可能不同）
        if connected:
            sr = (getattr(sdk, 'sample_rate', None)
                  or getattr(sdk, '_sample_rate', None)
                  or self.current_sampling_rate)
            ch = (getattr(sdk, 'channel_count', None)
                  or getattr(sdk, '_channel_count', None)
                  or 32)
            color = SUCCESS
            text = f"已连接 ({int(ch)}ch, {int(sr)}Hz)"
        else:
            color = ERROR
            text = ("模拟数据（非硬件）" if self.eegView.mock.enabled
                    else f"未连接 ({self.current_sampling_rate}Hz)")

        # 把当前 UI 选的滤波带挂到状态栏（一次性）
        key = ('eeg', color, text)
        if getattr(self, '_last_eeg_status', None) != key:
            self._last_eeg_status = key
            self.eeg_status_indicator.setStyleSheet(f"font: bold 16px; color: {color};")
            self.eeg_status_label.setText(text)
            self.eeg_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def closeEvent(self, event: QtGui.QCloseEvent):
        section("程序关闭")
        self._closing = True
        if self.erp_analyzer.preprocessor is not None:
            self.erp_analyzer.preprocessor.reset()
        self._stop_impedance_worker()
        self._data_worker.request_stop()
        if self._data_thread.isRunning():
            self._data_thread.quit()
            self._data_thread.wait(2000)
        if not self._data_thread.isRunning() and self._data_worker._sdk is not None:
            self._data_worker._sdk.close()
            self._data_worker._sdk = None
        if hasattr(self, 'vr_marker_receiver'):
            self.vr_marker_receiver.stop()
            log("[MainWindow] vr_marker_receiver 已停止")
        if hasattr(self, 'eego_trigger') and self.eego_trigger is not None:
            try:
                from eego_trigger import shutdown_trigger
                shutdown_trigger()
                log("[MainWindow] eego_trigger 已关闭")
            except Exception:
                log_exc("[MainWindow] shutdown_trigger 异常:")
        super().closeEvent(event)


if __name__ == "__main__":
    import sys
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
