import pytest
from ekstra_csi.types import CSIFrame, ChainCSI
from ekstra_csi import parse


def _make_frame(**kw):
    defaults = dict(
        timestamp_us=123456, system_time=1700000000.0, seq=1,
        ta='aa:bb:cc:dd:ee:ff', rssi=-45, snr=25, bw=2,
        chains=[ChainCSI(0, 0, 0x8001, list(range(256)), list(range(256)))],
        complete=True)
    defaults.update(kw)
    return CSIFrame(**defaults)


def test_roundtrip_preserves_all_fields():
    frame = _make_frame()
    data = parse.serialize(frame)
    got = parse.deserialize(data[4:])  # skip length prefix
    assert got.seq == frame.seq
    assert got.ta == frame.ta
    assert got.rssi == frame.rssi
    assert got.snr == frame.snr
    assert got.bw == frame.bw
    assert got.n_chains == 1
    assert got.chains[0].i_values == frame.chains[0].i_values


def test_roundtrip_6_chains():
    """Stimulated BW80 capture: 6 chains, 256 subcarriers each."""
    chains = [ChainCSI(rx, 0, 0x8000 if rx == 5 else rx,
                        [i + rx * 10 for i in range(256)],
                        [i - rx * 10 for i in range(256)])
              for rx in range(6)]
    frame = _make_frame(chains=chains)
    data = parse.serialize(frame)
    got = parse.deserialize(data[4:])
    assert got.n_chains == 6
    assert got.n_sub == 256
    for i in range(6):
        assert got.chains[i].rx_ant == i
        assert len(got.chains[i].i_values) == 256


def test_bw20_has_64_subcarriers():
    chains = [ChainCSI(0, 0, 0x8000, list(range(64)), list(range(64)))]
    frame = _make_frame(bw=0, chains=chains)
    data = parse.serialize(frame)
    got = parse.deserialize(data[4:])
    assert got.bw == 0
    assert got.n_sub == 64


def test_negative_rssi_preserved():
    """RSSI is signed -- beacon from far AP might be -80 dBm."""
    frame = _make_frame(rssi=-80)
    data = parse.serialize(frame)
    got = parse.deserialize(data[4:])
    assert got.rssi == -80


def test_empty_chains_survives():
    """Defensive: if firmware sends zero chains (seen once during cold boot)."""
    frame = _make_frame(chains=[])
    data = parse.serialize(frame)
    got = parse.deserialize(data[4:])
    assert got.n_chains == 0
    assert got.n_sub == 0
