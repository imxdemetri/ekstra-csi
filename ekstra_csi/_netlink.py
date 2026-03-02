# Netlink vendor protocol for mt76 CSI.
#
# None of this is documented. I pieced it together from:
# - strace output of mt76-vendor and CSIdump
# - reading mt76_connac_mcu.c in the kernel source
# - trial and error with raw netlink sockets
# - a hex editor and a lot of patience
#
# If you're reading this to understand the protocol, start with do_dump()
# and work backwards. The init sequence is in init_csi().
#
# Stdlib only -- this runs on the router where there's no pip.

import struct
import socket
import os
import time
import logging

log = logging.getLogger(__name__)

# --- kernel constants ---
# I pulled these from the header files because the Python netlink
# bindings on OpenWrt are either missing or broken.

NETLINK_GENERIC = 16                 # linux/netlink.h
NLA_F_NESTED = 0x8000                # without this flag on VENDOR_DATA, the kernel
                                     # silently drops the whole command. nowhere in the
                                     # docs. cost me two weeks.

NL80211_CMD_VENDOR = 103             # nl80211.h
NL80211_ATTR_IFINDEX = 3
NL80211_ATTR_VENDOR_ID = 195
NL80211_ATTR_VENDOR_SUBCMD = 196
NL80211_ATTR_VENDOR_DATA = 197

MTK_VENDOR_ID = 0x0ce7               # MediaTek's OUI -- from mt76-vendor source
MTK_CSI_SUBCMD = 0xc2                # the CSI-specific vendor subcommand

CTRL_CMD_GETFAMILY = 3               # genetlink.h
CTRL_ATTR_FAMILY_NAME = 2
CTRL_ATTR_FAMILY_ID = 1
GENL_ID_CTRL = 0x10

NLMSG_ERROR = 2                      # netlink.h
NLMSG_DONE = 3
NLM_F_REQUEST = 0x01
NLM_F_DUMP = 0x300
NLM_F_ACK = 0x04

# --- CSI control attributes ---
# These are the attribute IDs inside the vendor data payload.
# Here's the thing: they're not stable. Our OpenWrt 24.10 build on the
# OpenWrt One uses DUMP_NUM=8, DATA=9. But I've seen upstream mt76-vendor
# code that expects 9 and 10. Different kernel patch versions shift the
# enum. discover_attrs() tries both and picks whatever works.
CSI_CTRL_MODE = 2
CSI_CTRL_CFG_TYPE = 3
CSI_CTRL_CFG_VAL1 = 4
CSI_CTRL_CFG_VAL2 = 5
CSI_CTRL_INTERVAL = 7
CSI_CTRL_DUMP_NUM = 8
CSI_CTRL_DATA = 9

# --- per-record attributes inside CSI_CTRL_DATA ---
# Found these by dumping raw NLA payloads and comparing field sizes
# against what mt76_connac_mcu.c packs into the MCU event buffer.
CSI_DATA_TS = 3          # uint32 -- firmware monotonic clock, microseconds
CSI_DATA_RSSI = 4        # int8 -- signed, dBm
CSI_DATA_SNR = 5         # uint8 -- PHY-level estimate, dB
CSI_DATA_BW = 6          # uint8 -- 0/1/2/3 for 20/40/80/160
CSI_DATA_TA = 8          # nested -- 6 bytes of MAC, but each byte is its own NLA (why?)
CSI_DATA_I = 9           # nested -- N int16 values, one per subcarrier
CSI_DATA_Q = 10          # same layout as I
CSI_DATA_TX_ANT = 17     # uint16
CSI_DATA_RX_ANT = 18     # uint8
CSI_DATA_CHAIN_INFO = 19 # uint32 -- BIT(15) means "last chain in this measurement"


# --- NLA builders ---
# Netlink attributes are TLV with 4-byte alignment.
# I wrote these by hand because ctypes felt like overkill for
# something this small, and the struct module is always there.

def nla(attr_type, data):
    length = 4 + len(data)
    pad = (4 - (length % 4)) % 4
    return struct.pack('<HH', length, attr_type) + data + b'\x00' * pad

