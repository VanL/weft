"""Simple interactive command used for streaming IO tests."""

from __future__ import annotations

import sys


def main() -> int:
    while True:
        line = sys.stdin.readline()
        if line == "":
            break
        if line.strip() in {"quit", "exit"}:
            sys.stdout.write("goodbye\n")
            sys.stdout.flush()
            break
        sys.stdout.write(f"echo: {line}")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked via subprocess
    raise SystemExit(main())
