"""
Impedance monitoring widget — 64 电极 10-20/10-10 国际标准导联系统俯视图。

设计要点（参考旧 C++ 版 Impedance3）：
- 头部轮廓椭圆（深色背景）
- 每个电极 = 圆形，按通道号 1..N 命名
- 颜色编码：
    灰色 = 未连接（>200 kΩ 或无数据）
    绿色 = 良好（<cutoff）
    黄色 = 边界（cutoff ~ cutoff*1.5）
    红色 = 过高（≥cutoff*1.5）
- 顶部工具栏：阈值滑块、当前阈值显示、SDK 状态
- 底部信息栏：良好率、最大阻抗通道

实现：使用 QGraphicsScene/View 把头部轮廓和电极都画在同一个场景里，
避免 QWidget 子树嵌套过深导致的事件循环问题。
"""

from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets
from biostyle import (apply_theme, PLOT as _BG_DEEP,
                      PANEL as _BG_HEAD, BORDER as _BORDER, TEXT as _TEXT_MAIN,
                      MUTED as _TEXT_DIM, BLUE as _SELECT, SUCCESS, ORANGE, ERROR)


# ============================================================================
# 64 电极 10-20/10-10 标准布局（归一化坐标，俯视）
#
# 坐标来源：旧 C++ Builder 项目 Impedance3.dfm 中 64 个电极 TShape 的
# 实际 Left/Top 值。DFM 头部椭圆 Shape1: Left=117, Top=56, Width=824, Height=601
#   x_norm = (px - 117) / 824
#   y_norm = (py - 56)  / 601
#
# 通道顺序与 eego SDK 默认输出一致（EEG IN 1 的 32 通道 10-20 系统 +
# EEG IN 2 的 32 通道 10-10 扩展）。原始 C++ Impedance3.cpp:
#   for gm_j in 0..buf._channel_count:
#       ShowElectrode(gm_j+1, color, buf._data[gm_j]/1000)
# 即 SDK 输出位置 j → 通道名 DFM_CHANNEL_NAMES[j+1]。
# ============================================================================

_DFM_HEAD_LEFT = 117
_DFM_HEAD_TOP  = 56
_DFM_HEAD_W    = 824
_DFM_HEAD_H    = 601

_DFM_POSITIONS: Dict[int, Tuple[int, int]] = {
    1:  (445, 89),   2:  (515, 81),   3:  (589, 89),
    4:  (283, 203),  5:  (397, 240),  6:  (515, 256),  7:  (629, 240),  8:  (739, 203),
    9:  (301, 302),  10: (443, 318),  11: (589, 322),  12: (731, 310),
    13: (165, 472),  14: (203, 376),  15: (373, 377),  16: (515, 377),  17: (667, 377),
    18: (819, 376),  19: (847, 473),
    20: (315, 457),  21: (467, 441),  22: (584, 441),  23: (715, 457),
    24: (307, 561),  25: (403, 521),  26: (515, 513),  27: (619, 521),  28: (715, 561),
    29: (515, 577),  30: (467, 633),  31: (563, 633),
    32: (231, 40),   # EOG — 左太阳穴
    33: (347, 145),  34: (443, 169),  35: (595, 169),  36: (683, 145),
    37: (339, 225),  38: (451, 249),  39: (571, 249),  40: (683, 225),
    41: (371, 313),  42: (515, 321),  43: (659, 318),
    44: (283, 377),  45: (451, 377),  46: (587, 377),  47: (747, 377),
    48: (395, 449),  49: (643, 449),
    50: (347, 537),  51: (467, 513),  52: (563, 513),  53: (675, 537),
    54: (411, 585),  55: (459, 577),  56: (571, 577),  57: (611, 585),
    58: (219, 289),  59: (803, 289),
    60: (243, 473),  61: (779, 473),
    62: (371, 601),  63: (651, 601),
    64: (515, 633),
    65: (516, 438),  # CPz — 参考点（不在 64 路 EEG 内）
    66: (511, 172),  # GND — 头顶
}