def nla_u8(t, v):  return nla(t, struct.pack('B', v))
def nla_u16(t, v): return nla(t, struct.pack('<H', v))
def nla_u32(t, v): return nla(t, struct.pack('<I', v))
def nla_nested(t, payload): return nla(t | NLA_F_NESTED, payload)


def parse_nla(data, offset=0):
    attrs = {}
    while offset < len(data) - 3:
        nla_len, nla_type = struct.unpack_from('<HH', data, offset)
        if nla_len < 4:
            break
        attrs[nla_type & 0x7FFF] = data[offset + 4:offset + nla_len]
        offset += (nla_len + 3) & ~3
    return attrs


def _nlhdr(length, msg_type, flags, seq, pid):
    return struct.pack('<IHHII', length, msg_type, flags, seq, pid)


def resolve_nl80211(timeout=3.0):
    """Ask the kernel for the nl80211 family ID. Has to happen once per boot."""
    pid = os.getpid()
    s = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
    s.bind((pid, 0))
    s.settimeout(timeout)

    seq = 1
    payload = struct.pack('BBH', CTRL_CMD_GETFAMILY, 1, 0)
    s.send(_nlhdr(16 + len(payload), GENL_ID_CTRL,
                  NLM_F_REQUEST | NLM_F_DUMP, seq, pid) + payload)

    fam_id = None
    try:
        while True:
            data = s.recv(65535)
            off = 0
            while off < len(data):
                if off + 16 > len(data): break
                nl_len, nl_type = struct.unpack_from('<IH', data, off)
                if nl_len < 16: break
                if nl_type == NLMSG_DONE:
                    s.close()
                    if fam_id is None:
                        raise RuntimeError("nl80211 not found -- is cfg80211 loaded?")
                    return fam_id
                if nl_type == GENL_ID_CTRL:
                    a = parse_nla(data[off:off + nl_len], 20)
                    if a.get(CTRL_ATTR_FAMILY_NAME, b'').rstrip(b'\x00') == b'nl80211':
                        fam_id = struct.unpack('<H', a[CTRL_ATTR_FAMILY_ID][:2])[0]
                off += (nl_len + 3) & ~3
    except socket.timeout:
        pass
    finally:
        s.close()
    if fam_id is None:
        raise RuntimeError("nl80211 resolution timed out -- is the WiFi driver up?")
    return fam_id


def send_vendor_cmd(fam, ifindex, vendor_payload, timeout=3.0):
    pid = os.getpid()
    s = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
    s.bind((pid, 0))
    s.settimeout(timeout)

    seq = int(time.time()) & 0xFFFFFFFF
    genl = struct.pack('BBH', NL80211_CMD_VENDOR, 0, 0)
    attrs = (nla_u32(NL80211_ATTR_IFINDEX, ifindex) +
             nla_u32(NL80211_ATTR_VENDOR_ID, MTK_VENDOR_ID) +
             nla_u32(NL80211_ATTR_VENDOR_SUBCMD, MTK_CSI_SUBCMD) +
             nla_nested(NL80211_ATTR_VENDOR_DATA, vendor_payload))
    body = genl + attrs
    s.send(_nlhdr(16 + len(body), fam, NLM_F_REQUEST | NLM_F_ACK, seq, pid) + body)
    try:
        data = s.recv(65535)
        if len(data) >= 20:
            err = struct.unpack_from('<i', data, 16)[0]
            s.close()
            return err
    except socket.timeout:
        pass
    s.close()
    return -999


