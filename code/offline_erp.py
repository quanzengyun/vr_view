"""
离线 ERP 分析脚本：用 MNE 读取保存的 EDF 文件，按 trigger 信息做 epoching，
画出 P300 经典对比图（target vs standard）。

用法：
    python offline_erp.py <path/to/file.edf>

依赖：mne, numpy, matplotlib, scipy
"""

import os
import sys
import json
import argparse
from typing import List, Optional, Tuple

import numpy as np


def load_edf_with_events(edf_path: str) -> Tuple["mne.io.Raw", List[dict]]:
    """读 EDF + 同目录下 events.json，合并为统一事件列表。

    新格式使用已定位软件事件并核对 EDF；旧格式优先 EDF 注释，不跨来源合并去重。
    返回:
        raw: mne Raw
        events: [{onset_sec, label, source}, ...]
    """
    import mne

    raw = mne.io.read_raw_edf(edf_path, preload=True, verbose=False)

    auxiliary = {name: "stim" for name in ("TRIG", "HW_TRIG") if name in raw.ch_names}
    if auxiliary:
        raw.set_channel_types(auxiliary, verbose=False)
    json_path = os.path.splitext(edf_path)[0] + ".events.json"
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            document = json.load(f)
        if document.get("marker_schema") == "sample_index_v1":
            # 唯一事件源；不能用500ms去重合并重复标签，也不能把未定位事件加进来。
            events = [dict(event, source="events_json") for event in document.get("events", [])
                      if event.get("status") == "located"]
            events.sort(key=lambda event: (event["sample_index"], event["event_id"]))
            expected = [(str(int(event["label"])), event["sample_index"] / raw.info["sfreq"])
                        for event in events]
            actual = [(str(ann["description"]), float(ann["onset"]))
                      for ann in raw.annotations if str(ann["description"]).isdigit()]
            # MNE/edfio 会按文本重排同一时刻的注释；比较完整事件多重集，
            # 仍逐条保留重复事件，返回列表保留接收 event_id 的顺序。
            expected.sort(key=lambda item: (item[1], item[0]))
            actual.sort(key=lambda item: (item[1], item[0]))
            if len(actual) != len(expected) or any(a[0] != e[0] or abs(a[1]-e[1]) > 1e-6
                                                   for a,e in zip(actual,expected)):
                raise ValueError("EDF 注释与同名软件事件日志不一致")
            for event in events:
                event["onset_sec"] = event["sample_index"] / raw.info["sfreq"]
            return raw, events

    # ---- 事件来源 1：EDF+ annotation ----
    events_from_annot = []
    if raw.annotations is not None and len(raw.annotations) > 0:
        for ann in raw.annotations:
            desc = str(ann.get('description', ''))
            onset = float(ann.get('onset', 0.0))
            label = 0
            # EDF+ annotation description 现在直接存 label 整数（如 "33"）；
            # 兼容旧的 "label=33" / "trigger=33" 写法
            try:
                label = int(desc.strip())
            except Exception:
                if "=" in desc:
                    try:
                        label = int(desc.split("=", 1)[1])
                    except Exception:
                        label = 0
            events_from_annot.append({
                "onset_sec": onset,
                "label": label,
                "source": "edf_annotation",
            })

    # ---- 事件来源 2：events.json ----
    events_from_json = []
    json_path = os.path.splitext(edf_path)[0] + ".events.json"
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        start_ms = None
        try:
            # events.json 现在自带 start_time_ms，不再依赖 .cnt.json
            start_ms = int(data.get("start_time_ms", 0) or 0)
        except Exception:
            pass
        if start_ms is None:
            start_ms = 0

        for ev in data.get("events", []):
            ts_ms = int(ev.get("timestamp_ms", 0))
            onset_sec = (ts_ms - start_ms) / 1000.0
            events_from_json.append({
                "onset_sec": onset_sec,
                "label": int(ev.get("label", 0)),
                "source": "events_json",
                "event_type": ev.get("event_type", "unknown"),
                "channel": int(ev.get("channel", 0)),
            })

    # 旧文件优先使用 EDF 注释，保留所有重复事件；无注释时才回退到同名日志。
    selected = [event for event in events_from_annot if event["label"] > 0]
    if not selected:
        selected = [event for event in events_from_json if event["label"] > 0]
    selected.sort(key=lambda event: event["onset_sec"])
    return raw, selected