# 电极号 → 通道名（按 eego SDK 默认命名约定）
_DFM_CHANNEL_NAMES: Dict[int, str] = {
    1: "Fp1", 2: "Fpz", 3: "Fp2",
    4: "F7",  5: "F3",  6: "Fz",  7: "F4",  8: "F8",
    9: "FC5", 10: "FC1", 11: "FC2", 12: "FC6",
    13: "M1", 14: "T7", 15: "C3", 16: "Cz", 17: "C4", 18: "T8", 19: "M2",
    20: "CP5", 21: "CP1", 22: "CP2", 23: "CP6",
    24: "P7", 25: "P3", 26: "Pz", 27: "P4", 28: "P8",
    29: "POz", 30: "O1", 31: "O2",
    32: "EOG",
    33: "AF7", 34: "AF3", 35: "AF4", 36: "AF8",
    37: "F5",  38: "F1",  39: "F2",  40: "F6",
    41: "FC3", 42: "FCz", 43: "FC4",
    44: "C5",  45: "C1",  46: "C2",  47: "C6",
    48: "CP3", 49: "CP4",
    50: "P5",  51: "P1",  52: "P2",  53: "P6",
    54: "PO3", 55: "PO5", 56: "PO6", 57: "PO4",
    58: "FT7", 59: "FT8",
    60: "TP7", 61: "TP8",
    62: "PO7", 63: "PO8",
    64: "Oz",
    65: "CPz",
    66: "GND",
}


def _dfm_to_norm(px: int, py: int) -> Tuple[float, float]:
    """DFM 像素坐标 → 归一化坐标 [0,1]."""
    return (
        (px - _DFM_HEAD_LEFT) / _DFM_HEAD_W,
        (py - _DFM_HEAD_TOP)  / _DFM_HEAD_H,
    )


# ============================================================================
# 导出：通道名 → 归一化坐标（直接给绘图用）
# ============================================================================

ELECTRODE_POSITIONS_64: Dict[str, Tuple[float, float]] = {
    _DFM_CHANNEL_NAMES[k]: _dfm_to_norm(*_DFM_POSITIONS[k])
    for k in range(1, 67)  # 64 路 EEG + EOG + CPz + GND
}

# 按 SDK stream 位置 (j, 0..63) 顺序排列的电极名
# 旧版 C++ Impedance3.cpp 中 stream position j 对应 DFM 电极号 j+1
ELECTRODES_BY_STREAM_POS: List[str] = [
    _DFM_CHANNEL_NAMES[j + 1] for j in range(64)
]
# 兼容旧名称
ELECTRODES_64_ORDERED = ELECTRODES_BY_STREAM_POS

# 业务用 32 导联（按 SDK 标准 10-20 顺序，与 data_worker.STANDARD_32_CHANNELS 一致）
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
# 颜色定义（与主窗口一致）
# ============================================================================


COLOR_DISCONNECT = QtGui.QColor(120, 130, 150)
COLOR_GOOD       = QtGui.QColor(80, 220, 120)
COLOR_WARN       = QtGui.QColor(255, 200, 70)
COLOR_BAD        = QtGui.QColor(255, 80, 90)


# ============================================================================
# 电极 graphics item（圆形 + 标签）
# ============================================================================

class _ElectrodeItem(QtWidgets.QGraphicsItem):
    """一个圆形电极 + 通道名标签。"""

    def __init__(self, name: str, nx: float, ny: float,
                 radius_px: float = 7.0,
                 label_offset: float = 11.0):
        super().__init__()
        self.name = name
        self._nx = nx
        self._ny = ny
        self._r = radius_px
        self._label_off = label_offset
        self._imp_kohm: float = -1.0
        self._cutoff: float = 20.0
        self._highlight = name in STANDARD_32_CHANNELS
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._make_tooltip())

    def boundingRect(self) -> QtCore.QRectF:
        margin = max(self._r, self._label_off) + 30
        return QtCore.QRectF(-margin, -margin, margin * 2, margin * 2)

    def set_impedance(self, kohm: float, cutoff: float) -> None:
        self._imp_kohm = float(kohm)
        self._cutoff = float(cutoff)
        self.setToolTip(self._make_tooltip())
        self.update()

    def _make_tooltip(self) -> str:
        if self._imp_kohm < 0:
            return f"<b>{self.name}</b><br>未连接 / 无数据"
        if self._imp_kohm > 200:
            return f"<b>{self.name}</b><br>阻抗: Inf（>200 kΩ）"
        return f"<b>{self.name}</b><br>阻抗: {self._imp_kohm:.1f} kΩ"

    def _decide_color(self) -> QtGui.QColor:
        if self._imp_kohm < 0 or self._imp_kohm > 200:
            return COLOR_DISCONNECT
        if self._imp_kohm < self._cutoff:
            return COLOR_GOOD
        if self._imp_kohm < self._cutoff * 1.5:
            return COLOR_WARN
        return COLOR_BAD

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        color = self._decide_color()
        if self._highlight:
            pen = QtGui.QPen(QtGui.QColor(_SELECT), 1.4)
        else:
            pen = QtGui.QPen(QtGui.QColor(_BORDER), 1)
        painter.setPen(pen)
        painter.setBrush(color)
        painter.drawEllipse(QtCore.QPointF(0, 0), self._r, self._r)
        font = QtGui.QFont("Consolas", 7)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(_TEXT_MAIN))
        text_rect = QtCore.QRectF(
            -22, self._label_off - 4, 44, 12
        )
        painter.drawText(text_rect, QtCore.Qt.AlignCenter, self.name)