def init_csi(fam, ifindex, interval_ms=50):
    """Four vendor commands to start CSI capture.

    I found this sequence by strace-ing mt76-vendor's "csi set" command.
    The order is: configure firmware, enable per-frame mode, enable CSI,
    then set capture interval. If you swap steps 2 and 3 the firmware
    just ignores you silently.

    The 0x22 in step 1 is the frame type bitmask: data + probe response.
    Tried 0xFF first (everything) and got flooded with beacons.

    The interval step is critical for capture rate. Without it, the
    firmware buffers slowly (~0.4 Hz at 10ms polling). With interval=50ms
    we measured 25 Hz; interval=20ms gives ~19 Hz. The firmware seems to
    use the interval as a minimum spacing between CSI captures.
    """
    steps = [
        (2, 3, 0, 0x22, "firmware config"),
        (2, 9, 1, 0,    "per-frame capture"),
        (1, 0, 0, 0,    "CSI enable"),
    ]
    for mode, ctype, val1, val2, desc in steps:
        inner = (nla_u8(CSI_CTRL_MODE, mode) +
                 nla_u8(CSI_CTRL_CFG_TYPE, ctype) +
                 nla_u8(CSI_CTRL_CFG_VAL1, val1) +
                 nla_u32(CSI_CTRL_CFG_VAL2, val2))
        err = send_vendor_cmd(fam, ifindex, nla_nested(1, inner))
        if err != 0:
            log.error("init_csi '%s' failed: err=%d", desc, err)
            return desc
        log.info("init_csi '%s': ok", desc)

    # Set capture interval -- without this the firmware barely produces records
    inner = (nla_u8(CSI_CTRL_MODE, 2) +
             nla_u8(CSI_CTRL_CFG_TYPE, CSI_CTRL_INTERVAL) +
             nla_u8(CSI_CTRL_CFG_VAL1, interval_ms) +
             nla_u32(CSI_CTRL_CFG_VAL2, 0))
    err = send_vendor_cmd(fam, ifindex, nla_nested(1, inner))
    if err != 0:
        log.warning("set interval=%dms failed: err=%d (non-fatal)", interval_ms, err)
    else:
        log.info("init_csi 'interval=%dms': ok", interval_ms)

    return None


def disable_csi(fam, ifindex):
    inner = nla_u8(CSI_CTRL_MODE, 0)
    return send_vendor_cmd(fam, ifindex, nla_nested(1, inner))


def do_dump(fam, ifindex, count=20, timeout=2.0,
            dump_num=CSI_CTRL_DUMP_NUM, data_attr=CSI_CTRL_DATA):
    """Pull buffered CSI records from the firmware via vendor netlink.

    This is the hot path. On a good day it returns up to `count` records.
    Each record is one (rx_ant, tx_ant) chain -- you need the ChainGrouper
    to assemble them into complete measurements.

    Important: the kernel sends NLMSG_ERROR with err=0 as an ACK before
    the data records. Earlier versions of this code treated any NLMSG_ERROR
    as terminal and broke out -- missing all the actual CSI data that follows.
    Fixed by skipping err=0 ACKs and only terminating on real errors.

    A GETFAMILY handshake is required for the dump socket to receive vendor
    responses. This function creates a fresh socket per call, which works
    but adds overhead. For sustained capture loops, use CsiDumper instead --
    it holds a persistent socket with one GETFAMILY handshake and reuses it
    across dumps.
    """
    pid = os.getpid()
    seq = int(time.time() * 1000) & 0xFFFFFFFF

    s = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
    s.bind((pid, 0))
    s.settimeout(timeout)

    # GETFAMILY handshake -- required for the kernel to route vendor dump
    # responses back to this socket.
    gf_payload = struct.pack('BBH', CTRL_CMD_GETFAMILY, 1, 0)
    s.send(_nlhdr(16 + len(gf_payload), GENL_ID_CTRL,
                  NLM_F_REQUEST | NLM_F_DUMP, seq, pid) + gf_payload)
    seq += 1
    try:
        while True:
            gf_data = s.recv(65535)
            off = 0
            while off < len(gf_data):
                if off + 16 > len(gf_data): break
                nl_len, nl_type = struct.unpack_from('<IH', gf_data, off)
                if nl_len < 16: break
                if nl_type == NLMSG_DONE: raise StopIteration
                off += (nl_len + 3) & ~3
    except (StopIteration, socket.timeout):
        pass

    genl = struct.pack('BBH', NL80211_CMD_VENDOR, 0, 0)
    attrs = (nla_u32(NL80211_ATTR_IFINDEX, ifindex) +
             nla_u32(NL80211_ATTR_VENDOR_ID, MTK_VENDOR_ID) +
             nla_u32(NL80211_ATTR_VENDOR_SUBCMD, MTK_CSI_SUBCMD) +
             nla_nested(NL80211_ATTR_VENDOR_DATA, nla_u16(dump_num, count)))
    body = genl + attrs
    s.send(_nlhdr(16 + len(body), fam, NLM_F_REQUEST | NLM_F_DUMP, seq, pid) + body)

    records = []
    done = False
    while not done:
        try:
            data = s.recv(65535)
            off = 0
            while off < len(data):
                if off + 16 > len(data): break
                nl_len, nl_type = struct.unpack_from('<IH', data, off)
                if nl_len < 16: break
                if nl_type == NLMSG_ERROR:
                    # err=0 is just an ACK -- skip it and keep reading.
                    # Only terminate on real errors (err != 0).
                    if off + 20 <= len(data):
                        err = struct.unpack_from('<i', data, off + 16)[0]
                        if err != 0:
                            done = True
                    off += (nl_len + 3) & ~3
                    continue
                if nl_type == NLMSG_DONE:
                    done = True; break
                if nl_type == fam:
                    top = parse_nla(data[off:off + nl_len], 20)
                    vd = top.get(NL80211_ATTR_VENDOR_DATA)
                    if vd:
                        ctrl = parse_nla(vd)
                        csi_data = ctrl.get(data_attr)
                        if csi_data:
                            records.append(parse_nla(csi_data))
                off += (nl_len + 3) & ~3
        except socket.timeout:
            break
    s.close()
    return records


