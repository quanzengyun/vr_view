"""
eeg_view_widget.py —— 轻量 compat shim

原文件（1605 行）已重构为 eeg_view/ 子包：
  - eeg_view.channel_meta    通道元数据 + ChannelItemDelegate
  - eeg_view.filters         HP/LP 滤波状态机
  - eeg_view.mock_data       Mock 数据生成 + 软件 trigger 桥接
  - eeg_view.recorder        录制生命周期
  - eeg_view.persister       EDF/CSV/JSON 持久化
  - eeg_view.widget          EEGViewWidget 主类

外部 import 行为完全保持不变：
  from eeg_view_widget import EEGViewWidget
  from eeg_view_widget import save_record_to_files
  from eeg_view_widget import STANDARD_32_CHANNELS
"""

from eeg_view import (
    EEGViewWidget,
    STANDARD_32_CHANNELS,
    save_record_to_files,
)

# 也兼容其他公开 API（main_window.py 内部用到）
from eeg_view import (  # noqa: F401
    REGION_ORDER,
    ChannelItemDelegate,
    region_of,
    get_channel_color,
    get_region_band,
    get_chip_color,
    get_region_label,
    get_default_gain,
)

__all__ = [
    "EEGViewWidget",
    "save_record_to_files",
    "STANDARD_32_CHANNELS",
    "REGION_ORDER",
    "ChannelItemDelegate",
    "region_of",
    "get_channel_color",
    "get_region_band",
    "get_chip_color",
    "get_region_label",
    "get_default_gain",
]
