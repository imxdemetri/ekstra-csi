# mt76 CSI internals

Notes from reading the kernel source, mt76-vendor, and strace output.

## How CSI gets from the air to userspace

1. mt7915e hardware captures frames and computes CSI in the PHY
2. Firmware packages CSI into MCU events (MCU_EVENT = 0x12, tag CSI_DATA = 0x04)
3. mt76 driver receives via `mt76_connac_mcu_parse_tx_resource_req` and
   pushes to a per-phy ringbuffer (64 entries)
4. Userspace reads via nl80211 vendor command (OUI 0x0ce7, subcmd 0xc2)
5. Each vendor dump returns up to N records (we ask for 20)

## Record format

Each netlink record contains nested attributes. The attribute IDs aren't
stable across firmware builds -- `_netlink.discover_attrs()` probes for
the right numbering.

Key attributes per record:
- **ts** (uint32): firmware monotonic timestamp, microseconds
- **rssi** (int8): per-frame RSSI in dBm
- **snr** (uint8): per-frame SNR in dB (PHY-level estimate)
- **bw** (uint8): 0=BW20, 1=BW40, 2=BW80, 3=BW160
- **ta** (nested, 6 bytes): transmitter address -- this is the critical one
  that MtkCSIdump doesn't expose
- **rx_ant**, **tx_ant**: antenna indices for this chain
- **chain_info** (uint32): BIT(15) marks the last chain of a measurement
- **I**, **Q** (nested, N x int16): complex subcarrier values

## Chain grouping

The firmware sends one record per (rx_ant, tx_ant) pair. A complete BW80
measurement on the OpenWrt One produces up to 6 records (2 TX x 3 RX).
Records sharing the same `ts` belong to the same measurement.

MtkCSIdump groups by timestamp proximity (heuristic -- breaks when two
measurements arrive within the same ms). We use `chain_info` BIT(15) to
detect the last chain definitively.

## Traffic dependency

Beacons are single-stream -- the AP doesn't use spatial multiplexing for
management frames. So beacon-CSI only captures 3-4 chains depending on
the RX antenna configuration.

Data frames with HE (802.11ax) NSS=2 trigger CSI computation on all RX
chains for both spatial streams. Sending UDP traffic from the laptop to
the router forces data-frame transmission, which gets us all 6 chains.

Measured on OpenWrt One:
- Beacon-only: 16.6 Hz, 3.6 mean chains
- With 100 pps UDP stimulation: 30.1 Hz, 5.9 mean chains

## Attribute ID instability

The CSI netlink attributes don't have stable IDs across builds.
Our firmware (OpenWrt 24.10.1, kernel 6.6) uses DUMP_NUM=8, DATA=9.
The upstream mt76-vendor code expects DUMP_NUM=9, DATA=10.

`discover_attrs()` tries both and picks whichever returns valid records.

## BW80 specifics

BW80 produces 256 subcarriers per chain. Unlike some Broadcom firmwares,
the mt76 implementation fills all 256 -- no zero-padded guard bands. We
verified this by checking that the first 64 and last 64 subcarriers have
non-zero I/Q values in captured data.

Some older firmware versions split BW80 into segments that need manual
reassembly. We haven't seen this on MT7981 but the flag exists in
`FirmwareProfile.needs_segment_reassembly`.
