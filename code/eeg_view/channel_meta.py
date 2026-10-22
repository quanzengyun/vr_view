"""
EEGView 内部子模块：通道元数据（颜色 / 区域 / 默认增益 / 标签绘制委托）。

这一块原本在 eeg_view_widget.py 顶部（L1-188），是纯数据 + 一个 QStyledItemDelegate，
与主 widget 主体耦合度低，单独抽出后主 widget 仅需 from .channel_meta import ... 即可。
"""

from typing import List

from PyQt5 import QtCore, QtGui, QtWidgets


# ============================================================================
# 标准 32 导联（ANT Neuro CNT 文件常用配置）
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


def region_of(name: str) -> str:
    """根据通道名判断其所属脑区。"""
    upper = name.upper()
    if upper in {"VREF", "REF", "GND", "A1", "A2", "M1", "M2"}:
        return "ref"
    # 枕叶
    if upper in {"O1", "O2", "OZ", "POZ"}:
        return "occipital"
    # 顶叶
    if upper in {"P3", "P4", "PZ", "P7", "P8"}:
        return "parietal"
    # 颞叶
    if upper in {"T7", "T8"}:
        return "temporal"
    # 中央区 / 中央沟
    if upper in {"C3", "C4", "CZ", "CP1", "CP2", "CP5", "CP6", "FC1", "FC2", "FC5", "FC6"}:
        return "central"
    # 额叶（含 Fp、Fz、F3/F4/F7/F8）
    if upper.startswith("F") or upper == "FZ":
        return "frontal"
    return "other"


# 区域中文名映射（用于标签显示）
_REGION_CHINESE: dict = {
    "frontal":   "额叶",
    "central":   "中央",
    "temporal":  "颞叶",
    "parietal":  "顶叶",
    "occipital": "枕叶",
    "ref":       "参考",
    "other":     "其他",
}

# (波形色, 标签 chip 色, 区域背景带颜色, 区域缩写)
_REGION_PALETTE: dict = {
    "frontal":   (QtGui.QColor(255, 138, 92),   QtGui.QColor(255, 138, 92),  QtGui.QColor(40, 26, 20),  "FRONT"),
    "central":   (QtGui.QColor(91, 240, 214),   QtGui.QColor(91, 240, 214),  QtGui.QColor(18, 36, 35),  "CENT"),
    "parietal":  (QtGui.QColor(122, 184, 255),  QtGui.QColor(122, 184, 255), QtGui.QColor(20, 30, 48),  "PARI"),
    "occipital": (QtGui.QColor(166, 108, 255),  QtGui.QColor(166, 108, 255), QtGui.QColor(32, 22, 48),  "OCCI"),
    "temporal":  (QtGui.QColor(255, 105, 180),  QtGui.QColor(255, 105, 180), QtGui.QColor(40, 22, 32),  "TEMP"),
    "ref":       (QtGui.QColor(140, 151, 180),  QtGui.QColor(80, 90, 110),   QtGui.QColor(24, 28, 36),  "REF"),
    "other":     (QtGui.QColor(200, 200, 200),  QtGui.QColor(200, 200, 200), QtGui.QColor(28, 28, 28),  "..."),
}

# 区域绘制顺序（标签列从上到下排列区域，方便视觉定位）
REGION_ORDER: List[str] = [
    "frontal", "central", "temporal", "parietal", "occipital", "ref",
]


def get_channel_color(name: str) -> QtGui.QColor:
    """按通道所属脑区返回波形颜色。"""
    return QtGui.QColor(_REGION_PALETTE[region_of(name)][0])


def get_region_band(name: str) -> QtGui.QColor:
    """返回该通道所属脑区的背景带颜色（透明叠加用）。"""
    return QtGui.QColor(_REGION_PALETTE[region_of(name)][2])


def get_chip_color(name: str) -> QtGui.QColor:
    """返回该通道在标签列表中左侧颜色条的色。"""
    return QtGui.QColor(_REGION_PALETTE[region_of(name)][1])


