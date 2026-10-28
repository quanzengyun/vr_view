"""
eeg_view 子包入口。

公开 API（与旧的 eeg_view_widget.py 顶层的 import 行为保持一致）：
- from eeg_view import EEGViewWidget             # 主 widget
- from eeg_view import save_record_to_files       # 数据持久化（test_mock_marker_pipeline.py 用）
- from eeg_view import STANDARD_32_CHANNELS       # 标准 32 导联
- from eeg_view import region_of, get_channel_color, get_region_band, ...
"""

from .channel_meta import (
    STANDARD_32_CHANNELS,
    REGION_ORDER,
    ChannelItemDelegate,
    region_of,
    get_channel_color,
    get_region_band,
    get_chip_color,
    get_region_label,
    get_default_gain,
)
from .persister import save_record_to_files

# 延迟到模块完全加载后导出 EEGViewWidget（避免循环引用）
# 因为 EEGViewWidget 内部从 .filters / .mock_data / .recorder 导入
from .widget import EEGViewWidget  # noqa: E402

__all__ = [
    "EEGViewWidget",
    "STANDARD_32_CHANNELS",
    "REGION_ORDER",
    "ChannelItemDelegate",
    "region_of",
    "get_channel_color",
    "get_region_band",
    "get_chip_color",
    "get_region_label",
    "get_default_gain",
    "save_record_to_files",
]
