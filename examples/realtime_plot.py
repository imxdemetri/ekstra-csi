"""Live CSI amplitude waterfall in the terminal. No matplotlib needed."""
import numpy as np
from ekstra_csi import CSIClient, TrafficStimulator
from ekstra_csi.preprocessing import StaticRemover

router = "192.168.1.1"
sr = StaticRemover()
BINS = 60  # terminal width

with TrafficStimulator(router):
    for frame in CSIClient(router):
        H = frame.to_complex()
        dynamic = sr.remove(H[0])
        amp = np.abs(dynamic)
        # downsample to terminal width
        if len(amp) > BINS:
            amp = amp[:BINS * (len(amp) // BINS)].reshape(-1, len(amp) // BINS).mean(axis=1)
        mx = amp.max() + 1e-10
        bar = ''.join('#' if a/mx > 0.3 else '.' for a in amp[:BINS])
        print(f"{frame.ta} snr={frame.snr:2d} |{bar}|")
