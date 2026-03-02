# Benchmarks

All measurements on OpenWrt One (MT7981 Filogic 820, mt7976 adie, OpenWrt 24.10.1,
kernel 6.6.86). Client: laptop with Intel AX1650s, Windows 11, 802.11ax HE80.

## Capture rate

Measured with `CsiDumper` (persistent netlink socket), 4-step init including
`CSI_CTRL_INTERVAL`, 10ms poll interval. Multiple sessions 2026-03-01/02.

| Condition | Meas Hz | Chains/meas | Notes |
|-----------|---------|-------------|-------|
| interval=50ms, strong signal (-68 dBm) | 19.8 | 2.0 | No stimulation |
| interval=50ms, weak signal (-75 dBm) | 6.8 | 3.2 | No stimulation |
| interval=50ms, + UDP stim (200 pps) | 6.5-6.8 | 3.2-4.0 | Same-link stimulation |
| interval=20ms, no stim | 10-19 | varies | Diminishing returns below 30ms |
| No interval set | 0.4-0.6 | varies | Firmware barely buffers |

Stimulation increases chains per measurement (data frames trigger multi-stream
CSI vs single-stream beacons) but does not increase the unique measurement rate
when the stimulation traffic shares the same WiFi link as the capture session.
A dedicated second client on a wired backhaul should decouple these -- untested.

The firmware's `CSI_CTRL_INTERVAL` is the dominant factor. Without it, capture
rate stays below 1 Hz regardless of poll speed. With interval=50ms we see
10-20 Hz depending on signal strength and environment.

100 pps is the sweet spot for stimulation. Going higher doesn't help -- the
firmware ringbuffer fills at roughly the same rate. Bandwidth cost: ~20 KB/s.

## Subcarrier yield

| BW | Expected subs | Measured non-zero subs | Fill rate |
|----|--------------|----------------------|-----------|
| BW20 | 64 | 62-64 | >96% |
| BW40 | 128 | 126-128 | >98% |
| BW80 | 256 | 252-256 | >98% |

No zero-padded guard bands on mt76 -- all subcarriers carry real data.

## Parse throughput

Parsing raw netlink records on the OpenWrt One's ARM Cortex-A53.
Measured 2026-03-01 with `bench/throughput.py`, Python 3.11.14 (musl libc).

| Method | Records/sec | Notes |
|--------|------------|-------|
| dict-based `parse_nla` | ~2,400 | parse each NLA attribute individually |
| fixed-stride I/Q read | **~11,500** | read int16 at `i*8+4`, skip NLA headers |

The `parse_record` function in `_netlink.py` uses the fixed-stride approach
for I/Q (the hot path) and `parse_nla` for the smaller metadata fields.
Combined throughput on real records with BW80/256 subcarriers: **~865 rec/s**.

The daemon needs ~180 rec/s at 20 Hz with 6 chains, so there's ~4.8x headroom
at the measured combined rate. CPython on glibc should push this higher.

## Wire transfer

Frame size at BW80, 6 chains: ~6.2 KB (see [wire_format.md](wire_format.md)).
At 20 fps: ~124 KB/s sustained TCP throughput.
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
