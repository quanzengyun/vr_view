"""
生物气息美化样式（清爽浅色主题）。

集中所有 QSS + EEG 心律呼吸动画驱动。
保留波形界面（EEGView/ERPView）的深色专业风格，
其他界面调整为温和浅灰 + 薄荷蓝。
"""

import math as _math
from PyQt5 import QtCore, QtGui, QtWidgets


# ============================================================================
# 配色：清爽浅色主题
# ============================================================================
# 主背景：柔和浅灰（不刺眼）
COLOR_BG_DARK = "#F0F4F8"           # 整体背景
COLOR_BG_PANEL = "#FFFFFF"          # 面板/卡片：纯白（干净）
COLOR_BG_SIDEBAR = "#E8EDF5"        # 侧栏：浅灰蓝（温和）
COLOR_BORDER = "#C8D5E8"            # 边框：柔灰蓝
COLOR_TEXT = "#2D3748"              # 主文字：深灰（柔和黑）
COLOR_TEXT_DIM = "#8896A8"          # 次级文字：中灰

# 强调色：薄荷蓝（清新不刺眼）
COLOR_NEURON_BLUE = "#5B9BD5"       # 主操作蓝（温和）
COLOR_NEURON_CYAN = "#4ECDC4"       # 状态青（清新）
COLOR_NEURON_PURPLE = "#9B8FD4"     # 强调紫（柔和）
COLOR_NEURON_ORANGE = "#F4A261"     # 警示橙（温和暖色）

# 辉光：浅天蓝
COLOR_NEURAL_GLOW = "#A8C8F0"


