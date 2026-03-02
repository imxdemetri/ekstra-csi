import pytest
from ekstra_csi.types import CSIFrame, ChainCSI


def test_chain_n_sub():
    c = ChainCSI(0, 0, 0, list(range(256)), list(range(256)))
    assert c.n_sub == 256


def test_frame_bw_name():
    c = [ChainCSI(0, 0, 0, [0]*64, [0]*64)]
    f = CSIFrame(0, 0.0, 1, 'aa:bb:cc:dd:ee:ff', -40, 25, 2, c)
    assert f.bw_name == 'BW80'


def test_frame_repr_is_useful():
    c = [ChainCSI(0, 0, 0, [0]*256, [0]*256)]
    f = CSIFrame(0, 0.0, 42, 'aa:bb:cc:dd:ee:ff', -45, 25, 2, c)
    r = repr(f)
    assert 'seq=42' in r
    assert 'BW80' in r
    assert '256sc' in r


def test_to_complex_shape():
    np = pytest.importorskip('numpy')
    chains = [ChainCSI(i, 0, 0, list(range(128)), list(range(128))) for i in range(4)]
    f = CSIFrame(0, 0.0, 1, 'aa:bb:cc:dd:ee:ff', -40, 25, 1, chains)
    H = f.to_complex()
    assert H.shape == (4, 128)
    assert H.dtype == np.complex64  # float32 is enough for int16 I/Q


def test_to_complex_needs_numpy():
    """If numpy isn't installed, to_complex raises with install hint."""
    import ekstra_csi.types as mod
    old = mod.HAS_NP
    mod.HAS_NP = False
    try:
        c = [ChainCSI(0, 0, 0, [1, 2], [3, 4])]
        f = CSIFrame(0, 0.0, 1, 'aa:bb:cc:dd:ee:ff', -40, 25, 0, c)
        with pytest.raises(RuntimeError, match="numpy required"):
            f.to_complex()
    finally:
        mod.HAS_NP = old
