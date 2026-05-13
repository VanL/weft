"""Main entry point for running Weft as a module."""

from __future__ import annotations

import sys

from .bootstrap import main

if __name__ == "__main__":
    sys.exit(main())
