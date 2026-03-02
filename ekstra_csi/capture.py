# Capture loop and chain grouping. Runs on the router.
# For the laptop side, use CSIClient instead.
import os
import time
import logging

from . import _netlink as nl
from .types import CSIFrame, ChainCSI

log = logging.getLogger(__name__)


def get_ifindex(iface: str = "phy0") -> int:
    """Resolve a PHY or netdev name to its kernel interface index.

    Accepts either a PHY name like 'phy0' or a netdev name like 'phy0-ap0'.
    nl80211 vendor commands need the netdev ifindex, not the PHY index --
    these are different numbers. /sys/class/ieee80211/phy0/index gives the
    PHY index (wrong), /sys/class/net/phy0-ap0/ifindex gives the netdev
    index (right). Got burned by this during initial deployment.
    """
    # Direct netdev name (e.g. phy0-ap0, wlan0)
    netdev_path = f"/sys/class/net/{iface}/ifindex"
    if os.path.exists(netdev_path):
        with open(netdev_path) as f:
            return int(f.read().strip())

    # PHY name (e.g. "phy1") -- OpenWrt names AP interfaces as {phy}-ap0
    phy_dir = f"/sys/class/ieee80211/{iface}"
    if not os.path.exists(phy_dir):
        raise RuntimeError(f"interface not found: {iface} (tried {netdev_path} and {phy_dir})")

    ap_name = f"{iface}-ap0"
    ap_path = f"/sys/class/net/{ap_name}/ifindex"
    if os.path.exists(ap_path):
        with open(ap_path) as f:
            idx = int(f.read().strip())
        log.info("resolved %s -> %s (ifindex %d)", iface, ap_name, idx)
        return idx

    # Fallback: scan all net interfaces for one whose phy80211 matches
    net_base = "/sys/class/net"
    if os.path.isdir(net_base):
        for netdev in sorted(os.listdir(net_base)):
            phy_link = os.path.join(net_base, netdev, "phy80211")
            if os.path.islink(phy_link):
                phy_real = os.path.basename(os.readlink(phy_link))
                if phy_real == iface:
                    idx_path = os.path.join(net_base, netdev, "ifindex")
                    with open(idx_path) as f:
                        idx = int(f.read().strip())
                    log.info("resolved %s -> %s (ifindex %d) via phy80211 link", iface, netdev, idx)
                    return idx

    raise RuntimeError(
        f"could not resolve ifindex for {iface}. "
        f"Try passing the netdev name directly (e.g. phy0-ap0, phy1-ap0)."
    )


class ChainGrouper:
    """Group individual chain records into complete CSI measurements.

    The firmware sends one netlink record per (rx_ant, tx_ant) pair.
    A full BW80 measurement with stimulated traffic is 6 records, all
    sharing the same firmware timestamp. MtkCSIdump doesn't group these
    at all -- it just dumps individual records and hopes you sort it out.

    I group by timestamp and flush when chain_info BIT(15) is set,
    which marks the last chain definitively. The 50ms timeout handles
    the rare case where BIT(15) gets lost (seen it once during a
    firmware crash).
    """
    TIMEOUT_MS = 50   # chain records arrive <2ms apart in practice

    def __init__(self):
        self._pending = {}  # ts -> list[record_dict]
        self._seq = 0

    def push(self, rec):
        ts = rec.get('ts', 0)
        self._pending.setdefault(ts, []).append(rec)

    def flush(self, force_ts=None):
        """Yield complete CSIFrames. Call after each dump batch."""
        frames = []
        now = time.time()
        dead = []

        for ts, recs in self._pending.items():
            # BIT(15) in chain_info marks the last chain of a measurement
            has_last = any(r.get('chain_info', 0) & 0x8000 for r in recs)
            if has_last or ts == force_ts:
                chains = [ChainCSI(
                    rx_ant=r.get('rx_ant', 0),
                    tx_ant=r.get('tx_ant', 0),
                    chain_info=r.get('chain_info', 0),
                    i_values=r.get('i', []),
                    q_values=r.get('q', [])
                ) for r in recs]

                r0 = recs[0]
                self._seq += 1
                frames.append(CSIFrame(
                    timestamp_us=ts, system_time=now, seq=self._seq,
                    ta=r0.get('ta', '??:??:??:??:??:??'),
                    rssi=r0.get('rssi', 0), snr=r0.get('snr', 0),
                    bw=r0.get('bw', 0), chains=chains,
                    complete=has_last))
                dead.append(ts)

        for ts in dead:
            del self._pending[ts]

        # Timeout stale groups (missed BIT(15))
        for ts, recs in list(self._pending.items()):
            if recs and (now - recs[0].get('_arrive', now)) > self.TIMEOUT_MS / 1000:
                log.debug("timeout-flushing ts=%d with %d chains", ts, len(recs))
                dead.append(ts)

        return frames


def capture_loop(iface: str = "phy0", poll_hz: int = 20):
    """Generator that yields CSIFrames forever. Runs on the router."""
    fam = nl.resolve_nl80211()
    ifindex = get_ifindex(iface)

    attrs = nl.discover_attrs(fam, ifindex)
    fail = nl.init_csi(fam, ifindex)
    if fail:
        raise RuntimeError(f"CSI init failed at step: {fail}")

    grouper = ChainGrouper()
    interval = 1.0 / poll_hz

    try:
        while True:
            raw_records = nl.do_dump(
                fam, ifindex, count=20, timeout=3.0,
                dump_num=attrs['dump_num'], data_attr=attrs['data'])

            for rd in raw_records:
                rec = nl.parse_record(rd, ta_attr=attrs['ta'],
                                      i_attr=attrs['i'], q_attr=attrs['q'])
                rec['_arrive'] = time.time()
                grouper.push(rec)

            for frame in grouper.flush():
                yield frame

            time.sleep(interval)
    finally:
        nl.disable_csi(fam, ifindex)