# ============================================================================
# 完整 QSS（清爽浅色主题）
# ============================================================================
MAIN_QSS = f"""
/* ====== 全局基础 ====== */
QMainWindow, QWidget#central {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT};
    font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    font-size: 12px;
}}

/* ====== GroupBox ====== */
QGroupBox {{
    font-weight: 600;
    font-size: 12px;
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    margin-top: 12px;
    background-color: {COLOR_BG_PANEL};
    padding: 10px 8px 8px 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 8px;
    color: {COLOR_NEURON_CYAN};
    background-color: {COLOR_BG_PANEL};
    border-radius: 4px;
    letter-spacing: 1px;
}}

/* ====== 标签 ====== */
QLabel {{
    color: {COLOR_TEXT};
    background: transparent;
}}
QLabel[role="dim"] {{
    color: {COLOR_TEXT_DIM};
}}
QLabel[role="status"] {{
    color: {COLOR_NEURON_CYAN};
    padding: 5px 10px;
    border-radius: 5px;
    background-color: rgba(78, 205, 196, 0.12);
    border: 1px solid rgba(78, 205, 196, 0.30);
    font: bold 13px;
    qproperty-alignment: AlignCenter;
}}
QLabel[role="dot"] {{
    color: {COLOR_NEURON_CYAN};
    font: bold 16px;
    background: transparent;
    padding: 0;
    border: none;
}}
QLabel[role="dot_warn"] {{
    color: {COLOR_NEURON_ORANGE};
    font: bold 16px;
    background: transparent;
    padding: 0;
    border: none;
}}
QLabel[role="status_warn"] {{
    color: {COLOR_NEURON_ORANGE};
    background-color: rgba(244, 162, 97, 0.12);
    border: 1px solid rgba(244, 162, 97, 0.30);
}}

/* ====== 下拉框 ====== */
QComboBox {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 5px 10px;
    selection-background-color: {COLOR_NEURON_BLUE};
}}
QComboBox:hover {{
    border: 1px solid {COLOR_NEURON_BLUE};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {COLOR_NEURON_BLUE};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLOR_BG_PANEL};
    color: {COLOR_TEXT};
    selection-background-color: {COLOR_NEURON_BLUE};
    border: 1px solid {COLOR_BORDER};
    outline: 0;
}}

/* ====== 按钮（基础胶囊按钮）====== */
QPushButton {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    border: 1px solid {COLOR_NEURON_BLUE};
    background-color: rgba(91, 155, 213, 0.10);
    color: {COLOR_NEURON_BLUE};
}}
QPushButton:pressed {{
    background-color: rgba(91, 155, 213, 0.18);
}}
QPushButton:disabled {{
    color: {COLOR_TEXT_DIM};
    background-color: rgba(200, 213, 232, 0.40);
    border: 1px solid rgba(200, 213, 232, 0.60);
}}

/* 主操作蓝：采集开始 / 暂停 */
QPushButton[role="primary"] {{
    color: white;
    background-color: {COLOR_NEURON_BLUE};
    border: 1px solid {COLOR_NEURON_BLUE};
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{
    background-color: #4A8BC4;
    border-color: #4A8BC4;
}}
QPushButton[role="primary"]:checked {{
    background-color: {COLOR_NEURON_PURPLE};
    border-color: {COLOR_NEURON_PURPLE};
}}

/* 警示橙：实验运行 / 警示 */
QPushButton[role="warn"] {{
    color: white;
    background-color: {COLOR_NEURON_ORANGE};
    border: 1px solid {COLOR_NEURON_ORANGE};
}}
QPushButton[role="warn"]:hover {{
    background-color: #E8924E;
    border-color: #E8924E;
}}

/* 次级按钮 */
QPushButton[role="secondary"] {{
    color: {COLOR_TEXT_DIM};
    background-color: transparent;
    border: 1px solid {COLOR_BORDER};
}}
QPushButton[role="secondary"]:hover {{
    color: {COLOR_NEURON_BLUE};
    border: 1px solid {COLOR_NEURON_BLUE};
}}

/* 事件标记按钮 */
QPushButton[role="event"] {{
    border-radius: 10px;
    padding: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QPushButton[role="event_target"] {{
    color: {COLOR_NEURON_BLUE};
    background-color: rgba(91, 155, 213, 0.08);
    border: 1px solid rgba(91, 155, 213, 0.40);
}}
QPushButton[role="event_target"]:hover {{
    background-color: rgba(91, 155, 213, 0.16);
    border: 1px solid {COLOR_NEURON_BLUE};
}}
QPushButton[role="event_standard"] {{
    color: {COLOR_NEURON_ORANGE};
    background-color: rgba(244, 162, 97, 0.08);
    border: 1px solid rgba(244, 162, 97, 0.40);
}}
QPushButton[role="event_standard"]:hover {{
    background-color: rgba(244, 162, 97, 0.16);
    border: 1px solid {COLOR_NEURON_ORANGE};
}}

/* 侧栏折叠按钮 */
QPushButton#toggleButton {{
    background-color: {COLOR_NEURON_BLUE};
    color: white;
    border: none;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    font-weight: 700;
}}
QPushButton#toggleButton:hover {{
    background-color: #4A8BC4;
}}

/* ====== 侧栏 ====== */
QWidget#sideBarWidget {{
    background-color: {COLOR_BG_SIDEBAR};
    border-right: 1px solid {COLOR_BORDER};
}}

/* ====== 主内容区 ====== */
QWidget#mainContainer {{
    background-color: {COLOR_BG_DARK};
}}

/* ====== 滚动条 ====== */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: {COLOR_BORDER};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLOR_NEURON_BLUE};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    background: none;
    height: 0;
}}

/* ====== DockWidget（VR 视频流）====== */
QDockWidget {{
    color: {COLOR_TEXT};
    titlebar-close-icon: none;
}}
QDockWidget::title {{
    background-color: {COLOR_BG_PANEL};
    padding: 6px 10px;
    border-bottom: 1px solid {COLOR_BORDER};
    text-align: left;
}}
QDockWidget > QWidget {{
    background-color: {COLOR_BG_PANEL};
}}

/* ====== Tab 控件（标签页）====== */
QTabWidget::pane {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    background-color: {COLOR_BG_PANEL};
    margin-top: -1px;
}}
QTabBar::tab {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT_DIM};
    padding: 6px 14px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{
    background-color: {COLOR_BG_PANEL};
    color: {COLOR_NEURON_CYAN};
    border-bottom: 2px solid {COLOR_NEURON_CYAN};
}}
QTabBar::tab:hover:!selected {{
    color: {COLOR_TEXT};
}}

/* ====== 输入框 ====== */
QLineEdit {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 5px 10px;
    selection-background-color: {COLOR_NEURON_BLUE};
}}
QLineEdit:focus {{
    border: 1px solid {COLOR_NEURON_BLUE};
}}

/* ====== SpinBox ====== */
QSpinBox, QDoubleSpinBox {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: {COLOR_NEURON_BLUE};
}}
QSpinBox:hover, QDoubleSpinBox:hover {{
    border: 1px solid {COLOR_NEURON_BLUE};
}}

/* ====== 进度条 ====== */
QProgressBar {{
    background-color: rgba(200, 213, 232, 0.40);
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    text-align: center;
    color: {COLOR_TEXT};
    font-weight: 600;
}}
QProgressBar::chunk {{
    background-color: {COLOR_NEURON_CYAN};
    border-radius: 5px;
}}
"""