# ============================================================================
# 头部轮廓 graphics item
# ============================================================================

class _HeadOutlineItem(QtWidgets.QGraphicsItem):
    """头部俯视轮廓 + 鼻 + 耳。"""

    def __init__(self, scene_w: float, scene_h: float):
        super().__init__()
        self._w = scene_w
        self._h = scene_h
        self.setZValue(-10)

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(0, 0, self._w, self._h)

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        if self._w < 10 or self._h < 10:
            return
        head_rect = QtCore.QRectF(
            self._w * 0.10, self._h * 0.04,
            self._w * 0.80, self._h * 0.96,
        )
        painter.setPen(QtGui.QPen(QtGui.QColor(_SELECT), 3))
        painter.setBrush(QtGui.QColor(_BG_HEAD))
        painter.drawEllipse(head_rect)
        # 中心十字线
        painter.setPen(QtGui.QPen(QtGui.QColor(_BORDER), 1, QtCore.Qt.DashLine))
        painter.drawLine(
            int(head_rect.center().x()), int(head_rect.top()),
            int(head_rect.center().x()), int(head_rect.bottom()),
        )
        painter.drawLine(
            int(head_rect.left()), int(head_rect.center().y()),
            int(head_rect.right()), int(head_rect.center().y()),
        )
        # 鼻子（俯视图，鼻尖朝向画布上方，与 forehead 同侧，指向画外）
        cx = head_rect.center().x()
        painter.setPen(QtGui.QPen(QtGui.QColor(_SELECT), 2))
        painter.setBrush(QtGui.QColor(_BG_HEAD))
        nose = QtGui.QPolygonF([
            QtCore.QPointF(cx - 18, head_rect.top() + 2),
            QtCore.QPointF(cx + 18, head_rect.top() + 2),
            QtCore.QPointF(cx, head_rect.top() - 26),
        ])
        painter.drawPolygon(nose)
        # 左右耳
        for side in (-1, 1):
            ex = cx + side * (head_rect.width() / 2 + 2)
            cy = head_rect.center().y()
            ear = QtGui.QPolygonF([
                QtCore.QPointF(ex, cy - 14),
                QtCore.QPointF(ex, cy + 14),
                QtCore.QPointF(ex + side * 22, cy),
            ])
            painter.drawPolygon(ear)


# ============================================================================
# 主 widget
# ============================================================================

