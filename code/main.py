"""
EEG 实时采集与分析系统 - 主入口

使用方法:
    python main.py
"""

import sys
from PyQt5 import QtWidgets
from main_window import MainWindow


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow() 
    win.show() 
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

