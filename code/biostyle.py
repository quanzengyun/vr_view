"""医疗浅色主题：白色面板、蓝灰分隔、医疗蓝操作色。"""

from PyQt5 import QtGui

BG = "#F3F6F9"
PANEL = "#FFFFFF"
SIDEBAR = "#EAF1F6"
PLOT = "#FCFDFE"
HOVER = "#E4EFF8"
BORDER = "#CBD8E3"
TEXT = "#243B4F"
MUTED = "#536B7D"
BLUE = "#166BA8"
PRIMARY = "#1475AD"
CYAN = "#087F8C"
ORANGE = "#AB5B16"
PURPLE = "#7556A2"
SUCCESS = "#197645"
ERROR = "#B53F47"

MAIN_QSS = f"""
QWidget {{
    color: {TEXT};
    font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI';
    font-size: 12px;
    selection-background-color: {PRIMARY};
    selection-color: white;
}}
QMainWindow, QDialog, QWidget#central, QWidget#mainContainer {{ background: {BG}; }}
QWidget#sideBarWidget {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QGroupBox {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px;
    margin-top: 10px; padding: 10px 8px 8px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {BLUE};
}}
QLabel {{ background: transparent; border: none; }}
QLabel[role="dim"] {{ color: {MUTED}; }}
QLabel[role="title"] {{ font-size: 13px; font-weight: 600; }}
QLabel[role="status"] {{
    color: {CYAN}; background: {BG}; border: 1px solid {BORDER};
    border-radius: 5px; padding: 5px 8px; font-weight: 600;
}}
QLabel[role="dot"] {{ color: {MUTED}; font-size: 16px; }}
QFrame#erpHeader {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QPushButton {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 7px 12px; font-weight: 600;
}}
QPushButton:hover {{ background: {HOVER}; border-color: {BLUE}; }}
QPushButton:pressed, QPushButton:checked {{ background: {HOVER}; border-color: {CYAN}; }}
QPushButton[role="primary"] {{ background: {PRIMARY}; border-color: {PRIMARY}; color: white; }}
QPushButton[role="primary"]:hover {{ background: #0C6395; border-color: {BLUE}; }}
QPushButton[role="primary"]:checked {{ background: {ERROR}; border-color: {ERROR}; }}
QPushButton:disabled, QPushButton[role="primary"]:disabled {{
    background: {SIDEBAR}; color: #82909C; border-color: {BORDER};
}}
QPushButton#toggleButton {{ padding: 0; background: {PANEL}; border: none; border-radius: 0; }}
QPushButton#toggleButton:hover {{ background: {HOVER}; color: {BLUE}; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 6px;
}}
QComboBox:hover, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {BLUE}; }}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: #82909C; background: {BG}; }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox::down-arrow {{
    image: none; width: 0; height: 0; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid {MUTED};
}}
QComboBox QAbstractItemView, QMenu {{
    background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; outline: 0;
}}
QMenu::item {{ padding: 6px 16px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QCheckBox {{ background: transparent; spacing: 6px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border: 1px solid {MUTED}; border-radius: 3px; background: {BG};
}}
QCheckBox::indicator:checked {{ background: {CYAN}; border-color: {CYAN}; }}
QCheckBox::indicator:hover {{ border-color: {BLUE}; }}
QCheckBox:disabled {{ color: #82909C; }}
QListWidget, QTreeView, QTableView {{ background: {PLOT}; alternate-background-color: {PANEL}; border: none; }}
QHeaderView::section {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 5px; }}
QDockWidget::title {{
    background: {PANEL}; padding: 15px 10px; border-bottom: 1px solid {BORDER}; text-align: left;
}}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{ background: {HOVER}; }}
QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:hover {{ background: {BORDER}; }}
QScrollBar:vertical {{ background: {BG}; width: 8px; margin: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 8px; margin: 0; }}
QScrollBar::handle {{ background: {BORDER}; border-radius: 4px; min-width: 20px; min-height: 20px; }}
QScrollBar::handle:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QSlider::groove:horizontal {{ background: {BORDER}; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {BLUE}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QTabWidget::pane {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QTabBar::tab {{ background: {BG}; color: {MUTED}; padding: 6px 12px; }}
QTabBar::tab:selected {{ background: {PANEL}; color: {CYAN}; border-bottom: 2px solid {CYAN}; }}
QProgressBar {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {PRIMARY}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px; }}
"""


def apply_theme(widget):
    """调色板覆盖原生控件的默认底色；QSS 负责控件状态与间距。"""
    palette = widget.palette()
    for role, color in ((QtGui.QPalette.Window, BG), (QtGui.QPalette.Base, PLOT),
                        (QtGui.QPalette.AlternateBase, PANEL), (QtGui.QPalette.Button, PANEL),
                        (QtGui.QPalette.WindowText, TEXT), (QtGui.QPalette.Text, TEXT),
                        (QtGui.QPalette.ButtonText, TEXT), (QtGui.QPalette.Highlight, PRIMARY),
                        (QtGui.QPalette.HighlightedText, "#FFFFFF"), (QtGui.QPalette.ToolTipBase, PANEL),
                        (QtGui.QPalette.ToolTipText, TEXT)):
        palette.setColor(role, QtGui.QColor(color))
    for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
        palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor(MUTED))
    widget.setPalette(palette)
    widget.setStyleSheet(MAIN_QSS)


def style_plot(plot):
    """只统一图表外观，不改变采样、视图范围或鼠标交互。"""
    plot.setBackground(PLOT)
    plot.getViewBox().setBackgroundColor(PLOT)
    for side in ('left', 'bottom', 'right', 'top'):
        axis = plot.getAxis(side)
        axis.setPen(BORDER)
        axis.setTextPen(MUTED)
        axis.setStyle(tickFont=QtGui.QFont('Microsoft YaHei UI', 8))
