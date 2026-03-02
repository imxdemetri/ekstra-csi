from collections import deque

import numpy as np


class StaticRemover:
    """Subtract the static environment from CSI to isolate human motion.

    The walls and furniture dominate the signal by 20-40 dB. Without
    removing them, any motion detection is basically impossible -- you're
    looking for a 1-2% change on top of a massive static component.

    EMA works well here because it's O(1) memory (matters on the router)
    and α=0.95 gives about a 20-frame time constant at 30 fps. I tried
    windowed mean first but it ate too much RAM on the ARM core.
    """
    def __init__(self, alpha=0.95):
        self.alpha = alpha
        self._baseline = None

    def remove(self, H):
        """H: complex array (any shape). Returns H with static removed."""
        if self._baseline is None:
            self._baseline = H.copy()
            return np.zeros_like(H)
        dynamic = H - self._baseline
        self._baseline = self.alpha * self._baseline + (1 - self.alpha) * H
        return dynamic

    def reset(self):
        self._baseline = None


class DFSProfiler:
    """Doppler shift extraction -- tells you how fast things are moving.

    The math: multiply each frame by the conjugate of the previous frame.
    Static stuff cancels out (phase doesn't change), moving stuff shows
    up as a frequency in the result. FFT that and you get a velocity
    spectrum.

    At 5 GHz, 1 Hz of Doppler shift is about 3 cm/s of radial velocity.
    Hand gestures show up around 5-15 Hz, body sway is under 2 Hz.
    The separation is surprisingly clean -- I didn't expect it to work
    this well with commodity hardware.
    """
    def __init__(self, window=64, hop=16):
        self.window = window
        self.hop = hop
        self._buf = deque(maxlen=window)

    def push(self, H_row):
        self._buf.append(H_row)

    def ready(self) -> bool:
        return len(self._buf) >= self.window

    def extract(self):
        """(n_sub, window) real DFS magnitude spectrogram."""
        if not self.ready():
            return None
        frames = np.array(self._buf)  # (window, n_sub)
        conj_mult = frames[1:] * np.conj(frames[:-1])
        dfs = np.fft.fftshift(np.fft.fft(conj_mult, axis=0), axes=0)
        return np.abs(dfs).T  # (n_sub, window-1)
