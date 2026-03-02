import socket
import time
import threading
import logging

log = logging.getLogger(__name__)

# Tested a bunch of rates. 100 pps is the sweet spot -- gets 5.9 mean
# chains vs 3.6 without. Tried 200 and 500 pps too, no improvement.
# The firmware ring buffer just fills at the same rate regardless.
DEFAULT_RATE = 100
DEFAULT_PORT = 5556
PKT_SIZE = 64   # minimal UDP payload -- just needs to trigger a data frame


class TrafficStimulator:
    """Send UDP packets to the router to force data-frame CSI capture.

    Took me a while to figure out why I was only getting 3-4 chains.
    Turns out beacons are single-stream -- the AP doesn't use MIMO for
    management frames. Data frames with HE-NSS 2 trigger CSI on all
    RX chains. So you just need to send some traffic to the router and
    suddenly you go from 3 chains to 6. Obvious in retrospect.
    """
    def __init__(self, router_ip: str, rate: int = DEFAULT_RATE,
                 port: int = DEFAULT_PORT):
        self.router_ip = router_ip
        self.rate = rate
        self.port = port
        self._running = False
        self._thread = None
        self._sock = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("stimulator: %d pps -> %s:%d", self.rate, self.router_ip, self.port)

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()
            self._sock = None

    def _loop(self):
        pkt = bytes(PKT_SIZE)
        interval = 1.0 / self.rate
        while self._running:
            try:
                self._sock.sendto(pkt, (self.router_ip, self.port))
            except OSError:
                break
            time.sleep(interval)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()
