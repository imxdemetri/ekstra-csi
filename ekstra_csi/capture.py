# Capture loop and chain grouping. Runs on the router.
# For the laptop side, use CSIClient instead.
import os
import time
import logging

from . import _netlink as nl
from .types import CSIFrame, ChainCSI

log = logging.getLogger(__name__)


def get_ifindex(iface: str = "phy0") -> int:
    path = f"/sys/class/ieee80211/{iface}/index"
    try:
        with open(path) as f:
            return int(f.read().strip())
    except FileNotFoundError:
        raise RuntimeError(f"phy interface not found: {path}")


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
