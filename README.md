# ekstra-csi

Full-metadata CSI extraction for MediaTek mt76 chipsets on OpenWrt.

MtkCSIdump gets CSI off the router but strips the metadata -- no source MAC,
no RSSI, no SNR, no packet sequence numbers. This library talks directly to
the mt76 kernel via netlink and keeps everything the driver provides.

## What you get that MtkCSIdump doesn't

| Field | MtkCSIdump | ekstra-csi |
|-------|-----------|------------|
| Complex I/Q | yes | yes |
| Source MAC | no | yes |
| RSSI | no | yes |
| SNR | no | yes |
| Packet sequence | no | yes |
| Chain grouping | timestamp heuristic | exact (pkt_sn + chain_info) |
| BW80 reassembly | manual | automatic |
| Per-device routing | no | yes |
| Traffic stimulation | no | built-in |

## Why it matters

Without source MAC, every device's CSI blends into one stream. You can't
tell if a signal change came from someone walking past the TV or waving
at the laptop. With per-device routing, each WiFi link becomes an
independent sensor.

With traffic stimulation, beacon-only capture (~2 chains) becomes full
data-frame capture (3-6 chains) -- up to a 3x improvement in spatial
information per measurement at minimal bandwidth cost.

## Requirements

- OpenWrt router with mt76 CSI patches **compiled into the kernel module**
  (tested on OpenWrt One with [mt76-csi-patches](https://github.com/MtkWifiRev/mt76-csi-patches))
- Python 3.10+
- Root access on the router (netlink requires it)

> **Note**: Stock OpenWrt firmware does not include the CSI buffering layer.
> The mt76 driver must be patched to relay CSI events from the firmware MCU
> to userspace via the nl80211 vendor dump interface. See
> [docs/hardware_validation.md](docs/hardware_validation.md) for details.

## Quick start

On the router:

```
pip install ekstra-csi
ekstra-csi-daemon --iface phy0 --port 5500
```

On your laptop:

```python
from ekstra_csi import CSIClient

for frame in CSIClient("192.168.1.1"):
    print(f"{frame.ta}  rssi={frame.rssi}  snr={frame.snr}  "
          f"{frame.bw_name} {frame.n_chains}ch x {frame.n_sub}sc")
```

## Per-device sensing

```python
from ekstra_csi import CSIClient
from ekstra_csi.demux import DeviceDemux

demux = DeviceDemux(targets=["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"])
for frame in CSIClient("192.168.1.1"):
    demux.push(frame)
```

## Traffic stimulation

```python
from ekstra_csi import CSIClient, TrafficStimulator

with TrafficStimulator("192.168.1.1"):
    for frame in CSIClient("192.168.1.1"):
        # stimulation increases chains per measurement (more spatial streams)
        H = frame.to_complex()  # (n_chains, n_sub) complex64
```

## Preprocessing

```python
from ekstra_csi.preprocessing import StaticRemover, DFSProfiler

sr = StaticRemover(alpha=0.95)
dfs = DFSProfiler(window=64)

for frame in client:
    H = frame.to_complex()
    dynamic = sr.remove(H[0])  # static environment removed
    dfs.push(dynamic)
    if dfs.ready():
        spectrogram = dfs.extract()  # Doppler velocity spectrogram
```

## Hardware

Tested on the [OpenWrt One](https://openwrt.org/toh/openwrt/one) (MT7981B SoC,
mt7976 radio) running OpenWrt 24.10 with
[mt76-csi-patches](https://github.com/MtkWifiRev/mt76-csi-patches).
Should work on any mt76 device with the CSI vendor extension: GL.iNet,
Xiaomi, TP-Link Archer series, and other OpenWrt-supported routers.

BW80 gives 256 real subcarriers across up to 6 chains = 1,536 complex
features per measurement. Measured 7-20 Hz depending on signal conditions,
with 256 subcarriers confirmed non-zero in every capture. More than Intel
5300 (30 subs x 3 chains = 90) or Nexmon on BCM4366c0 (256 subs x 1
chain = 256). See [docs/hardware_validation.md](docs/hardware_validation.md)
for detailed benchmarks.

## Citation

If you use ekstra-csi in academic work, cite:

```bibtex
@inproceedings{ekstra-csi,
  title     = {ekstra-csi: Full-Metadata Channel State Information Extraction
               for MediaTek mt76 WiFi Chipsets},
  author    = {Rodriguez, Demetri},
  year      = {2026},
}
```

## License

Apache-2.0. Academic use requires citation (see LICENSE).
