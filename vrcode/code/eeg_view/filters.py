"""
EEGView 内部子模块：HP/LP 滤波状态机（每通道独立 IIR 状态）。

原本嵌在 EEGViewWidget._update_view 里（L898-922），是纯 numpy 操作，
与 widget UI 完全无关，抽出后便于单测。
"""

import numpy as np


class FilterState:
    """
    每通道独立的高通 + 低通 IIR 状态机（一阶 RC）。
    输入形状：(n_samples, n_channels)  的 ndarray (Volt)
    直接 in-place 修改输入数组以避免分配。
    """

    def __init__(self, n_channels: int) -> None:
        self.n_channels = n_channels
        self.hp_state = np.zeros(n_channels, dtype=float)
        self.hp_prev_x = np.zeros(n_channels, dtype=float)
        self.lp_state = np.zeros(n_channels, dtype=float)

    def reset(self, n_channels: int = None) -> None:
        """重置滤波状态（通道数变化时也用）。"""
        n = n_channels if n_channels is not None else self.n_channels
        self.n_channels = n
        self.hp_state = np.zeros(n, dtype=float)
        self.hp_prev_x = np.zeros(n, dtype=float)
        self.lp_state = np.zeros(n, dtype=float)

    def apply(
        self,
        x: np.ndarray,
        sample_rate: float,
        low_hz: float,
        high_hz: float,
    ) -> None:
        """
        对 2D 数组 x (n_samples, n_channels) 施加 HP+LP 滤波（in-place）。

        与原 _update_view 中实现 1:1 等价（每通道独立状态、每 sample 串行更新），
        性能已经实测过 OK，不要改成向量化（向量化需要前一个 sample 的输出，串行依赖无法避免）。
        """
        if sample_rate <= 0:
            return
        dt = 1.0 / sample_rate
        # 高通（一阶）
        if low_hz > 0 and low_hz < (sample_rate / 2):
            alpha_hp = np.exp(-2 * np.pi * low_hz * dt)
            for ch in range(x.shape[1]):
                hp_state = self.hp_state[ch]
                hp_prev_x = self.hp_prev_x[ch]
                for n in range(x.shape[0]):
                    xn = x[n, ch]
                    hp_state = alpha_hp * (hp_state + xn - hp_prev_x)
                    x[n, ch] = hp_state
                    hp_prev_x = xn
                self.hp_state[ch] = hp_state
                self.hp_prev_x[ch] = hp_prev_x

        # 低通（一阶）
        if high_hz > 0 and high_hz < (sample_rate / 2):
            alpha_lp = np.exp(-2 * np.pi * high_hz * dt)
            for ch in range(x.shape[1]):
                lp_state = self.lp_state[ch]
                for n in range(x.shape[0]):
                    xn = x[n, ch]
                    lp_state = alpha_lp * lp_state + (1 - alpha_lp) * xn
                    x[n, ch] = lp_state
                self.lp_state[ch] = lp_state
