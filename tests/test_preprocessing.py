import numpy as np
import pytest
from ekstra_csi.preprocessing import StaticRemover, DFSProfiler


def test_static_remover_first_frame_is_zero():
    """First frame becomes the baseline -- dynamic output is all zeros."""
    sr = StaticRemover()
    H = np.random.randn(256) + 1j * np.random.randn(256)
    out = sr.remove(H)
    assert np.allclose(out, 0)


def test_static_remover_tracks_changes():
    sr = StaticRemover(alpha=0.5)
    static = np.ones(64, dtype=complex) * 10
    sr.remove(static)

    # add a motion component
    motion = static + np.random.randn(64) * 0.5
    out = sr.remove(motion)
    assert np.abs(out).max() > 0
    assert np.abs(out).max() < 2  # bounded -- not amplifying noise


def test_dfs_not_ready_until_window_full():
    dfs = DFSProfiler(window=32)
    for _ in range(31):
        dfs.push(np.ones(64, dtype=complex))
    assert not dfs.ready()
    dfs.push(np.ones(64, dtype=complex))
    assert dfs.ready()


def test_dfs_shape():
    dfs = DFSProfiler(window=16)
    for t in range(16):
        # slight phase rotation to simulate motion
        phase = np.exp(1j * 0.1 * t)
        dfs.push(np.ones(64, dtype=complex) * phase)
    spec = dfs.extract()
    assert spec.shape == (64, 15)  # (n_sub, window-1)


def test_dfs_detects_constant_velocity():
    """Constant phase rotation -> single DFS peak."""
    dfs = DFSProfiler(window=64)
    freq = 0.15  # normalized Doppler frequency
    for t in range(64):
        H = np.exp(1j * 2 * np.pi * freq * t) * np.ones(32, dtype=complex)
        dfs.push(H)
    spec = dfs.extract()
    # peak should be concentrated, not spread
    peak_energy = spec.max(axis=1).mean()
    total_energy = spec.mean()
    assert peak_energy > 3 * total_energy  # clear peak above noise floor
