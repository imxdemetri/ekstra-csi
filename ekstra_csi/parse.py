import struct
import array

from .types import ChainCSI, CSIFrame


def _mac(raw: bytes) -> str:
    return ':'.join(f'{b:02x}' for b in raw)


WIRE_HEADER = struct.Struct('<IfIb6sBBBHI')
# seq(I), system_time(f), ts_us(I), rssi(b), ta(6s), snr(B), bw(B),
# complete(B), chain_count(H), reserved(I)

CHAIN_HEADER = struct.Struct('<BBIh')
# rx_ant(B), tx_ant(B), chain_info(I), n_sub(h)


def serialize(frame: CSIFrame) -> bytes:
    """Pack a CSIFrame for TCP transfer. Tight, no JSON."""
    parts = [WIRE_HEADER.pack(
        frame.seq, frame.system_time, frame.timestamp_us, frame.rssi,
        bytes.fromhex(frame.ta.replace(':', '')),
        frame.snr, frame.bw, int(frame.complete), len(frame.chains), 0)]

    for c in frame.chains:
        parts.append(CHAIN_HEADER.pack(c.rx_ant, c.tx_ant, c.chain_info, c.n_sub))
        parts.append(array.array('h', c.i_values).tobytes())
        parts.append(array.array('h', c.q_values).tobytes())

    body = b''.join(parts)
    return struct.pack('<I', len(body)) + body


def deserialize(data: bytes) -> CSIFrame:
    """Unpack one frame from wire bytes (after reading the 4-byte length prefix)."""
    (seq, sys_time, ts_us, rssi, ta_raw, snr, bw, complete,
     chain_count, _) = WIRE_HEADER.unpack_from(data)
    ta = _mac(ta_raw)

    off = WIRE_HEADER.size
    chains = []
    for _ in range(chain_count):
        rx, tx, ci, n_sub = CHAIN_HEADER.unpack_from(data, off)
        off += CHAIN_HEADER.size
        sub_bytes = n_sub * 2
        i_vals = list(array.array('h', data[off:off + sub_bytes]))
        off += sub_bytes
        q_vals = list(array.array('h', data[off:off + sub_bytes]))
        off += sub_bytes
        chains.append(ChainCSI(rx, tx, ci, i_vals, q_vals))

    return CSIFrame(
        timestamp_us=ts_us, system_time=sys_time, seq=seq,
        ta=ta, rssi=rssi, snr=snr, bw=bw, chains=chains,
        complete=bool(complete))
