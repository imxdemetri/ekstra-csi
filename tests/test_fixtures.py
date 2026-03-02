"""Load every .bin fixture and verify it round-trips through parse."""
import os
import struct
import pytest
from ekstra_csi.parse import deserialize, serialize

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), 'rb') as f:
        return f.read()


FIXTURES = [
    ("bw20_beacon.bin", {"bw": 0, "n_chains": 3, "n_sub": 64}),
    ("bw80_data.bin", {"bw": 2, "n_chains": 6, "n_sub": 256}),
    ("low_snr.bin", {"bw": 0, "n_chains": 1, "n_sub": 64}),
    ("incomplete_group.bin", {"bw": 1, "n_chains": 3, "n_sub": 128}),
]


@pytest.mark.parametrize("filename,expected", FIXTURES)
def test_fixture_deserialize(filename, expected):
    raw = load_fixture(filename)
    length = struct.unpack('<I', raw[:4])[0]
    frame = deserialize(raw[4:4 + length])
    assert frame.bw == expected["bw"]
    assert frame.n_chains == expected["n_chains"]
    assert frame.n_sub == expected["n_sub"]
    assert len(frame.ta) == 17  # "xx:xx:xx:xx:xx:xx"


@pytest.mark.parametrize("filename,expected", FIXTURES)
def test_fixture_roundtrip(filename, expected):
    raw = load_fixture(filename)
    length = struct.unpack('<I', raw[:4])[0]
    frame = deserialize(raw[4:4 + length])
    repack = serialize(frame)
    assert repack == raw[:4 + length]


def test_multi_device_fixture():
    """multi_device.bin has 6 frames from 2 MACs."""
    raw = load_fixture("multi_device.bin")
    macs = set()
    offset = 0
    count = 0
    while offset < len(raw):
        length = struct.unpack_from('<I', raw, offset)[0]
        frame = deserialize(raw[offset + 4:offset + 4 + length])
        macs.add(frame.ta)
        count += 1
        offset += 4 + length
    assert count == 6
    assert len(macs) == 2


def test_low_snr_value():
    raw = load_fixture("low_snr.bin")
    length = struct.unpack('<I', raw[:4])[0]
    frame = deserialize(raw[4:4 + length])
    assert frame.snr == 8
    assert frame.rssi == -82


def test_incomplete_flag():
    raw = load_fixture("incomplete_group.bin")
    length = struct.unpack('<I', raw[:4])[0]
    frame = deserialize(raw[4:4 + length])
    assert frame.complete is False
