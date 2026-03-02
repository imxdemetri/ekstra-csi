"""Separate CSI streams by source MAC for multi-link sensing."""
import sys
from ekstra_csi import CSIClient, TrafficStimulator
from ekstra_csi.demux import DeviceDemux

router = "192.168.1.1"
targets = sys.argv[1:] or None  # pass MACs as CLI args, or capture all

demux = DeviceDemux(targets=targets)

with TrafficStimulator(router):
    for frame in CSIClient(router):
        demux.push(frame)
        if frame.seq % 100 == 0:
            print(f"devices seen: {demux.stats}")