def extract_epochs_by_label(raw, events: List[dict],
                            pre_ms: int = 200, post_ms: int = 800,
                            picks: Optional[List[str]] = None,
                            low_hz: Optional[float] = None,
                            high_hz: Optional[float] = None) -> dict:
    """根据 events 列表提取 epochs，按 label 分组。

    可选地对 EEG 通道应用零相位带通滤波：
        - low_hz > 0  → 高通（去除直流/漂移）
        - high_hz > 0 → 低通（去除高频噪声）
        - 0 / None    → 不启用该方向
    默认与显示端一致：0.01Hz HP + 30Hz LP（caller 不传时启用）。

    Args:
        raw: mne.io.Raw
        events: List[dict]，来自 load_edf_with_events
        pre_ms: epoch 刺激前长度 (ms)
        post_ms: epoch 刺激后长度 (ms)
        picks: 要纳入的通道名（默认所有 EEG，不含 stim）
        low_hz: 高通截止 (Hz)；None=不启用，0=不启用，>0 且 < sfreq/2=启用
        high_hz: 低通截止 (Hz)；同上

    Returns:
        {
            label_int: epochs ndarray (n_trials, n_channels, n_times),
            ...
        }
    """
    sfreq = float(raw.info['sfreq'])

    if picks is None:
        # 默认所有 EEG 通道（去掉 stim 类型）
        picks = [ch for ch in raw.ch_names
                 if raw.get_channel_types(picks=[ch])[0] != 'stim']

    # ---- 离线带通滤波（零相位 filtfilt 保持 epoch 对齐）----
    # 录制时存的是原始数据（不滤波），这里根据用户参数选频带分析
    # 注意：低频建议 ≥ 0.1Hz 起步（避免过度颠簸拖尾）
    raw_for_epoch = raw
    l, h = (float(low_hz) if low_hz else None), (float(high_hz) if high_hz else None)
    if l is not None or h is not None:
        # mne 要求 low < high，且都在 (0, sfreq/2)
        nyq = sfreq / 2.0
        l_val = l if (l and 0 < l < nyq) else None
        h_val = h if (h and 0 < h < nyq) else None
        if l_val is not None or h_val is not None:
            try:
                # 用 IIR butterworth 而非 FIR：数据很短（5s）时 FIR 滤波器长度会超过信号导致失真
                # IIR 阶数低、延迟小，适合短数据
                try:
                    raw_for_epoch = raw.copy().filter(
                        l_freq=l_val, h_freq=h_val,
                        picks=picks, method='iir',
                        iir_params=dict(order=4, ftype='butter'),
                        verbose=False
                    )
                except (TypeError, Exception):
                    # 旧版 mne 不支持 method/iir_params 参数
                    raw_for_epoch = raw.copy().filter(
                        l_freq=l_val, h_freq=h_val,
                        picks=picks, verbose=False
                    )
                f_range = []
                if l_val: f_range.append(f"{l_val:g}Hz HP")
                if h_val: f_range.append(f"{h_val:g}Hz LP")
                print(f"[Filter] 离线带通滤波已应用：{' + '.join(f_range)}（IIR Butterworth 4 阶；原始数据未修改）")
            except Exception as _exc:
                print(f"[Filter] 滤波失败（fallback 用原始数据）: {_exc}")
                raw_for_epoch = raw

    pre_s = pre_ms / 1000.0
    post_s = post_ms / 1000.0

    data = raw_for_epoch.get_data(picks=picks)  # (n_channels, n_times)
    n_channels, n_times = data.shape
    n_pre = int(pre_s * sfreq)
    n_post = int(post_s * sfreq)
    n_epoch = n_pre + n_post

    grouped = {}
    skipped = 0
    for ev in events:
        label = ev["label"]
        onset_sample = int(ev["onset_sec"] * sfreq)
        s_start = onset_sample - n_pre
        s_end = onset_sample + n_post
        if s_start < 0 or s_end > n_times:
            skipped += 1
            continue
        epoch = data[:, s_start:s_end]  # (n_channels, n_epoch)
        grouped.setdefault(label, []).append(epoch)

    out = {k: np.stack(v) for k, v in grouped.items() if len(v) >= 1}
    print(f"[Epochs] 共 {len(events)} 个事件，分组成 {len(out)} 个 label，跳过 {skipped} 个（窗口越界）")
    for k, arr in out.items():
        print(f"  label={k}: {arr.shape[0]} trials, shape={arr.shape}")
    return out


