"""Independent, time-fading viewport gaze trace (no scene/world tracking)."""

import csv
import math
import threading
import time
from collections import deque

from PyQt5 import QtCore, QtGui, QtWidgets
from biostyle import PLOT, BORDER, MUTED, ORANGE


class GazeTrajectoryWidget(QtWidgets.QWidget):
    """Receive raw Unity viewport points safely from the TCP worker thread."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 220)
        self.trail_seconds = 5.0
        self.gap_seconds = 0.25
        self._points = deque(maxlen=4096)
        self._lock = threading.Lock()
        self._break_pending = True
        self._clock = time.monotonic
        self._record_started = None
        self._record_rows = []
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(33)

    def update_gaze_point(self, x, y):
        """Store only: no GUI calls from the network callback. Keep raw samples."""
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            self.break_trace()
            return
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1):
            self.break_trace()
            return
        now = self._clock()
        with self._lock:
            connected = (not self._break_pending and bool(self._points)
                         and 0 <= now - self._points[-1][0] <= self.gap_seconds)
            # Unity viewport has its origin at the bottom left; Qt uses top left.
            self._points.append((now, x, 1.0 - y, connected))
            if self._record_started is not None and now >= self._record_started:
                self._record_rows.append((time.time_ns() / 1e6,
                    now - self._record_started, x, y,
                    int(connected and bool(self._record_rows))))
            self._break_pending = False

    def start_recording(self):
        with self._lock:
            self._record_started = self._clock()
            self._record_rows = []

    def stop_recording(self):
        with self._lock:
            self._record_started = None

    def save_csv(self, path):
        """保存整次实验的有效注视点；坐标保持 Unity 原始归一化 X/Y。"""
        with self._lock:
            rows = list(self._record_rows)
        with open(path, 'w', newline='', encoding='utf-8-sig') as output:
            writer = csv.writer(output)
            writer.writerow(['timestamp_ms', 'elapsed_s', 'gaze_x', 'gaze_y',
                             'connected_to_previous'])
            writer.writerows(rows)

    def break_trace(self):
        """Preserve the fading tail but never join it to a new connection."""
        with self._lock:
            self._break_pending = True

    def clear(self):
        with self._lock:
            self._points.clear()
            self._break_pending = True
        self.update()

    def visible_points(self, now=None):
        now = self._clock() if now is None else now
        with self._lock:
            while self._points and now - self._points[0][0] >= self.trail_seconds:
                self._points.popleft()
            return list(self._points)

    def opacity(self, timestamp, now):
        return max(0.0, min(1.0, 1.0 - (now - timestamp) / self.trail_seconds)) ** 2

    def _refresh(self):
        # Prune even when hidden or no more packets arrive.
        self.visible_points()
        self.update()

    def plot_rect(self):
        available = QtCore.QRectF(self.rect()).adjusted(42, 38, -20, -36)
        width = min(available.width(), available.height() * 16 / 9)
        height = width * 9 / 16
        return QtCore.QRectF(available.center().x() - width / 2,
                             available.center().y() - height / 2, width, height)

    def paintEvent(self, event):
        now = self._clock()
        points = self.visible_points(now)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor(PLOT))
        rect = self.plot_rect()
        painter.setPen(QtGui.QPen(QtGui.QColor(BORDER), 1))
        painter.setBrush(QtGui.QColor(PLOT))
        painter.drawRect(rect)
        for i in range(1, 4):
            x, y = rect.left() + rect.width() * i / 4, rect.top() + rect.height() * i / 4
            painter.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))
            painter.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))
        painter.setPen(QtGui.QColor(MUTED))
        painter.drawText(QtCore.QPointF(rect.left() - 20, rect.top() + 5), '1')
        painter.drawText(QtCore.QPointF(rect.left() - 20, rect.bottom()), '0')
        painter.drawText(QtCore.QPointF(rect.left(), rect.bottom() + 18), '0')
        painter.drawText(QtCore.QPointF(rect.right() - 7, rect.bottom() + 18), '1')
        painter.drawText(QtCore.QPointF(rect.center().x(), rect.bottom() + 18), 'X')
        painter.drawText(QtCore.QPointF(rect.left() - 26, rect.center().y()), 'Y')
        active = bool(points) and now - points[-1][0] <= self.gap_seconds
        painter.drawText(12, 22, '实时注视 · 最近 5 秒' if active else '等待注视数据 · 旧轨迹逐渐淡出')

        def position(point):
            return QtCore.QPointF(rect.left() + point[1] * rect.width(),
                                 rect.top() + point[2] * rect.height())

        def color(point):
            c = QtGui.QColor(ORANGE)
            c.setAlphaF(self.opacity(point[0], now))
            return c

        painter.save()
        painter.setClipRect(rect)
        for previous, current in zip(points, points[1:]):
            if not current[3]:
                continue
            a, b = position(previous), position(current)
            gradient = QtGui.QLinearGradient(a, b)
            gradient.setColorAt(0, color(previous))
            gradient.setColorAt(1, color(current))
            painter.setPen(QtGui.QPen(QtGui.QBrush(gradient), 2.5,
                                     QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            painter.drawLine(a, b)
        if points:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(color(points[-1]))
            painter.drawEllipse(position(points[-1]), 5, 5)
        painter.restore()
        painter.end()
