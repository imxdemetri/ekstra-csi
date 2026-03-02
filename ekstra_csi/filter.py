import logging

log = logging.getLogger(__name__)

# Below 15 dB the phase is noise-dominated. Measured on OpenWrt One: the
# std of phase across subcarriers jumps from ~0.3 rad to >1.5 rad.
DEFAULT_SNR_FLOOR = 15


class CSIFilter:
    """Drop junk frames before they reach your model."""

    def __init__(self, macs=None, snr_floor=DEFAULT_SNR_FLOOR, min_chains=1):
        self.macs = set(m.lower() for m in macs) if macs else None
        self.snr_floor = snr_floor
        self.min_chains = min_chains
        self._dropped = {'mac': 0, 'snr': 0, 'chains': 0}

    def accept(self, frame) -> bool:
        if self.macs and frame.ta not in self.macs:
            self._dropped['mac'] += 1
            return False
        if frame.snr < self.snr_floor:
            self._dropped['snr'] += 1
            return False
        if frame.n_chains < self.min_chains:
            self._dropped['chains'] += 1
            return False
        return True

    @property
    def stats(self):
        return dict(self._dropped)
