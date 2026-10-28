"""One subprocess-only SSH byte relay to an explicitly leased loopback port."""
import os
import select
import socket
import sys
import time

def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise ValueError('LOOPBACK_PORT_REQUIRED')
    port = int(sys.argv[1])
    if not 1024 <= port <= 65535:
        raise ValueError('LOOPBACK_PORT_BOUND')
    with socket.create_connection(('127.0.0.1', port), timeout=5) as connection:
        connection.settimeout(5)
        deadline = time.monotonic() + 95
        inputs = [0, connection]
        while inputs and time.monotonic() < deadline:
            ready, _, _ = select.select(inputs, [], [], 0.5)
            for source in ready:
                raw = os.read(0, 32768) if source == 0 else connection.recv(32768)
                if not raw:
                    if source == 0:
                        inputs.remove(0)
                        connection.shutdown(socket.SHUT_WR)
                    else:
                        return
                elif source == 0:
                    connection.sendall(raw)
                else:
                    view = memoryview(raw)
                    while view:
                        view = view[os.write(1, view):]
        raise TimeoutError('LOOPBACK_RELAY_EXPIRED')

if __name__ == '__main__':
    try:
        main()
    except Exception:
        # stdout is the encrypted SSH stream, never diagnostics or payload logs.
        sys.stderr.write('PUBLICATION_LOOPBACK_PROXY_FAILED\n')
        raise SystemExit(1)
