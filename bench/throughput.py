"""Measure parse throughput on the router. Run with: python -m bench.throughput"""
import time
import struct
from ekstra_csi._netlink import parse_nla, parse_record

# Synthetic record that matches real netlink layout.
# For real benchmarks, replace with captured binary from the daemon.

def make_fake_record(n_sub=256):
    d = {}
    d[3] = struct.pack('<I', 12345)      # ts
    d[4] = struct.pack('b', -45)         # rssi
    d[5] = struct.pack('B', 25)          # snr
    d[6] = struct.pack('B', 2)           # bw
    d[17] = struct.pack('<H', 0)         # tx_ant
    d[18] = struct.pack('B', 0)          # rx_ant
    d[19] = struct.pack('<I', 0x8001)    # chain_info

    # ta: 6 sub-attrs at 8B stride
    ta_bytes = b''
    for byte_val in [0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff]:
        ta_bytes += struct.pack('<HH', 6, 0) + struct.pack('B', byte_val) + b'\x00' * 3
    d[8] = ta_bytes

    # I/Q: N sub-attrs at 8B stride
    for attr_id in [9, 10]:
        raw = b''
        for i in range(n_sub):
            raw += struct.pack('<HHh', 6, i, i if attr_id == 9 else -i) + b'\x00' * 2
        d[attr_id] = raw

    return d


if __name__ == '__main__':
    rec = make_fake_record(256)
    N = 10000

    t0 = time.perf_counter()
    for _ in range(N):
        parse_record(rec)
    elapsed = time.perf_counter() - t0

    print(f"parse_record: {N/elapsed:.0f} records/sec ({elapsed/N*1e6:.0f} us/record)")
