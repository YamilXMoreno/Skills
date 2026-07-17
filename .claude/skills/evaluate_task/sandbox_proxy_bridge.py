#!/usr/bin/env python3
"""Minimal threaded TCP forwarder.

LEGACY / UNUSED: under rootless Docker the setup script rewrites the in-container
proxy base URL to the slirp host alias (10.0.2.2) directly, so no on-host
forwarder is started. This file is kept only for compatibility / non-rootless
fallbacks. Historically it exposed a host loopback-only service (the anonymizer
proxy on 127.0.0.1) on the docker bridge gateway so containers could reach it.

Usage: sandbox_proxy_bridge.py <listen_host:listen_port> <target_host:target_port>
"""
import socket
import sys
import threading


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client, target):
    try:
        upstream = socket.create_connection(target)
    except OSError as e:
        client.close()
        sys.stderr.write(f"upstream connect failed: {e}\n")
        return
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


def main():
    lhost, lport = sys.argv[1].rsplit(":", 1)
    thost, tport = sys.argv[2].rsplit(":", 1)
    target = (thost, int(tport))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((lhost, int(lport)))
    srv.listen(128)
    sys.stderr.write(f"forwarding {lhost}:{lport} -> {thost}:{tport}\n")
    while True:
        client, _ = srv.accept()
        handle(client, target)


if __name__ == "__main__":
    main()