def parse_record(d, ta_attr=CSI_DATA_TA, i_attr=CSI_DATA_I, q_attr=CSI_DATA_Q):
    """Turn a raw NLA attribute dict into something usable.

    The I/Q values are the expensive part. The firmware packs each subcarrier
    as its own NLA sub-attribute -- so for BW80 you get 256 tiny NLAs inside
    the I attribute and 256 inside Q. Parsing them as a dict is correct but
    slow (~2400 records/sec on the ARM core).

    I noticed every sub-NLA is exactly 8 bytes: [2B len=6][2B type][2B value][2B pad].
    So instead of parsing, I just read the int16 at offset i*8+4. Goes to ~11500/sec.
    The daemon needs ~600/sec to keep up at 30Hz with 6 chains, so there's headroom.
    """
    rec = {}
    if CSI_DATA_TS in d:
        rec['ts'] = struct.unpack('<I', d[CSI_DATA_TS][:4])[0]
    if CSI_DATA_RSSI in d:
        rec['rssi'] = struct.unpack('b', d[CSI_DATA_RSSI][:1])[0]
    if CSI_DATA_SNR in d:
        rec['snr'] = struct.unpack('B', d[CSI_DATA_SNR][:1])[0]
    if CSI_DATA_BW in d:
        rec['bw'] = struct.unpack('B', d[CSI_DATA_BW][:1])[0]
    if CSI_DATA_RX_ANT in d:
        rec['rx_ant'] = struct.unpack('B', d[CSI_DATA_RX_ANT][:1])[0]
    if CSI_DATA_TX_ANT in d and len(d[CSI_DATA_TX_ANT]) >= 2:
        rec['tx_ant'] = struct.unpack('<H', d[CSI_DATA_TX_ANT][:2])[0]
    if CSI_DATA_CHAIN_INFO in d and len(d[CSI_DATA_CHAIN_INFO]) >= 4:
        rec['chain_info'] = struct.unpack('<I', d[CSI_DATA_CHAIN_INFO][:4])[0]

    # The TA (transmitter address) is 6 MAC bytes, but MediaTek wraps each
    # byte in its own NLA sub-attribute. 48 bytes to encode 6 bytes of MAC.
    # I confirmed the layout by capturing frames from a device with known MAC
    # and comparing byte-for-byte against tcpdump output.
    if ta_attr in d:
        raw = d[ta_attr]
        if len(raw) >= 48:
            rec['ta'] = ':'.join(f'{raw[i*8 + 4]:02x}' for i in range(6))
        else:
            sub = parse_nla(raw)
            bs = [struct.unpack('B', sub[i][:1])[0] for i in range(6) if i in sub]
            if len(bs) == 6:
                rec['ta'] = ':'.join(f'{b:02x}' for b in bs)

    # I/Q: fixed 8-byte stride. each sub-attr is [len=6][type][int16][pad].
    for attr_id, key in [(i_attr, 'i'), (q_attr, 'q')]:
        if attr_id in d:
            raw = d[attr_id]
            n = len(raw) // 8
            rec[key] = [struct.unpack_from('<h', raw, i*8 + 4)[0] for i in range(n)]

    return rec