def label_to_event(label: int) -> Tuple[str, int]:
    """label -> (event_type, channel)

    与 vrcode/PICO.cs (247-252) 和 connect/V2.m (102-127) 保持一致：
        X1-X8 (target)   -> 字符重复2次 -> 11, 22, 33, 44, 55, 66, 77, 88
        G1-G8 (standard) -> ASCII(c)-48 -> 1, 2, 3, 4, 5, 6, 7, 8
        Y (被试按键反应)  -> 固定 89 -> response
    """
    if label == 89:
        return ("response", 0)
    if label in (11, 22, 33, 44, 55, 66, 77, 88):
        return ("target", label // 11)    # 44 -> target ch=4
    if 1 <= label <= 8:
        return ("standard", label)         # 3  -> standard ch=3
    return ("unknown", label)


def plot_erp(epochs_by_label: dict, sfreq: float, pre_ms: int = 200, post_ms: int = 800,
             out_png: Optional[str] = None,
             ch_names: Optional[List[str]] = None) -> None:
    """画 target vs standard 叠加 ERP。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    target_epochs = []
    standard_epochs = []
    for label, arr in epochs_by_label.items():
        evt, _ = label_to_event(label)
        if evt == "target":
            target_epochs.append(arr)
        elif evt == "standard":
            standard_epochs.append(arr)

    n = max(0, 1)
    n_target = sum(arr.shape[0] for lbl, arr in epochs_by_label.items() if label_to_event(lbl)[0] == "target")
    n_standard = sum(arr.shape[0] for lbl, arr in epochs_by_label.items() if label_to_event(lbl)[0] == "standard")
    n_resp = sum(arr.shape[0] for lbl, arr in epochs_by_label.items() if label_to_event(lbl)[0] == "response")
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle(f"ERP 离线分析（P300 范式） — Target: {n_target}, Standard: {n_standard}, Resp: {n_resp}",
                 fontsize=13)

    # 上左：全部通道平均的 target 叠加
    ax = axes[0, 0]
    if target_epochs:
        avg = np.mean(np.concatenate(target_epochs, axis=0), axis=(0, 1))
        ax.plot(np.arange(len(avg)) / sfreq * 1000 - pre_ms, avg * 1e6, color='red', label='Target')
    if standard_epochs:
        avg = np.mean(np.concatenate(standard_epochs, axis=0), axis=(0, 1))
        ax.plot(np.arange(len(avg)) / sfreq * 1000 - pre_ms, avg * 1e6, color='blue', label='Standard')
    ax.axhline(0, color='k', lw=0.5)
    ax.axvline(0, color='k', lw=0.5, ls='--')
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Amplitude (µV)")
    ax.set_title("Grand average ERP (all channels)")
    ax.legend()

    # 找到 Pz 通道索引（在所有 epochs 的第 1 维）
    pz_idx = None
    if epochs_by_label:
        first_label = next(iter(epochs_by_label))
        # 从 raw 取通道列表：通过传入 raw；这里改用传入的 ch_names
        pass

    # 上右：trial 数量柱状图
    ax = axes[0, 1]
    labels_list = list(epochs_by_label.keys())
    counts = [epochs_by_label[k].shape[0] for k in labels_list]
    ax.bar([str(l) for l in labels_list], counts, color='steelblue')
    ax.set_xlabel("Marker label")
    ax.set_ylabel("Trial count")
    ax.set_title("每个 label 的 trial 数")

    # 下左：差异波 (target - standard)
    ax = axes[1, 0]
    if target_epochs and standard_epochs:
        tg = np.mean(np.concatenate(target_epochs, axis=0), axis=(0, 1))
        st = np.mean(np.concatenate(standard_epochs, axis=0), axis=(0, 1))
        diff = tg - st
        ax.plot(np.arange(len(diff)) / sfreq * 1000 - pre_ms, diff * 1e6, color='green')
        ax.axhline(0, color='k', lw=0.5)
        ax.axvline(0, color='k', lw=0.5, ls='--')
        ax.set_xlabel("Time (ms)")
        ax.set_ylabel("Δ amplitude (µV)")
        ax.set_title("Difference wave (Target - Standard)")
    else:
        ax.text(0.5, 0.5, "no target/standard both", ha='center', transform=ax.transAxes)

    # 下中：Pz 单通道 ERP（如果存在）
    pz_idx = None
    if ch_names:
        for i, name in enumerate(ch_names):
            if name.upper() == "PZ":
                pz_idx = i
                break
    if pz_idx is not None and (target_epochs or standard_epochs):
        # 用一个 2x3 的最后一个子图替代 1,1 的文字汇总
        # 这里我们改用第三个轴追加
        ax2 = fig.add_subplot(2, 3, 6)
        if target_epochs:
            tg_pz = np.concatenate([e[None, pz_idx] for arr in target_epochs for e in [arr]], axis=0)
            tg_pz = np.concatenate(target_epochs, axis=0)[:, pz_idx, :].mean(axis=0)
            ax2.plot(np.arange(len(tg_pz)) / sfreq * 1000 - pre_ms, tg_pz * 1e6, color='red', label='Target')
        if standard_epochs:
            st_pz = np.concatenate(standard_epochs, axis=0)[:, pz_idx, :].mean(axis=0)
            ax2.plot(np.arange(len(st_pz)) / sfreq * 1000 - pre_ms, st_pz * 1e6, color='blue', label='Standard')
        ax2.axhline(0, color='k', lw=0.5)
        ax2.axvline(0, color='k', lw=0.5, ls='--')
        ax2.axvspan(280, 500, alpha=0.15, color='yellow', label='P300 window')
        ax2.set_xlabel("Time (ms)")
        ax2.set_ylabel("Amplitude (µV)")
        ax2.set_title("Pz channel")
        ax2.legend(loc='upper right', fontsize=8)
    else:
        # 没有 Pz 时保留文字汇总
        ax = axes[1, 1]
        ax.axis("off")
    summary_lines = [
        f"Events total: {sum(epochs_by_label[k].shape[0] for k in epochs_by_label)}",
        f"Labels: {sorted(epochs_by_label.keys())}",
    ]
    for k in sorted(epochs_by_label.keys()):
        evt, ch = label_to_event(k)
        summary_lines.append(f"  label={k} ({evt} ch{ch}): {epochs_by_label[k].shape[0]} trials")
    ax.text(0, 1, "\n".join(summary_lines), va='top', family='monospace', fontsize=10)

    plt.tight_layout()
    if out_png:
        plt.savefig(out_png, dpi=120, bbox_inches='tight')
        print(f"[Plot] saved to {out_png}")
    else:
        plt.show()


def main() -> int:
    parser = argparse.ArgumentParser(description="离线 ERP 分析（基于 EDF + events.json）")
    parser.add_argument("edf_path", help="EDF 文件路径")
    parser.add_argument("--pre", type=int, default=200, help="epoch 刺激前长度 (ms)")
    parser.add_argument("--post", type=int, default=800, help="epoch 刺激后长度 (ms)")
    parser.add_argument("--low", type=float, default=None,
                        help="高通截止频率 Hz（默认不启用；建议 0.1 或 1）")
    parser.add_argument("--high", type=float, default=None,
                        help="低通截止频率 Hz（默认不启用；ERP 经典用 30）")
    parser.add_argument("--out", default=None, help="输出 PNG 路径")
    args = parser.parse_args()

    if not os.path.exists(args.edf_path):
        print(f"ERR: {args.edf_path} 不存在")
        return 1

    raw, events = load_edf_with_events(args.edf_path)
    print(f"[EDF] channels={len(raw.ch_names)}, sfreq={raw.info['sfreq']} Hz, "
          f"duration={raw.times[-1]:.1f}s, n_events={len(events)}")
    print(f"[EDF] trigger channels: {[c for c in raw.ch_names if raw.get_channel_types(picks=[c])[0] == 'stim']}")

    if not events:
        print("ERR: 没有任何 trigger 事件（EDF annotation 与 events.json 都为空）")
        return 2

    epochs_by_label = extract_epochs_by_label(raw, events,
                                              pre_ms=args.pre, post_ms=args.post,
                                              low_hz=args.low, high_hz=args.high)
    if not epochs_by_label:
        print("ERR: 没有可用的 epoch（可能所有事件都越界）")
        return 3

    out_png = args.out or os.path.splitext(args.edf_path)[0] + "_erp.png"
    ch_names_eeg = [c for c in raw.ch_names
                    if raw.get_channel_types(picks=[c])[0] != 'stim']
    plot_erp(epochs_by_label, sfreq=float(raw.info['sfreq']),
             pre_ms=args.pre, post_ms=args.post, out_png=out_png,
             ch_names=ch_names_eeg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
