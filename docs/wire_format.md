# Wire Format

Binary protocol for daemon-to-client CSI streaming over TCP.
See `ekstra_csi/parse.py` for the reference implementation.

## Design choices

JSON would be ~40x larger per frame and require escaping the I/Q arrays.
At BW80 with 6 chains that's 3,072 int16 values per measurement -- JSON
encoding that at 20+ Hz saturates even a local LAN. Binary with struct
packing keeps each frame under 7 KB and parse time under 200 us.

I looked at protobuf and msgpack but both add dependencies. The daemon
runs on the router where `pip install` is painful. Stdlib-only
(`struct` + `array`) means zero setup.

## Frame layout

Every frame on the wire is length-prefixed:

```
[4 bytes: body_length (uint32 LE)]
[body_length bytes: frame body]
```

### Frame header (28 bytes)

```
offset  size  type      field         notes
------  ----  --------  -----------   -----
 0      4     uint32    seq           packet sequence number from firmware
 4      4     float32   system_time   time.time() on the router when captured
 8      4     uint32    timestamp_us  firmware monotonic clock (microseconds)
12      1     int8      rssi          signed, dBm (typically -30 to -90)
13      6     bytes     ta            transmitter MAC, big-endian byte order
19      1     uint8     snr           PHY-level estimate, dB
20      1     uint8     bw            0=BW20, 1=BW40, 2=BW80, 3=BW160
21      1     uint8     complete      1 if all chains in this group received
22      2     uint16    chain_count   number of chain records following
24      4     uint32    reserved      zero (alignment + future use)
```

Struct format string: `<IfIb6sBBBHI` (little-endian, 28 bytes).

The `ta` field is the raw 6-byte MAC in network order. To display:
`':'.join(f'{b:02x}' for b in ta_bytes)`.

### Chain record (8 + 4*n_sub bytes each)

Repeated `chain_count` times immediately after the header:

```
offset  size  type      field         notes
------  ----  --------  -----------   -----
 0      1     uint8     rx_ant        receive antenna index
 1      1     uint8     tx_ant        transmit antenna index
 2      4     uint32    chain_info    firmware chain descriptor; BIT(15) = last in group
 6      2     int16     n_sub         number of subcarriers (64/128/256)
 8      n*2   int16[]   i_values      in-phase, one per subcarrier
 8+n*2  n*2   int16[]   q_values      quadrature, one per subcarrier
```

Struct format string for the chain header: `<BBIh` (8 bytes).
I/Q arrays are packed as raw `array('h', values).tobytes()` -- no
per-element headers, just contiguous little-endian int16.

### Total frame size

For BW80 with N chains:

```
4 (length prefix)
+ 28 (header)
+ N * (8 + 256*2 + 256*2)  = N * 1032
```

Typical sizes:
- BW80, 2 chains: 2,096 bytes
- BW80, 4 chains: 4,160 bytes
- BW80, 6 chains: 6,224 bytes

## Netlink record format (kernel to daemon)

The daemon reads CSI from the kernel via nl80211 vendor dump commands.
Each record arrives as a nested NLA (netlink attribute) structure inside
a vendor response message.

### Vendor dump request

```
nl80211 header:
  cmd = NL80211_CMD_VENDOR (103)
  attrs:
    NL80211_ATTR_IFINDEX  (3)   = phy interface index
    NL80211_ATTR_VENDOR_ID (195) = 0x0ce7 (MediaTek OUI)
    NL80211_ATTR_VENDOR_SUBCMD (196) = 0xc2 (CSI subcommand)
    NL80211_ATTR_VENDOR_DATA (197, nested):
      CSI_CTRL_DUMP_NUM (8) = count (uint16, how many records to pull)
```

### Vendor dump response

Each response message contains one CSI record wrapped in:
`NL80211_ATTR_VENDOR_DATA -> CSI_CTRL_DATA (9, nested) -> per-field NLAs`

The per-field attributes inside CSI_CTRL_DATA:

```
NLA ID  size    type      field          kernel source reference
------  ------  --------  -----------    -------------------------
 3      4       uint32    timestamp_us   mt76_connac_mcu.c event buffer
 4      1       int8      rssi           signed dBm from PHY
 5      1       uint8     snr            PHY-level SNR estimate
 6      1       uint8     bw             0/1/2/3 for 20/40/80/160 MHz
 8      48      nested    ta             6-byte MAC, each byte wrapped in own NLA
 9      N*8     nested    i_values       int16 per subcarrier, 8-byte stride
10      N*8     nested    q_values       same layout as I
17      2       uint16    tx_ant         transmit antenna index
18      1       uint8     rx_ant         receive antenna index
19      4       uint32    chain_info     BIT(15) marks last chain in measurement
```

### NLA attribute numbering

These IDs are not stable across firmware versions. Our OpenWrt 24.10 build
uses DUMP_NUM=8, DATA=9. Upstream mt76-vendor code expects 9 and 10.
The `_netlink.discover_attrs()` function probes both and picks whichever
returns actual data.

### I/Q sub-attribute layout

The firmware wraps each subcarrier value in its own NLA sub-attribute:

```
offset  size  content
------  ----  -------
 0      2     length = 6
 2      2     type = subcarrier index
 4      2     int16 value (little-endian)
 6      2     padding (zero)
```

Fixed 8-byte stride. For BW80 (256 subcarriers), the I attribute is
256 * 8 = 2,048 bytes. Rather than parsing 256 individual NLAs, we
read `struct.unpack_from('<h', raw, i*8+4)` directly -- same result,
3x faster on the ARM core.

### Transmitter MAC sub-attribute layout

The TA attribute nests each MAC byte separately:

```
byte 0:  [len=6][type=0][value][pad]    8 bytes
byte 1:  [len=6][type=1][value][pad]    8 bytes
...
byte 5:  [len=6][type=5][value][pad]    8 bytes
```

Total: 48 bytes to encode 6 bytes of MAC. We extract with
`raw[i*8 + 4]` for i in 0..5.

### Chain grouping

Multiple chains from the same WiFi frame share a `timestamp_us` value.
The `chain_info` field's BIT(15) signals the last chain in a group.
A complete BW80 measurement with 2x2 MIMO produces 4 chain records
(2 TX x 2 RX segments). With traffic stimulation forcing HE-NSS 2,
we see up to 6 chains per measurement.

## Comparison: CSIdump vs ekstra-csi

CSIdump (the MtkCSIdump binary) reads from the same kernel buffer via
the same nl80211 vendor dump interface. Its `md_csi_dump_cb` callback
extracts the I/Q arrays but discards all other attributes before
sending raw subcarrier data over UDP.

Verified on a live OpenWrt One (2026-03-02): ekstra-csi's netlink dump
returns 14 NLA attributes per record (IDs 2-19). CSIdump's UDP output
contains only the I/Q values. The kernel provides the metadata -- CSIdump
just doesn't forward it.