def get_region_label(name: str) -> str:
    """返回该通道所属脑区的缩写。"""
    return _REGION_PALETTE[region_of(name)][3]


def get_default_gain(name: str) -> float:
    """EEG 通道默认增益。
    配合归一化 offset_step=1/32 和 display_scale，gain=0.3 时：
    100µV 信号占通道的 ~30%，默认即可见清晰。
    """
    upper = name.upper()
    # 参考通道（参考电极如 M1/M2/VREF/REF/GND）不绘制，给 0 增益避免拉满
    if upper in {"M1", "M2", "VREF", "REF", "GND", "A1", "A2"}:
        return 0.0
    # 默认 EEG 通道：100µV → 30 绘图单位
    return 0.3


# ============================================================================
# 通道标签绘制代理：在 QListWidget 每一行最左侧画一个色条（chip），
# 中间画通道名（粗体），右侧画脑区缩写（半透明小字）。
# ============================================================================
class ChannelItemDelegate(QtWidgets.QStyledItemDelegate):
    """自定义 QListWidget 标签绘制：左色条 + 通道名 + 区域缩写。"""

    CHIP_WIDTH = 4   # 色条宽度（像素）

    def paint(self, painter: QtGui.QPainter, option, index) -> None:  # type: ignore[override]
        painter.save()
        try:
            rect = option.rect
            name = str(index.data(QtCore.Qt.UserRole) or index.data(QtCore.Qt.DisplayRole) or "")

            # 背景
            if option.state & QtWidgets.QStyle.State_Selected:
                bg = QtGui.QColor(79, 123, 255, 56)   # 选中：RGBA 半透明蓝
            elif option.state & QtWidgets.QStyle.State_MouseOver:
                bg = QtGui.QColor(79, 123, 255, 28)
            else:
                bg = QtGui.QColor(0, 0, 0, 0)          # 透明（由外层 QSS 填充）
            painter.fillRect(rect, bg)

            # 左侧色条（按脑区颜色）
            chip_color = get_chip_color(name)
            # 选中态 → 更亮
            if option.state & QtWidgets.QStyle.State_Selected:
                chip_color = QtGui.QColor(chip_color)
                chip_color.setAlpha(255)
                # 加亮
                chip_color = chip_color.lighter(120)
            painter.fillRect(
                rect.x(), rect.y(),
                self.CHIP_WIDTH, rect.height(),
                chip_color,
            )

            # 通道名（粗体 + 偏白）
            text_color = QtGui.QColor("#FFFFFF" if option.state & QtWidgets.QStyle.State_Selected else "#E6ECF7")
            region_label = get_region_label(name)
            region_color = QtGui.QColor(_REGION_PALETTE[region_of(name)][1])
            region_color.setAlpha(180 if not (option.state & QtWidgets.QStyle.State_Selected) else 240)

            text_x = rect.x() + self.CHIP_WIDTH + 6
            text_w = rect.width() - self.CHIP_WIDTH - 8

            font_name = QtGui.QFont("Consolas", 9)
            font_name.setBold(True)
            painter.setFont(font_name)
            painter.setPen(text_color)

            # 区域缩写占用右侧 36px
            region_w = 36
            name_rect = QtCore.QRect(
                text_x, rect.y(),
                max(0, text_w - region_w), rect.height()
            )
            painter.drawText(name_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, name)

            # 区域缩写（右对齐、半透明）
            font_region = QtGui.QFont("Consolas", 8)
            painter.setFont(font_region)
            painter.setPen(region_color)
            region_rect = QtCore.QRect(
                text_x + name_rect.width(), rect.y(),
                region_w, rect.height()
            )
            painter.drawText(region_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, region_label)

        finally:
            painter.restore()

    def sizeHint(self, option, index) -> QtCore.QSize:  # type: ignore[override]
        size = super().sizeHint(option, index)
        # 最小高度，避免名字被截断
        size.setHeight(max(size.height(), 16))
        return size
