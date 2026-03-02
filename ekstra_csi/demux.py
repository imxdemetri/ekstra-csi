from collections import defaultdict
import logging

log = logging.getLogger(__name__)


class DeviceDemux:
    """Split CSI frames by source MAC -- one stream per device.

    This is why per-frame MAC matters. Without it, your phone's CSI
    mixes with the TV's CSI and the smart speaker's CSI and you can't
    tell which signal change came from where. With MAC routing, each
    WiFi link becomes its own independent sensor.
    """
    def __init__(self, targets=None, on_frame=None):
        self.targets = set(m.lower() for m in targets) if targets else None
        self.on_frame = on_frame
        self._buffers = defaultdict(list)
        self._counts = defaultdict(int)

    def push(self, frame):
        ta = frame.ta
        if self.targets and ta not in self.targets:
            return
        self._counts[ta] += 1
        if self.on_frame:
            self.on_frame(ta, frame)
        else:
            self._buffers[ta].append(frame)

    def pop(self, ta):
        frames = self._buffers.pop(ta, [])
        return frames

    def devices(self):
        return set(self._counts.keys())

    @property
    def stats(self):
        return dict(self._counts)
