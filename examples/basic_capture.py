"""Capture and print CSI frames from the router."""
from ekstra_csi import CSIClient, TrafficStimulator

router = "192.168.1.1"

with TrafficStimulator(router):
    for frame in CSIClient(router):
        print(frame)
        H = frame.to_complex()
        print(f"  H shape: {H.shape}  mean |H|: {abs(H).mean():.1f}")
