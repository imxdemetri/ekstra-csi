from __future__ import annotations
from dataclasses import dataclass

try:
    import numpy as np
    HAS_NP = True
except ImportError:
    HAS_NP = False


@dataclass
class ChainCSI:
    rx_ant: int
    tx_ant: int
    chain_info: int
    i_values: list   # int16 per subcarrier
    q_values: list   # int16 per subcarrier

    @property
    def n_sub(self) -> int:
        return len(self.i_values)

    def to_complex(self):
        if not HAS_NP:
            raise RuntimeError("numpy required: pip install ekstra-csi[processing]")
        return (np.array(self.i_values, dtype=np.float32)
                + 1j * np.array(self.q_values, dtype=np.float32))


@dataclass
class CSIFrame:
    """One complete CSI measurement -- all chains, all metadata.

    Without stimulation you get 3-4 chains (beacon traffic only).
    With a TrafficStimulator running, you get 6 chains consistently
    because data frames trigger MIMO CSI capture.
    """
    timestamp_us: int     # firmware monotonic clock
    system_time: float    # time.time() on the daemon
    seq: int              # monotonic, no gaps
    ta: str               # transmitter address "xx:xx:xx:xx:xx:xx"
    rssi: int             # dBm, signed
    snr: int              # dB, unsigned (firmware PHY estimate)
    bw: int               # 0=BW20, 1=BW40, 2=BW80, 3=BW160
    chains: list          # list[ChainCSI]
    complete: bool = True # False if timeout-completed (missed BIT(15))

    BW_MAP = {0: "BW20", 1: "BW40", 2: "BW80", 3: "BW160"}
    BW_SUBS = {0: 64, 1: 128, 2: 256, 3: 512}

    @property
    def bw_name(self) -> str:
        return self.BW_MAP.get(self.bw, f"?({self.bw})")

    @property
    def n_chains(self) -> int:
        return len(self.chains)

    @property
    def n_sub(self) -> int:
        return self.chains[0].n_sub if self.chains else 0

    def to_complex(self):
        if not HAS_NP:
            raise RuntimeError("numpy required: pip install ekstra-csi[processing]")
        return np.stack([c.to_complex() for c in self.chains])

    def __repr__(self):
        return (f"CSIFrame(seq={self.seq} ta={self.ta} {self.bw_name} "
                f"{self.n_chains}ch×{self.n_sub}sc "
                f"rssi={self.rssi} snr={self.snr})")


@dataclass
class FirmwareProfile:
    """What the firmware told us it can do. Detected at startup."""
    n_sub: int
    bw: int
    chains_per_meas: int
    needs_segment_reassembly: bool
    nl80211_fam: int
    attr_dump_num: int
    attr_data: int
    attr_i: int
    attr_q: int
    attr_ta: int
