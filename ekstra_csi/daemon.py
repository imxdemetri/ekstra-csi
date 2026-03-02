#!/usr/bin/env python3
"""Router-side CSI daemon.

Captures from netlink, streams to TCP clients. Main loop is single-threaded
because the ARM core on these routers isn't fast -- threading the hot path
just adds context-switch overhead for no benefit. TCP accept and health
logging get their own threads since they're idle 99% of the time.
"""
import socket
import struct
import time
import threading
import argparse
import logging

from . import _netlink as nl
from .capture import get_ifindex, ChainGrouper
from . import parse

log = logging.getLogger('ekstra_csi.daemon')


class CSIDaemon:
    def __init__(self, iface='phy0', port=5500, poll_hz=20):
        self.iface = iface
        self.port = port
        self.poll_hz = poll_hz
        self._clients = []
        self._lock = threading.Lock()
        self._running = False
        self._frames_sent = 0

    def _accept_loop(self, srv):
        while self._running:
            try:
                conn, addr = srv.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock:
                    self._clients.append(conn)
                log.info("client connected: %s", addr)
            except OSError:
                break

    def _broadcast(self, frame):
        data = parse.serialize(frame)
        dead = []
        with self._lock:
            for c in self._clients:
                try:
                    c.sendall(data)
                except OSError:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)
                try: c.close()
                except OSError: pass
        self._frames_sent += 1

    def _health_loop(self):
        while self._running:
            time.sleep(30)
            with self._lock:
                n = len(self._clients)
            log.info("health: %d clients, %d frames sent", n, self._frames_sent)

    def run(self):
        log.info("starting daemon on %s, port %d", self.iface, self.port)
        self._running = True

        fam = nl.resolve_nl80211()
        ifindex = get_ifindex(self.iface)
        attrs = nl.discover_attrs(fam, ifindex)

        fail = nl.init_csi(fam, ifindex)
        if fail:
            raise RuntimeError(f"CSI init failed at step: {fail}")
        log.info("CSI enabled")

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('0.0.0.0', self.port))
        srv.listen(4)

        threading.Thread(target=self._accept_loop, args=(srv,), daemon=True).start()
        threading.Thread(target=self._health_loop, daemon=True).start()

        grouper = ChainGrouper()
        interval = 1.0 / self.poll_hz

        try:
            while self._running:
                records = nl.do_dump(
                    fam, ifindex, count=20, timeout=3.0,
                    dump_num=attrs['dump_num'], data_attr=attrs['data'])

                for rd in records:
                    rec = nl.parse_record(rd, ta_attr=attrs['ta'],
                                          i_attr=attrs['i'], q_attr=attrs['q'])
                    rec['_arrive'] = time.time()
                    grouper.push(rec)

                for frame in grouper.flush():
                    self._broadcast(frame)

                time.sleep(interval)
        except KeyboardInterrupt:
            log.info("shutting down")
        finally:
            self._running = False
            nl.disable_csi(fam, ifindex)
            srv.close()
            with self._lock:
                for c in self._clients:
                    try: c.close()
                    except OSError: pass


def main():
    parser = argparse.ArgumentParser(description='ekstra-csi daemon')
    parser.add_argument('--iface', default='phy0')
    parser.add_argument('--port', type=int, default=5500)
    parser.add_argument('--poll-hz', type=int, default=20)
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s')

    CSIDaemon(iface=args.iface, port=args.port, poll_hz=args.poll_hz).run()


if __name__ == '__main__':
    main()