class ImpedanceWidget(QtWidgets.QDialog):
    """
    64 电极阻抗检查界面（独立弹窗）。

    使用 QGraphicsScene/View 在一个固定虚拟坐标空间（824×657，跟原 DFM
    头部椭圆 1:1）里画头图，所有电极位置按比例映射到 view。

    作为独立窗口使用时：
        dlg = ImpedanceWidget(self)        # parent = MainWindow
        dlg.show()
    """

    cutoffChanged = QtCore.pyqtSignal(float)

    # 场景虚拟大小 = DFM 头部椭圆尺寸（1:1 还原）
    SCENE_W = 824
    SCENE_H = 657

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("脑电帽阻抗检测 — Impedance Check")
        self.setMinimumSize(760, 900)
        self.resize(880, 980)
        self.cutoff_kohm: float = 20.0
        self._is_running: bool = False
        self._impedance_values: Dict[str, float] = {}
        self._electrode_items: Dict[str, _ElectrodeItem] = {}
        self._init_ui()

    # ---------------------- UI 初始化 ----------------------

    def _init_ui(self) -> None:
        apply_theme(self)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ===== 顶部工具栏 =====
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(12)

        title_label = QtWidgets.QLabel("脑电帽阻抗检测")
        title_font = QtGui.QFont("Microsoft YaHei", 12, QtGui.QFont.Bold)
        title_label.setFont(title_font)
        toolbar.addWidget(title_label)

        toolbar.addStretch()

        cutoff_label = QtWidgets.QLabel("阈值 (kΩ):")
        toolbar.addWidget(cutoff_label)

        self.slider_cutoff = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_cutoff.setMinimum(5)
        self.slider_cutoff.setMaximum(100)
        self.slider_cutoff.setValue(int(self.cutoff_kohm))
        self.slider_cutoff.setFixedWidth(220)
        self.slider_cutoff.valueChanged.connect(self._on_cutoff_changed)
        toolbar.addWidget(self.slider_cutoff)

        self.label_cutoff_value = QtWidgets.QLabel(f"{self.cutoff_kohm:.0f} kΩ")
        cutoff_font = QtGui.QFont("Consolas", 11, QtGui.QFont.Bold)
        self.label_cutoff_value.setFont(cutoff_font)
        self.label_cutoff_value.setFixedWidth(70)
        toolbar.addWidget(self.label_cutoff_value)

        # SDK 状态
        self.label_sdk_state = QtWidgets.QLabel("●  未连接")
        self.label_sdk_state.setStyleSheet(f"color: {_TEXT_DIM};")
        toolbar.addWidget(self.label_sdk_state)

        root.addLayout(toolbar)

        # ===== 中部：QGraphicsView =====
        self.scene = QtWidgets.QGraphicsScene(0, 0, self.SCENE_W, self.SCENE_H, self)
        self.scene.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(_BG_DEEP)))

        # 头部轮廓
        self.head_outline_item = _HeadOutlineItem(self.SCENE_W, self.SCENE_H)
        self.scene.addItem(self.head_outline_item)

        # 电极
        for name in ELECTRODES_64_ORDERED:
            nx, ny = ELECTRODE_POSITIONS_64[name]
            item = _ElectrodeItem(name, nx, ny, radius_px=7.5)
            item.setPos(nx * self.SCENE_W, ny * self.SCENE_H)
            self.scene.addItem(item)
            self._electrode_items[name] = item

        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setRenderHints(
            QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing
        )
        self.view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.view.setStyleSheet(
            f"QGraphicsView {{ background-color: {_BG_DEEP};"
            f" border: 1px solid {_BORDER}; }}"
        )
        self.view.setMinimumHeight(540)
        root.addWidget(self.view, 1)

        # ===== 底部信息栏 =====
        info_bar = QtWidgets.QHBoxLayout()

        self.label_pass_rate = QtWidgets.QLabel("良好率: --")
        self.label_pass_rate.setStyleSheet("font-weight: bold;")
        info_bar.addWidget(self.label_pass_rate)

        info_bar.addSpacing(20)

        self.label_max_imp = QtWidgets.QLabel("最高阻抗: --")
        info_bar.addWidget(self.label_max_imp)

        info_bar.addSpacing(20)

        self.label_total = QtWidgets.QLabel("总通道: --")
        info_bar.addWidget(self.label_total)

        info_bar.addStretch()

        legend_layout = QtWidgets.QHBoxLayout()
        legend_layout.setSpacing(8)
        for color, text in [
            (COLOR_GOOD,       "良好 (<阈值)"),
            (COLOR_WARN,       "边界 (阈值~1.5x)"),
            (COLOR_BAD,        "过高 (≥1.5x阈值)"),
            (COLOR_DISCONNECT, "未连接 / Inf"),
        ]:
            chip = QtWidgets.QLabel()
            chip.setFixedSize(14, 14)
            chip.setStyleSheet(
                f"background-color: {color.name()}; border-radius: 7px;"
                f" border: 1px solid {_BORDER};"
            )
            label = QtWidgets.QLabel(text)
            label.setStyleSheet(f"color: {_TEXT_DIM};")
            legend_layout.addWidget(chip)
            legend_layout.addWidget(label)
            legend_layout.addSpacing(8)
        info_bar.addLayout(legend_layout)

        root.addLayout(info_bar)

        # ===== 底部按钮行 =====
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)

        hint = QtWidgets.QLabel(
            "提示：所有电极良好（绿色）后，关闭此窗口即可开始正式采集。"
        )
        hint.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px;")
        btn_row.addWidget(hint)

        btn_row.addStretch()

        self.btn_minimize = QtWidgets.QPushButton("最小化")
        self.btn_minimize.setFixedWidth(90)
        self.btn_minimize.clicked.connect(self.showMinimized)
        btn_row.addWidget(self.btn_minimize)

        self.btn_close = QtWidgets.QPushButton("关闭")
        self.btn_close.setFixedWidth(90)
        self.btn_close.setDefault(True)
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close)

        root.addLayout(btn_row)

    # ---------------------- 数据更新 ----------------------

    def update_impedance(self, name_to_kohm: Dict[str, float]) -> None:
        """
        批量更新电极阻抗值。
        name_to_kohm: {channel_name: kΩ}, kΩ=-1 表示未连接/Inf
        """
        for name, kohm in name_to_kohm.items():
            if name in self._electrode_items:
                self._electrode_items[name].set_impedance(kohm, self.cutoff_kohm)
                self._impedance_values[name] = kohm
        self._update_info_bar()

    def set_all_disconnected(self) -> None:
        for name, item in self._electrode_items.items():
            item.set_impedance(-1.0, self.cutoff_kohm)
            self._impedance_values[name] = -1.0
        self._update_info_bar()

    def set_sdk_connected(self, connected: bool, channel_count: int = 0) -> None:
        if connected:
            self.label_sdk_state.setText(f"●  已连接 ({channel_count} 通道)")
            self.label_sdk_state.setStyleSheet(f"color: {SUCCESS};")
        else:
            self.label_sdk_state.setText("●  未连接")
            self.label_sdk_state.setStyleSheet(f"color: {_TEXT_DIM};")
            self.set_all_disconnected()

    # ---------------------- 内部回调 ----------------------

    def _on_cutoff_changed(self, value: int) -> None:
        self.cutoff_kohm = float(value)
        self.label_cutoff_value.setText(f"{self.cutoff_kohm:.0f} kΩ")
        for item in self._electrode_items.values():
            item.set_impedance(item._imp_kohm, self.cutoff_kohm)
        self.cutoffChanged.emit(self.cutoff_kohm)
        self._update_info_bar()

    def _update_info_bar(self) -> None:
        vals = [v for v in self._impedance_values.values() if v >= 0]
        if not vals:
            self.label_pass_rate.setText("良好率: --")
            self.label_max_imp.setText("最高阻抗: --")
            self.label_total.setText(f"总通道: {len(self._impedance_values)}")
            return
        good = sum(1 for v in vals if v < self.cutoff_kohm)
        rate = 100.0 * good / len(vals)
        max_v = max(vals)
        max_name = next(
            (n for n, v in self._impedance_values.items() if v == max_v), "?"
        )
        self.label_pass_rate.setText(f"良好率: {good}/{len(vals)} = {rate:.0f}%")
        self.label_max_imp.setText(f"最高阻抗: {max_name} {max_v:.1f} kΩ")
        self.label_total.setText(f"总通道: {len(self._impedance_values)}")
        if rate >= 90:
            color = SUCCESS
        elif rate >= 70:
            color = ORANGE
        else:
            color = ERROR
        self.label_pass_rate.setStyleSheet(f"color: {color}; font-weight: bold;")


# ============================================================================
# 简单自测（独立运行）
# ============================================================================

if __name__ == "__main__":
    import sys
    import random
    app = QtWidgets.QApplication(sys.argv)
    w = ImpedanceWidget()
    w.show()
    w.set_sdk_connected(True, 64)
    timer = QtCore.QTimer()
    timer.timeout.connect(lambda: w.update_impedance({
        n: random.uniform(-1, 80) for n in ELECTRODES_64_ORDERED
    }))
    timer.start(200)
    sys.exit(app.exec_())