def _hex_to_rgb(c: str):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _mix(c1: str, c2: str, t: float) -> str:
    """线性混合两个颜色 (t=0 -> c1, t=1 -> c2)。"""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


class EEGBreathingController(QtCore.QObject):
    """
    根据 EEGViewWidget 的 _last_key 模拟"神经脉冲"呼吸效果：
    - 周期性微调侧栏背景色
    - 调整开始按钮边框颜色（脉冲辉光）
    - 不修改任何布局尺寸，纯色变

    节拍：~1Hz（神经放电慢波）+ 0.5s 内渐变。
    """

    def __init__(self, eeg_view, sidebar: QtWidgets.QWidget, primary_btn: QtWidgets.QPushButton,
                 status_label: QtWidgets.QLabel = None, parent: QtCore.QObject = None):
        super().__init__(parent)
        self._eeg = eeg_view
        self._sidebar = sidebar
        self._primary = primary_btn
        self._status_label = status_label

        self._phase = 0.0           # 0..1
        self._interval = 40          # ms（25 fps 足够）
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._interval)

        # 捕获原始色，用于脉冲收敛回去
        self._orig_sidebar_bg = COLOR_BG_SIDEBAR
        self._orig_primary_bg = COLOR_NEURON_BLUE

        # 缓存最近一次 setStyleSheet 的字符串，避免颜色未变化时重复刷新 Qt 样式
        self._cached_sidebar_ss = ""
        self._cached_primary_ss = ""
        self._cached_status_ss = ""

    def _tick(self):
        # 根据 _last_key 是否在前进判断"有信号"
        last_key = float(getattr(self._eeg, "_last_key", 0.0))
        prev = getattr(self, "_prev_last_key", None)
        advancing = (prev is not None) and (last_key - prev) > 0.001
        self._prev_last_key = last_key

        # 没有信号：慢呼吸 ~0.5Hz；有信号：稍快 ~1.2Hz 模拟神经放电
        if advancing:
            self._phase += 0.05
        else:
            self._phase += 0.02
        if self._phase > 1000:
            self._phase -= 1000

        # 0..1 的脉动强度（正弦）
        s = 0.5 + 0.5 * _math.sin(self._phase)

        # 侧栏：浅灰蓝底色泛起柔和薄荷蓝微光
        sb = _mix(self._orig_sidebar_bg, "#DCE8F5", 0.55 * s)
        sb = _mix(sb, "#C8E4E0", 0.25 * s)  # 薄荷清透
        sb_ss = f"QWidget#sideBarWidget {{ background-color: {sb}; }}"
        if sb_ss != self._cached_sidebar_ss:
            self._cached_sidebar_ss = sb_ss
            self._sidebar.setStyleSheet(sb_ss)

        # 主按钮边框辉光（蓝 ↔ 青）
        if self._primary is not None:
            glow = _mix(COLOR_NEURON_BLUE, COLOR_NEURON_CYAN, s)
            p_ss = (
                f"QPushButton[role='primary'] {{"
                f"  color: white;"
                f"  background-color: {COLOR_NEURON_BLUE};"
                f"  border: 1px solid {glow};"
                f"  padding: 7px 14px;"
                f"  border-radius: 8px;"
                f"  font-weight: 600;"
                f"}}"
                f"QPushButton[role='primary']:hover {{ background-color: #4A8BC4; }}"
                f"QPushButton[role='primary']:checked {{"
                f"  background-color: {COLOR_NEURON_PURPLE};"
                f"  border: 1px solid {COLOR_NEURAL_GLOW};"
                f"}}"
            )
            if p_ss != self._cached_primary_ss:
                self._cached_primary_ss = p_ss
                self._primary.setStyleSheet(p_ss)

        # 状态标签：青↔紫的微弱脉冲（仅文字色）
        if self._status_label is not None:
            c = _mix(COLOR_NEURON_CYAN, COLOR_NEURON_PURPLE, 0.5 * s)
            s_ss = (
                f"color: {c}; padding: 5px 10px; border-radius: 5px;"
                f" background-color: rgba(78, 205, 196, 0.10);"
                f" border: 1px solid rgba(78, 205, 196, 0.25);"
            )
            if s_ss != self._cached_status_ss:
                self._cached_status_ss = s_ss
                self._status_label.setStyleSheet(s_ss)

    def stop(self):
        self._timer.stop()
