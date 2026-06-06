from __future__ import annotations

import argparse
import select
import socket
import threading
import traceback
from datetime import datetime
from pathlib import Path


def write_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} {message}\n")


def relay(client: socket.socket, addr, target_addr: tuple[str, int], log_path: Path) -> None:
    target: socket.socket | None = None
    c2t = 0
    t2c = 0
    try:
        target = socket.create_connection(target_addr, timeout=10)
        target.settimeout(None)
        client.settimeout(None)
        sockets = [client, target]
        peer = {client: target, target: client}

        while sockets:
            readable, _, exceptional = select.select(sockets, [], sockets, 120)
            if exceptional:
                break
            if not readable:
                write_log(log_path, f"timeout addr={addr} c2t={c2t} t2c={t2c}")
                break
            for src in readable:
                try:
                    data = src.recv(65536)
                except OSError:
                    data = b""
                dst = peer[src]
                if data:
                    dst.sendall(data)
                    if src is client:
                        c2t += len(data)
                    else:
                        t2c += len(data)
                    continue

                if src in sockets:
                    sockets.remove(src)
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
        write_log(log_path, f"closed addr={addr} c2t={c2t} t2c={t2c}")
    except Exception:
        write_log(log_path, f"relay error addr={addr!r}\n{traceback.format_exc()}")
    finally:
        for s in (client, target):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Forward Stack-chan LAN HTTP traffic to WSL.")
    parser.add_argument("--listen-address", required=True)
    parser.add_argument("--listen-port", type=int, default=8000)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=8000)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    listen_addr = (args.listen_address, args.listen_port)
    target_addr = (args.target_host, args.target_port)
    log_path = Path(args.log)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(listen_addr)
    listener.listen(64)
    write_log(log_path, f"listening {listen_addr} -> {target_addr}")

    while True:
        client, addr = listener.accept()
        threading.Thread(
            target=relay,
            args=(client, addr, target_addr, log_path),
            daemon=False,
        ).start()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # argparse errors are already printed to stderr. Runtime errors need a
        # visible traceback when the script is launched from a console.
        raise
