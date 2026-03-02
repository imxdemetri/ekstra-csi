import socket
import struct
import time
import threading
import logging

from .types import CSIFrame
from . import parse

log = logging.getLogger(__name__)


class CSIClient:
    """Connect to the daemon on the router and iterate over CSI frames.

    Auto-reconnects on disconnect with exponential backoff. The daemon
    drops clients cleanly when they go away, so reconnecting is safe.
    """
    def __init__(self, host: str = "192.168.1.1", port: int = 5500,
                 reconnect: bool = True, max_backoff: float = 10.0):
        self.host = host
        self.port = port
        self.reconnect = reconnect
        self.max_backoff = max_backoff
        self._sock = None
        self._buf = b''

    def connect(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(30.0)
        self._sock.connect((self.host, self.port))
        self._buf = b''
        log.info("connected to %s:%d", self.host, self.port)

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(8192)
            if not chunk:
                raise ConnectionError("daemon closed connection")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _recv_frame(self) -> CSIFrame:
        length_data = self._recv_exact(4)
        length = struct.unpack('<I', length_data)[0]
        body = self._recv_exact(length)
        return parse.deserialize(body)

    def frames(self):
        """Iterate over CSI frames. Reconnects on failure if configured."""
        backoff = 0.5
        while True:
            try:
                if not self._sock:
                    self.connect()
                    backoff = 0.5
                while True:
                    yield self._recv_frame()
            except (OSError, ConnectionError, struct.error) as e:
                log.warning("connection lost: %s", e)
                self.close()
                if not self.reconnect:
                    return
                log.info("reconnecting in %.1fs", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff)

    def __iter__(self):
        return self.frames()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()
