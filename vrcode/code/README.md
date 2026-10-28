# EEG 实时采集与分析系统

Python 版本的 EEG 实时采集、显示和 ERP 分析系统。

## 功能特性

- **实时 EEG 数据采集**：支持 eego SDK 和模拟数据
- **多通道波形显示**：33通道实时波形显示，支持滤波、增益调节
- **ERP 分析**：实时 P300 事件相关电位分析
- **VR 集成**：接收 VR 事件标记，支持眼动追踪（开发中）
- **数据导入导出**：支持 CNT 格式文件读取，CSV 格式保存

## 文件结构

```
code/
├── main.py                    # 主入口
├── main_window.py             # 主窗口界面
├── data_worker.py             # 数据采集工作线程
├── eeg_view_widget.py         # EEG 波形显示组件
├── erp_analyzer.py            # ERP 分析模块
├── erp_view_widget.py         # ERP 结果显示组件
├── shared_buffer.py           # 线程安全数据缓冲区
├── cnt_reader.py              # CNT 文件读取
├── eego_sdk.py                # eego SDK Python 封装
├── vr_marker_receiver.py      # VR 标记接收器
├── vr_video_receiver.py       # VR 视频接收器（第一步）
├── vr_video_widget.py         # VR 视频显示组件（第一步）
└── display_worker.py          # 实验显示工作线程（预留）

vrcode/                        # Unity VR 代码
├── PICO.cs                    # 主实验控制脚本
├── EyeTracker.cs              # 眼动追踪
└── ButtonRecord.cs            # 按键记录
```

## 使用方法

### 1. 安装依赖

```bash
pip install PyQt5 pyqtgraph numpy mne pillow
# 如果需要读取 ANT CNT 文件
pip install antio
```

### 2. 运行程序

```bash
python main.py
```

### 3. 基本操作

1. **开始实验**：点击"开始实验"按钮，输入被试名称
2. **暂停/继续**：实验进行中可以暂停/继续数据采集
3. **结束实验**：点击"结束实验"，数据自动保存到 `E:\VR展示\Save`
4. **导入数据**：点击"导入数据"按钮，选择 CNT 或 CSV 文件

### 4. VR 集成

- 程序启动时自动监听端口 12345，等待 VR 连接
- VR 发送的事件标记（Target/Standard）会自动用于 ERP 分析
- 视频流功能（开发中）：显示 VR 视角并叠加注视点

## 配置说明

### 采样率设置
- 默认：500 Hz
- 可选：500, 1000, 2000 Hz
- 实验进行中不可修改

### 滤波设置
- 低通/高通：0, 0.01, 1, 3, 5, 30, 50, 100, 200 Hz
- 默认：0 - 30 Hz（带通）

### 通道配置
- 默认：33通道（标准10-20系统）
- 导入 CNT 文件时自动适配通道数

## 开发计划

- [x] 第一步：Unity 发送视频帧，Python 接收并显示
- [ ] 第二步：Unity 计算注视点屏幕坐标并发送
- [ ] 第三步：Python 在视频上叠加注视点

详见：`IMPLEMENTATION_PLAN.md`

## 注意事项

1. **eego SDK**：使用真实设备时，确保 `eego-SDK.dll` 在正确路径
2. **CNT 文件路径**：避免中文路径，可能导致读取失败
3. **VR 连接**：确保 VR 和 PC 在同一局域网，端口 12345 未被占用

## 许可证

内部使用
