"""保存原始脑电、软件事件和独立硬件触发；EDF 注释以录制样本索引定位。"""
import collections
import json
import os
import traceback

import numpy as np
from marker_session import VALID_LABELS


def save_record_to_files(packets, events, channel_names, sample_rate, amplitude_scale,
                         save_dir, subject="subject", meta=None,
                         filter_low_hz=0.0, filter_high_hz=30.0):
    meta = dict(meta or {})
    sfreq = float(sample_rate)
    if sfreq <= 0:
        raise ValueError("采样率必须大于 0")
    packets, events = list(packets), list(events or [])
    os.makedirs(save_dir, exist_ok=True)
    base = subject or "subject"
    name, suffix = base, 0
    while any(os.path.exists(os.path.join(save_dir, name + ext))
              for ext in (".csv", ".edf", ".events.json", ".annotations.xlsx")):
        suffix += 1
        name = f"{base}_{suffix}"
    prefix = os.path.join(save_dir, name)
    n = len(packets)
    canonical = (meta.get("marker_schema") == "sample_index_v1"
                 or any("event_id" in event for event in events))
    ts = np.array([p.timestamp for p in packets], dtype=float)
    counters = np.array([getattr(p, "sample_counter", 0) for p in packets], dtype=np.int64)
    indices = np.array([getattr(p, "sample_index", None) if getattr(p, "sample_index", None) is not None
                        else i for i, p in enumerate(packets)], dtype=np.int64)
    trigger = np.zeros(n, dtype=np.int64)
    located = []
    events_out = []
    if canonical:
        increasing = n == 0 or bool(np.all(np.diff(indices) > 0))
        for event in events:
            item = dict(event)
            if item.get("status") == "located":
                index = item.get("sample_index")
                row = int(np.searchsorted(indices, index)) if increasing and index is not None else n
                if row >= n or indices[row] != index:
                    item.update(status="sample_not_recorded", reason="已定位样本不在保存的数据中")
                elif int(item.get("label", 0)) not in VALID_LABELS:
                    item.update(status="invalid_label", reason="事件类别不合法")
                else:
                    item["session_sample_index"] = int(index)
                    item["sample_index"] = row
                    item["sample_counter"] = int(counters[row])
                    item["onset_sec"] = row / sfreq
                    located.append(item)
                    if trigger[row] == 0:
                        trigger[row] = int(item["label"])
            events_out.append(item)
    else:
        # 仅保留旧测试/离线调用的兼容路径；实时录制始终使用上面的事件列表。
        trigger = np.array([int(getattr(p, "trigger", 0) or 0) for p in packets], dtype=np.int64)
        previous = 0
        for i, value in enumerate(trigger):
            if value != previous and value in VALID_LABELS:
                located.append(dict(label=int(value), sample_index=i, onset_sec=i / sfreq))
            previous = value
        events_out = [dict(event, status="legacy_unverified") for event in events]

    located.sort(key=lambda e: (e["sample_index"], e.get("event_id", 0)))
    positions = [e["sample_index"] for e in located]
    counts = collections.Counter(e.get("status", "unknown") for e in events_out)
    hardware = np.array([float(p.hardware_trigger) if getattr(p, "hardware_trigger", None) is not None
                         else np.nan for p in packets], dtype=float)
    has_hardware = any(getattr(p, "hardware_trigger", None) is not None for p in packets)
    hardware_valid = np.isfinite(hardware)
    csv_path, edf_path, export_error = None, None, None
    workbook_path, workbook_error = None, None
    hardware_events = []
    try:
        from .annotation_workbook import hardware_runs, save_annotation_workbook
    except ImportError:
        from annotation_workbook import hardware_runs, save_annotation_workbook
    hardware_events = hardware_runs(hardware, ts, counters, sfreq)
    if n:
        values_uv = np.stack([p.voltage for p in packets]) * amplitude_scale
        columns = [ts[:, None], values_uv]
        names = ["timestamp_ms"] + list(channel_names)
        if has_hardware:
            columns.append(hardware[:, None])
            names.append("hardware_trigger")
        # 保留旧 CSV 最后两列 trigger/sample_counter 的顺序；新增索引单独命名。
        columns.extend([indices[:, None], trigger[:, None], counters[:, None]])
        names.extend(["sample_index", "trigger", "sample_counter"])
        try:
            csv_path = prefix + ".csv"
            np.savetxt(csv_path, np.concatenate(columns, axis=1), delimiter=",",
                       header=",".join(names), comments="")
        except Exception:
            csv_path = None
            traceback.print_exc()
        try:
            import datetime
            import mne
            # EDF 只保存 EEG 与软件注释。普通 TRIG/HW_TRIG 信号会被新版
            # pop_biosig 再次提取为事件；原始硬件值仍完整保存在 CSV。
            names = list(channel_names)
            raw = mne.io.RawArray(values_uv.T.astype(float) * 1e-6,
                                  mne.create_info(names, sfreq, ["eeg"] * len(names)), verbose="ERROR")
            raw.set_meas_date(datetime.datetime.fromtimestamp(ts[0] / 1000.0, tz=datetime.timezone.utc))
            if located:
                raw.set_annotations(mne.Annotations([e["onset_sec"] for e in located],
                                                    [0.0] * len(located),
                                                    [str(int(e["label"])) for e in located]))
            edf_path = prefix + ".edf"
            raw.export(edf_path, fmt="edf", overwrite=True, physical_range="channelwise", verbose="ERROR")
            try:
                from .edf_patch import patch_edf_header
            except ImportError:
                from edf_patch import patch_edf_header
            patch_edf_header(edf_path)
        except Exception as exc:
            export_error = str(exc)
            edf_path = None
            traceback.print_exc()
    else:
        export_error = "no_samples: 只保存未定位事件日志，没有生成 EDF"

    try:
        workbook_path = prefix + ".annotations.xlsx"
        save_annotation_workbook(workbook_path, events_out if canonical else located,
                                 hardware_events, sfreq, has_hardware)
    except Exception as exc:
        workbook_error = str(exc)
        workbook_path = None
        traceback.print_exc()

    integrity = dict(received=len(events_out), located=counts.get("located", 0),
                     unlocated=sum(v for k, v in counts.items() if k != "located"),
                     status_counts=dict(counts), annotation_count=len(located),
                     same_sample_extra_events=len(positions) - len(set(positions)),
                     sample_index_gaps=int(np.sum(np.diff(indices) != 1)) if n else 0,
                     hardware_trigger_available=has_hardware,
                     hardware_missing_samples=int(np.sum(~hardware_valid)) if has_hardware else n,
                     hardware_nonzero_runs=len(hardware_events))
    if meta.get("hardware_counter_present") and n > 1:
        integrity["hardware_counter_gaps"] = int(np.sum(np.diff(counters) > 1))
        integrity["hardware_counter_resets"] = int(np.sum(np.diff(counters) <= 0))
    report = dict(subject=subject, channels=list(channel_names), sample_rate_hz=sfreq,
                  unit="uV", n_samples=n, start_time_ms=float(ts[0]) if n else None,
                  end_time_ms=float(ts[-1]) if n else None,
                  n_events=len(events_out), marker_schema="sample_index_v1" if canonical else "legacy_trigger",
                  timing_basis="PC 接收时刻映射到首批数据估算的采样时钟；不是光电测得的刺激时刻",
                  edf_annotations="软件事件列表生成数字文本注释，onset=录制样本索引/采样率",
                  edf_event_storage="annotations_only_v1",
                  trigger_channel_in_edf="不保存 TRIG；软件事件仅在 EDF+ Annotations 中保存一次，CSV trigger 列保留",
                  hardware_trigger_in_edf="不保存 HW_TRIG；SDK 原始值保存在 CSV hardware_trigger 列，非零段另列于标注工作表",
                  annotation_workbook=os.path.basename(workbook_path) if workbook_path else None,
                  annotation_workbook_error=workbook_error,
                  filter_low_hz=filter_low_hz, filter_high_hz=filter_high_hz,
                  integrity=integrity, edf_export_error=export_error, events=events_out, meta=meta)
    events_path = prefix + ".events.json"
    with open(events_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[Record] received={len(events_out)} located={counts.get('located', 0)} annotations={len(located)} "
          f"unlocated={integrity['unlocated']} same_sample_extra={integrity['same_sample_extra_events']}")
    return csv_path or edf_path or events_path
