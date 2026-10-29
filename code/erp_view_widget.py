"""
ERP (P300) 显示组件
显示实时计算的 ERP 波形
"""

from typing import Optional, List
import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from erp_analyzer import ERPAnalyzer
from biostyle import PLOT, PANEL, BORDER, MUTED, BLUE, ORANGE, ERROR, style_plot


class _CenteredAmplitudeAxis(pg.AxisItem):
    """纵轴加宽与EEG对齐后，幅值标题仍居中放在轴的留白区域。"""

    def resizeEvent(self, event=None):
        super().resizeEvent(event)
        if self.label is not None:
            center = self.label.mapRectToParent(self.label.boundingRect()).center().x()
            self.label.setX(self.label.x() + self.width() / 2 - center)


class ERPViewWidget(QtWidgets.QWidget):
    """
    ERP 波形显示组件
    
    显示：
    - Target ERP（蓝色）
    - Standard ERP（橙色）
    """
    
    def __init__(self, erp_analyzer: ERPAnalyzer, parent=None):
        super().__init__(parent)
        self.erp_analyzer = erp_analyzer
        
        # 显示配置
        self.selected_channel_idx: Optional[int] = None  # None 表示显示所有通道平均
        self.selected_group_idx: Optional[int] = None
        
        self._init_ui()
        
        # 定时更新
        self._update_timer = QtCore.QTimer(self)
        self._update_timer.timeout.connect(self._update_plot)
        self._update_timer.start(200)  # 每 200ms 更新一次
    
    def _init_ui(self) -> None:
        """初始化 UI"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # 标题和控制栏
        self.title_bar = title_bar = QtWidgets.QFrame(self)
        title_bar.setObjectName("erpHeader")
        title_layout = QtWidgets.QHBoxLayout(title_bar)
        title_layout.setContentsMargins(12, 8, 12, 8)
        
        title_label = QtWidgets.QLabel("P300 ERP 分析", self)
        title_label.setProperty("role", "title")
        title_layout.addWidget(title_label)
        
        # 通道选择（可选）
        ch_label = QtWidgets.QLabel("通道:", self)
        ch_label.setProperty("role", "dim")
        title_layout.addWidget(ch_label)
        self.combo_channel = QtWidgets.QComboBox(self)
        self.combo_channel.addItem("所有通道平均")
        self.combo_channel.currentIndexChanged.connect(self._on_channel_changed)
        title_layout.addWidget(self.combo_channel)
        
        # 试次统计
        self.trial_label = QtWidgets.QLabel("Target: 0, Standard: 0", self)
        self.trial_label.setProperty("role", "dim")
        self.trial_label.setStyleSheet("font-size: 10px;")
        title_layout.addWidget(self.trial_label)
        
        title_layout.addStretch(1)
        
        layout.addWidget(title_bar)
        
        # 绘图区域
        self.plot = pg.PlotWidget(background=PLOT,
                                  axisItems={'left': _CenteredAmplitudeAxis('left')})
        style_plot(self.plot)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel('bottom', '时间', units='ms', color=MUTED)
        self.plot.setLabel('left', '幅值', units='µV', color=MUTED)
        self.plot.getAxis('left').enableAutoSIPrefix(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        # 负的水平偏移使图例固定在绘图区右上角，随窗口缩放保持边距。
        legend = self.plot.addLegend(offset=(-12, 12), brush=PANEL, pen=BORDER,
                                     labelTextColor=MUTED, labelTextSize='8pt',
                                     horSpacing=24, verSpacing=-3)
        legend.layout.setContentsMargins(4, 3, 4, 3)
        
        # 创建曲线
        self.curve_target = self.plot.plot(
            pen=pg.mkPen(color=BLUE, width=2),
            name="Target"
        )
        self.curve_standard = self.plot.plot(
            pen=pg.mkPen(color=ORANGE, width=2),
            name="Standard"
        )
        
        # 添加零线
        self.zero_line = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(color=MUTED, style=QtCore.Qt.DashLine))
        self.plot.addItem(self.zero_line)
        
        # 添加刺激时刻标记线（刺激在 0ms，与 time_axis_ms 的 -pre_stimulus~+post_stimulus 区间一致）
        self.stimulus_line = pg.InfiniteLine(
            pos=0,
            angle=90,
            pen=pg.mkPen(color=ERROR, width=2),
        )
        self.plot.addItem(self.stimulus_line)
        
        layout.addWidget(self.plot)
    
    def set_channel_list(self, channel_names: List[str]) -> None:
        """设置通道列表"""
        if self.erp_analyzer.analysis_groups:
            # 下拉框使用采集名称与原列索引的映射；不以列表位置猜测导联编号。
            self.combo_channel.blockSignals(True)
            self.combo_channel.clear()
            for group in self.erp_analyzer.analysis_groups:
                self.combo_channel.addItem(group['label'])
            preferred = next((i for i,g in enumerate(self.erp_analyzer.analysis_groups)
                              if g['label'].casefold() == 'pz'),
                             next(i for i,g in enumerate(self.erp_analyzer.analysis_groups) if g['available']))
            self.combo_channel.setCurrentIndex(preferred)
            self.combo_channel.blockSignals(False)
            self._on_channel_changed(preferred)
            return
        self.combo_channel.clear()
        self.combo_channel.addItem("所有通道平均")
        for i, name in enumerate(channel_names):
            self.combo_channel.addItem(f"{i+1}. {name}")
        if self.erp_analyzer.preprocessor is not None:
            preferred = next((name for name in ('Pz','Cz') if name in channel_names), channel_names[0])
            self.combo_channel.setCurrentIndex(channel_names.index(preferred)+1)
    
    def _on_channel_changed(self, index: int) -> None:
        """通道选择改变"""
        if self.erp_analyzer.analysis_groups:
            if index < 0:
                return
            self.selected_group_idx = index
            group = self.erp_analyzer.analysis_groups[index]
            self.selected_channel_idx = group['indices'][0] if len(group['indices']) == 1 else None
            self._update_plot()
            return
        self.selected_group_idx = None
        if index == 0:
            self.selected_channel_idx = None  # 所有通道平均
        else:
            self.selected_channel_idx = index - 1
        self._update_plot()
    
    def _update_plot(self) -> None:
        """更新绘图"""
        # 绘图只读取已有ERP，不在切换通道或刷新时重新滤波、分段或剔除试次。
        # 获取 ERP 数据
        erp_data = self.erp_analyzer.get_erp_data(channel_idx=self.selected_channel_idx, group_idx=self.selected_group_idx)
        self.title_bar.setToolTip(erp_data['preprocessing_status'])
        if erp_data['unit'] == 'V':
            erp_data = dict(erp_data, **{key: erp_data[key]*1e6
                                       for key in ('target','standard') if key in erp_data})
        
        # 更新试次统计
        self.trial_label.setText(
            f"Target: {erp_data['n_target']}, Standard: {erp_data['n_standard']}  "
            f"等待: {erp_data['n_pending']}, 未纳入: {erp_data['n_rejected']}"
        )
        
        # 更新曲线
        time_ms = erp_data['time']
        
        if 'target' in erp_data:
            self.curve_target.setData(time_ms, erp_data['target'])
        else:
            self.curve_target.setData([], [])
        
        if 'standard' in erp_data:
            self.curve_standard.setData(time_ms, erp_data['standard'])
        else:
            self.curve_standard.setData([], [])
        
        # 自动调整 Y 轴范围
        if 'target' in erp_data or 'standard' in erp_data:
            all_data = []
            if 'target' in erp_data:
                all_data.extend(erp_data['target'])
            if 'standard' in erp_data:
                all_data.extend(erp_data['standard'])
            
            if all_data:
                y_min = np.min(all_data)
                y_max = np.max(all_data)
                margin = (y_max - y_min) * 0.1
                self.plot.setYRange(y_min - margin, y_max + margin)
        
        # 设置 X 轴范围
        if len(time_ms) > 0:
            self.plot.setXRange(time_ms[0], time_ms[-1])
    
    def reset(self) -> None:
        """重置显示"""
        self.curve_target.setData([], [])
        self.curve_standard.setData([], [])
        self.trial_label.setText("Target: 0, Standard: 0")