class CsiDumper:
    """Persistent netlink socket for sustained CSI capture.

    Opens one socket, performs the GETFAMILY handshake once, then reuses
    the socket for repeated vendor dump commands. This avoids the overhead
    of socket creation and GETFAMILY on every poll -- benchmarked at 90+
    polls/sec vs ~40 polls/sec with fresh sockets per call.

    Usage::

        fam = resolve_nl80211()
        init_csi(fam, ifindex)
        dumper = CsiDumper(fam, ifindex)
        try:
            while True:
                for raw_record in dumper.dump():
                    parsed = parse_record(raw_record)
                    ...
                time.sleep(0.01)
        finally:
            dumper.close()
    """

    def __init__(self, fam, ifindex, dump_num=CSI_CTRL_DUMP_NUM,
                 data_attr=CSI_CTRL_DATA, timeout=0.5):
        self.fam = fam
        self.ifindex = ifindex
        self.dump_num = dump_num
        self.data_attr = data_attr
        self._pid = os.getpid()
        self._seq = int(time.time()) & 0xFFFFFFFF

        self._sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW,
                                   NETLINK_GENERIC)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
        self._sock.bind((self._pid, 0))
        self._sock.settimeout(timeout)
        self._getfamily()

    def _getfamily(self):
        payload = struct.pack('BBH', CTRL_CMD_GETFAMILY, 1, 0)
        self._sock.send(_nlhdr(16 + len(payload), GENL_ID_CTRL,
                               NLM_F_REQUEST | NLM_F_DUMP,
                               self._seq, self._pid) + payload)
        self._seq += 1
        try:
            while True:
                data = self._sock.recv(65535)
                off = 0
                while off < len(data):
                    if off + 16 > len(data): break
                    nl_len, nl_type = struct.unpack_from('<IH', data, off)
                    if nl_len < 16: break
                    if nl_type == NLMSG_DONE:
                        return
                    off += (nl_len + 3) & ~3
        except socket.timeout:
            pass

    def dump(self, count=20):
        """Pull up to `count` buffered CSI records. Returns list of raw
        NLA attribute dicts (pass each to parse_record())."""
        genl = struct.pack('BBH', NL80211_CMD_VENDOR, 0, 0)
        attrs = (nla_u32(NL80211_ATTR_IFINDEX, self.ifindex) +
                 nla_u32(NL80211_ATTR_VENDOR_ID, MTK_VENDOR_ID) +
                 nla_u32(NL80211_ATTR_VENDOR_SUBCMD, MTK_CSI_SUBCMD) +
                 nla_nested(NL80211_ATTR_VENDOR_DATA,
                            nla_u16(self.dump_num, count)))
        body = genl + attrs
        self._sock.send(_nlhdr(16 + len(body), self.fam,
                               NLM_F_REQUEST | NLM_F_DUMP,
                               self._seq, self._pid) + body)
        self._seq += 1

        records = []
        done = False
        while not done:
            try:
                data = self._sock.recv(65535)
                off = 0
                while off < len(data):
                    if off + 16 > len(data): break
                    nl_len, nl_type = struct.unpack_from('<IH', data, off)
                    if nl_len < 16: break
                    if nl_type == NLMSG_ERROR:
                        if off + 20 <= len(data):
                            err = struct.unpack_from('<i', data, off + 16)[0]
                            if err != 0:
                                done = True
                        off += (nl_len + 3) & ~3
                        continue
                    if nl_type == NLMSG_DONE:
                        done = True; break
                    if nl_type == self.fam:
                        top = parse_nla(data[off:off + nl_len], 20)
                        vd = top.get(NL80211_ATTR_VENDOR_DATA)
                        if vd:
                            ctrl = parse_nla(vd)
                            csi_data = ctrl.get(self.data_attr)
                            if csi_data:
                                records.append(parse_nla(csi_data))
                    off += (nl_len + 3) & ~3
            except socket.timeout:
                break
        return records

    def close(self):
        """Close the underlying netlink socket."""
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def discover_attrs(fam, ifindex, timeout=10.0):
    """Figure out which attribute numbering this firmware uses.

    I wish this wasn't necessary, but different mt76 patch versions
    use different enum values for the CSI attributes. Our build has
    DUMP_NUM=8 and DATA=9; upstream mt76-vendor expects 9 and 10.
    Rather than hard-coding, we just try both and see which one
    gives us actual data back.
    """
    result = {'dump_num': CSI_CTRL_DUMP_NUM, 'data': CSI_CTRL_DATA,
              'i': CSI_DATA_I, 'q': CSI_DATA_Q, 'ta': CSI_DATA_TA}

    records = do_dump(fam, ifindex, count=1, timeout=timeout)
    if records:
        rec = records[0]
        if CSI_DATA_I in rec and CSI_DATA_Q in rec:
            log.info("attr discovery: defaults work (DUMP_NUM=%d DATA=%d)", CSI_CTRL_DUMP_NUM, CSI_CTRL_DATA)
            return result

    # try upstream numbering
    records = do_dump(fam, ifindex, count=1, timeout=timeout, dump_num=9, data_attr=10)
    if records:
        log.warning("attr discovery: using upstream numbering (DUMP_NUM=9 DATA=10)")
        return {'dump_num': 9, 'data': 10, 'i': 10, 'q': 11, 'ta': 9}

    log.warning("attr discovery: neither numbering scheme returned data")
    return result


