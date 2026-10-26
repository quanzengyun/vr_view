"""
ERP (P300) 显示组件
显示实时计算的 ERP 波形
"""

from typing import Optional, List
import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from erp_analyzer import ERPAnalyzer


class ERPViewWidget(QtWidgets.QWidget):
    """
    ERP 波形显示组件
    
    显示：
    - Target ERP（蓝色）
    - Standard ERP（橙色）
    - P300 (target - standard)（可选）
    """
    
    def __init__(self, erp_analyzer: ERPAnalyzer, parent=None):
        super().__init__(parent)
        self.erp_analyzer = erp_analyzer
        
        # 显示配置
        self.show_p300 = True  # 是否显示 P300 差异波
        self.selected_channel_idx: Optional[int] = None  # None 表示显示所有通道平均
        self.selected_group_idx: Optional[int] = None
        
        self._init_ui()
        
        # 定时更新
        self._update_timer = QtCore.QTimer(self)
        self._update_timer.timeout.connect(self._update_plot)
        self._update_timer.start(200)  # 每 200ms 更新一次
    
    def _init_ui(self) -> None:
        """初始化 UI"""
        self.setStyleSheet("background-color: #E8EDF5;")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # 标题和控制栏
        title_bar = QtWidgets.QFrame(self)
        title_bar.setStyleSheet("background-color: #FFFFFF; border: 1px solid #C8D5E8; border-radius: 6px;")
        title_layout = QtWidgets.QHBoxLayout(title_bar)
        title_layout.setContentsMargins(12, 8, 12, 8)
        
        title_label = QtWidgets.QLabel("P300 ERP 分析", self)
        title_label.setStyleSheet("font: bold 13px; color: #2D3748; background: transparent;")
        title_layout.addWidget(title_label)
        
        # 通道选择（可选）
        ch_label = QtWidgets.QLabel("通道:", self)
        ch_label.setStyleSheet("color: #8896A8; background: transparent;")
        title_layout.addWidget(ch_label)
        self.combo_channel = QtWidgets.QComboBox(self)
        self.combo_channel.addItem("所有通道平均")
        self.combo_channel.setStyleSheet("""
            QComboBox { background-color: #F0F4F8; border: 1px solid #C8D5E8;
                        border-radius: 6px; padding: 4px 8px; color: #2D3748; }
            QComboBox:hover { border-color: #5B9BD5; }
            QComboBox::drop-down { border: none; }
        """)
        self.combo_channel.currentIndexChanged.connect(self._on_channel_changed)
        title_layout.addWidget(self.combo_channel)
        
        # 显示 P300 复选框
        self.checkbox_p300 = QtWidgets.QCheckBox("显示 P300", self)
        self.checkbox_p300.setChecked(True)
        self.checkbox_p300.setStyleSheet("""
            QCheckBox { color: #2D3748; background: transparent; }
            QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px;
                                   border: 1px solid #C8D5E8; background: #F0F4F8; }
            QCheckBox::indicator:checked { background: #4ECDC4; border-color: #4ECDC4; }
        """)
        self.checkbox_p300.toggled.connect(self._on_p300_toggled)
        title_layout.addWidget(self.checkbox_p300)
        
        # 试次统计
        self.trial_label = QtWidgets.QLabel("Target: 0, Standard: 0", self)
        self.trial_label.setStyleSheet("color: #8896A8; font: 10px; background: transparent;")
        title_layout.addWidget(self.trial_label)
        
        title_layout.addStretch(1)
        
        layout.addWidget(title_bar)
        self.preprocessing_label = QtWidgets.QLabel(self)
        self.preprocessing_label.setWordWrap(True)
        layout.addWidget(self.preprocessing_label)
        
        # 绘图区域
        self.plot = pg.PlotWidget(background="#F0F4F8")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel('bottom', '时间 (ms)', units='ms', color='#5B9BD5')
        self.plot.setLabel('left', '幅值 (µV)', units='µV', color='#5B9BD5')
        self.plot.setMenuEnabled(False)
        
        # 创建曲线
        self.curve_target = self.plot.plot(
            pen=pg.mkPen(color=(55, 120, 196), width=2),
            name="Target"
        )
        self.curve_standard = self.plot.plot(
            pen=pg.mkPen(color=(232, 130, 60), width=2),
            name="Standard"
        )
        self.curve_p300 = self.plot.plot(
            pen=pg.mkPen(color=(180, 60, 60), width=2, style=QtCore.Qt.DashLine),
            name="P300"
        )
        
        # 添加图例
        self.plot.addLegend()
        
        # 添加零线
        self.zero_line = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(color='gray', style=QtCore.Qt.DashLine))
        self.plot.addItem(self.zero_line)
        
        # 添加刺激时刻标记线（刺激在 0ms，与 time_axis_ms 的 -pre_stimulus~+post_stimulus 区间一致）
        self.stimulus_line = pg.InfiniteLine(
            pos=0,
            angle=90,
            pen=pg.mkPen(color='red', width=2),
        )
        self.plot.addItem(self.stimulus_line)
        
        layout.addWidget(self.plot)
    
    def set_channel_list(self, channel_names: List[str]) -> None:
        """设置通道列表"""
        if self.erp_analyzer.analysis_groups:
            self.combo_channel.blockSignals(True)
            self.combo_channel.clear()
            for group in self.erp_analyzer.analysis_groups:
                self.combo_channel.addItem(group['label'])
            self.combo_channel.setCurrentIndex(2)  # 默认Pz，四个选项按约定顺序排列。
            self.combo_channel.blockSignals(False)
            self._on_channel_changed(2)
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
    
    def _on_p300_toggled(self, checked: bool) -> None:
        """P300 显示切换"""
        self.show_p300 = checked
        self.curve_p300.setVisible(checked)
        self._update_plot()
    
    def _update_plot(self) -> None:
        """更新绘图"""
        # 获取 ERP 数据
        erp_data = self.erp_analyzer.get_erp_data(channel_idx=self.selected_channel_idx, group_idx=self.selected_group_idx)
        reasons = {'amplitude_artifact':'振幅超限', 'variance_artifact':'方差超限',
                   'incomplete_post_stimulus_at_stop':'停止时后窗不足',
                   'eye_model_unavailable':'眼电模型不可用'}
        counts = self.erp_analyzer.rejection_counts
        details = '；'.join(f'{label}: {counts[reason]}' for reason,label in reasons.items() if counts[reason])
        self.preprocessing_label.setText(erp_data['preprocessing_status'] + ('；'+details if details else ''))
        if erp_data['unit'] == 'V':
            erp_data = dict(erp_data, **{key: erp_data[key]*1e6
                                       for key in ('target','standard','p300') if key in erp_data})
        
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
        
        if 'p300' in erp_data and self.show_p300:
            self.curve_p300.setData(time_ms, erp_data['p300'])
        else:
            self.curve_p300.setData([], [])
        
        # 自动调整 Y 轴范围
        if 'target' in erp_data or 'standard' in erp_data:
            all_data = []
            if 'target' in erp_data:
                all_data.extend(erp_data['target'])
            if 'standard' in erp_data:
                all_data.extend(erp_data['standard'])
            if 'p300' in erp_data and self.show_p300:
                all_data.extend(erp_data['p300'])
            
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
        self.curve_p300.setData([], [])
        self.trial_label.setText("Target: 0, Standard: 0")
