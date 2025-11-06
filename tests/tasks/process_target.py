"""Utility script/callable used by tests to simulate task workloads."""

from __future__ import annotations

import argparse
import json
import socket
import time


def run_task(
    *,
    duration: float = 0.0,
    memory_mb: int = 0,
    connections: int = 0,
    result: str = "done",
    output_size: int = 0,
    cpu_percent: float = 0.0,
    open_files: int = 0,
) -> str:
    """Simulate work for use as either a callable or subprocess target."""
    import os
    import tempfile

    buffers: list[bytearray] = []
    sockets: list[socket.socket] = []
    tcp_servers: list[socket.socket] = []
    tcp_clients: list[socket.socket] = []
    files: list[tempfile.NamedTemporaryFile] = []
    file_paths: list[str] = []

    if memory_mb > 0:
        try:
            import mmap

            size_bytes = memory_mb * 1024 * 1024
            mm = mmap.mmap(-1, size_bytes)
            page_size = mmap.PAGESIZE
            for offset in range(0, size_bytes, page_size):
                mm.seek(offset)
                mm.write_byte(1)
            buffers.append(mm)
        except MemoryError:
            pass

    for _ in range(max(0, connections)):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        tcp_servers.append(server)

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(server.getsockname())
        conn, _ = server.accept()
        tcp_clients.extend([client, conn])

    for _ in range(max(0, open_files)):
        tmp = tempfile.NamedTemporaryFile(delete=False)
        files.append(tmp)
        file_paths.append(tmp.name)

    end_time = time.time() + max(0.0, duration)
    busy = max(0.0, cpu_percent)
    sleep_slice = 0.05
    while time.time() < end_time:
        if busy <= 0:
            time.sleep(max(0.0, end_time - time.time()))
            break
        busy_time = sleep_slice * min(1.0, busy)
        idle_time = sleep_slice - busy_time
        start = time.perf_counter()
        while time.perf_counter() - start < busy_time:
            pass
        if idle_time > 0:
            time.sleep(idle_time)

    output_data = result
    if output_size > 0:
        output_data += "X" * output_size

    for sock in sockets:
        try:
            sock.close()
        except OSError:
            pass
    for sock in tcp_clients:
        try:
            sock.close()
        except OSError:
            pass
    for server in tcp_servers:
        try:
            server.close()
        except OSError:
            pass

    for handle in files:
        try:
            handle.close()
        except OSError:
            pass
    for path in file_paths:
        try:
            os.unlink(path)
        except OSError:
            pass

    return output_data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--memory-mb", type=int, default=0)
    parser.add_argument("--connections", type=int, default=0)
    parser.add_argument("--result", default="done")
    parser.add_argument("--result-json", action="store_true")
    parser.add_argument("--output-size", type=int, default=0)
    parser.add_argument("--cpu-percent", type=float, default=0.0)
    parser.add_argument("--fds", type=int, default=0)
    args = parser.parse_args(argv)

    output = run_task(
        duration=args.duration,
        memory_mb=args.memory_mb,
        connections=args.connections,
        result=args.result,
        output_size=args.output_size,
        cpu_percent=args.cpu_percent,
        open_files=args.fds,
    )

    if args.result_json:
        print(json.dumps({"result": output}), end="")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