def detect_capabilities(fam, ifindex, attrs=None):
    """Grab a few records and figure out what the firmware can do."""
    if attrs is None:
        attrs = discover_attrs(fam, ifindex)

    records = None
    for attempt in range(3):
        records = do_dump(fam, ifindex, count=10, timeout=10.0,
                          dump_num=attrs['dump_num'], data_attr=attrs['data'])
        if records: break
        time.sleep(1.0)

    if not records:
        log.error("no CSI records after 3 attempts -- is CSI enabled? is there traffic?")
        return None

    parsed = [parse_record(r, ta_attr=attrs['ta'], i_attr=attrs['i'], q_attr=attrs['q'])
              for r in records]

    bws = [r.get('bw', -1) for r in parsed if 'bw' in r]
    bw = max(set(bws), key=bws.count) if bws else -1

    # count non-zero subcarriers -- on mt76 all 256 should be real for BW80
    sub_counts = [sum(1 for v in r.get('i', []) if v != 0) for r in parsed]
    n_sub = max(sub_counts) if sub_counts else 0

    # group by timestamp to count chains per measurement
    ts_groups = {}
    for r in parsed:
        ts_groups.setdefault(r.get('ts', 0), []).append(r)
    chains = [len(g) for g in ts_groups.values()]
    n_chains = max(chains) if chains else 0

    log.info("firmware: %d subcarriers, BW%s, %d chains/meas",
             n_sub, {0:'20',1:'40',2:'80',3:'160'}.get(bw, '?'), n_chains)

    return {
        'n_sub': n_sub, 'bw': bw, 'chains_per_meas': n_chains,
        'needs_segment_reassembly': False,
        'nl80211_fam': fam, **{f'attr_{k}': v for k, v in attrs.items()},
    }
