# Benchmarks

All measurements on OpenWrt One (MT7981 Filogic 820, mt7976 adie, OpenWrt 24.10.1, kernel 6.6.86).
Client: laptop with Intel AX1650s, Windows 11, 802.11ax HE80.

## Capture rate

| Condition | Hz | Mean chains | Min chains |
|-----------|-----|-------------|------------|
| Beacon only | 16.6 | 3.6 | 3 |
| 50 pps UDP | 24.1 | 5.1 | 4 |
| 100 pps UDP | 30.1 | 5.9 | 5 |
| 200 pps UDP | 30.3 | 5.9 | 5 |

100 pps is the sweet spot. Going higher doesn't help -- the firmware
ringbuffer fills at roughly the same rate. Bandwidth cost: ~20 KB/s.

## Subcarrier yield

| BW | Expected subs | Measured non-zero subs | Fill rate |
|----|--------------|----------------------|-----------|
| BW20 | 64 | 62-64 | >96% |
| BW40 | 128 | 126-128 | >98% |
| BW80 | 256 | 252-256 | >98% |

No zero-padded guard bands on mt76 -- all subcarriers carry real data.

## Parse throughput

Parsing raw netlink records on the OpenWrt One's ARM Cortex-A53 (measured 2026-03-01):

| Method | Records/sec | Notes |
|--------|------------|-------|
| dict-based parse | ~2,400 | parse_nla for each attribute |
| fixed-stride I/Q (`parse_record`) | **865** | 8-byte stride, skip inner NLA parse |

Measured 865 rec/s on Python 3.11.14 (musl libc, no JIT). The daemon needs
~180 rec/s at 30 Hz with 6 chains, so there's ~4.8x headroom even at the
measured rate. CPython on glibc or with struct caching should push this higher.

## Wire transfer

Frame size at BW80, 6 chains: ~6.2 KB.
At 30 fps: ~186 KB/s sustained TCP throughput.
Round-trip latency (daemon -> client deserialize): <2ms on local LAN.

## SNR vs phase stability

| SNR (dB) | Phase std across subcarriers (rad) |
|----------|-----------------------------------|
| 30+ | 0.15 - 0.25 |
| 20-30 | 0.25 - 0.40 |
| 15-20 | 0.40 - 0.80 |
| <15 | >1.5 (noise-dominated) |

We default to SNR_FLOOR=15 in the filter. Below this, the phase
information is unreliable for sensing.
