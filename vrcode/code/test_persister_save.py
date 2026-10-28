"""测试 eeg_view/persister.py 的 save_record_to_files.

模拟采集：32 通道 × 1000Hz × 3 秒 + 8 个 VR marker，验证：
  1. CSV 正确
  2. EDF+ 正确（32 EEG + 1 TRIG）
  3. EDF+ annotation 写入 label
  4. events.json 正确
  5. events.json 正确
"""
import os, sys, json, time, warnings
sys.path.insert(0, r'E:\VR展示\code\eeg_view')
warnings.filterwarnings("ignore")
import numpy as np

# 1) 构造 DataPacket-like 对象
class FakePacket:
    def __init__(self, ts, voltage, trigger=0, sample_counter=0):
        self.timestamp = ts
        self.voltage = voltage
        self.trigger = trigger
        self.sample_counter = sample_counter

# 2) 模拟参数
FS = 1000.0
N_SAMPLES = 3000  # 3 秒
N_CH = 32
CH_NAMES = [f"Ch{i+1}" for i in range(N_CH)]
START_MS = 1792053500000  # 复用 test1 时间戳
TRIAL_PERIOD_MS = 740  # 用户说 700ms 是接受范围
LABELS = [11, 6, 4, 2, 7, 8, 3, 5]  # 8 个 trial

# 3) 构造 packets
print(f"=== 构造测试数据 ===")
print(f"  通道数: {N_CH}, 采样率: {FS}Hz, 时长: {N_SAMPLES/FS}s, trial_period: {TRIAL_PERIOD_MS}ms")
packets = []
events = []
ts = START_MS
for i in range(N_SAMPLES):
    # 32 通道 × 1 sample (Volt)
    v = (np.random.randn(N_CH) * 10e-6).astype(np.float64)  # ±10 µV 噪声
    p = FakePacket(ts, v, trigger=0, sample_counter=i)
    packets.append(p)
    ts += 1  # 1ms = 1000Hz

# 在 0.5s, 1.24s, 1.98s... 处放 trigger 上升沿
rising_edges = []
for k, label in enumerate(LABELS):
    # 在 500ms + k * 740ms 处
    sample_idx = int((0.5 + k * 0.740) * FS)
    if sample_idx >= N_SAMPLES:
        break
    rising_edges.append(sample_idx)
    packets[sample_idx].trigger = label
    # events.json 用 wall-clock（与 persister.py 一致）
    events.append({
        "timestamp_ms": START_MS + sample_idx,
        "event_type": "target" if label >= 11 else "standard",
        "channel": label % 10 if label < 89 else 0,
        "label": label,
    })

print(f"  packets: {len(packets)}, events: {len(events)}, trigger 上升沿: {len(rising_edges)}")

# 4) 调 persister.save_record_to_files
print(f"\n=== 调用 save_record_to_files ===")
from persister import save_record_to_files
SAVE_DIR = r"E:\VR展示\Save\test_edf_output"
os.makedirs(SAVE_DIR, exist_ok=True)
# 清空旧文件
for f in os.listdir(SAVE_DIR):
    if f.endswith(('.csv', '.edf', '.events.json')):
        os.remove(os.path.join(SAVE_DIR, f))

t0 = time.time()
result = save_record_to_files(
    packets=packets,
    events=events,
    channel_names=CH_NAMES,
    sample_rate=FS,
    amplitude_scale=1e6,  # V → µV
    save_dir=SAVE_DIR,
    subject="test_persister",
    meta={"description": "persister self-test", "trial_period_ms": TRIAL_PERIOD_MS},
)
print(f"  save_record_to_files returned: {result}")
print(f"  耗时: {time.time()-t0:.1f}s")

# 5) 检查输出
print(f"\n=== 检查输出 ===")
files = sorted(os.listdir(SAVE_DIR))
for fn in files:
    full = os.path.join(SAVE_DIR, fn)
    print(f"  {fn}  ({os.path.getsize(full):,} bytes)")

# 6) 验证 EDF
print(f"\n=== 验证 EDF+ ===")
import pyedflib
edf_files = [f for f in files if f.endswith('.edf')]
if edf_files:
    edf_path = os.path.join(SAVE_DIR, edf_files[0])
    f = pyedflib.EdfReader(edf_path,
                            pyedflib.READ_ALL_ANNOTATIONS,
                            pyedflib.DO_NOT_CHECK_FILE_SIZE)
    print(f"  n_signals = {f.signals_in_file}")
    print(f"  file_duration = {f.file_duration}s")
    print(f"  signal labels (前 5): {f.getSignalLabels()[:5]}")
    print(f"  signal labels (后 3): {f.getSignalLabels()[-3:]}")
    trig_idx = f.getSignalLabels().index('TRIG')
    trig = f.readSignal(trig_idx)
    rising = np.where((trig[1:] > 0) & (trig[:-1] == 0))[0] + 1
    print(f"  TRIG 上升沿位置: {rising.tolist()}")
    print(f"  TRIG 上升沿值: {trig[rising].tolist()}")

    ann = f.readAnnotations()
    print(f"\n  n_annotations = {len(ann[0])}")
    for k in range(len(ann[0])):
        print(f"    t={ann[0][k]:.3f}s  desc={ann[2][k]!r}")
    f.close()

# 7) 验证 events.json
print(f"\n=== 验证 events.json ===")
evt_files = [f for f in files if f.endswith('.events.json')]
if evt_files:
    with open(os.path.join(SAVE_DIR, evt_files[0]), 'r', encoding='utf-8') as fj:
        d = json.load(fj)
    print(f"  n_events: {d['n_events']}")
    print(f"  sample_rate_hz: {d['sample_rate_hz']}")
    print(f"  subject: {d['subject']}")
    print(f"  channels: {len(d['channels'])}")
    print(f"  n_samples: {d['n_samples']}")
    print(f"  start_time_ms: {d['start_time_ms']}")
    print(f"  marker_encoding: {d['marker_encoding']}")
    print(f"  first event: {d['events'][0]}")
    print(f"  last event:  {d['events'][-1]}")

print("\n=== ALL DONE ===")
