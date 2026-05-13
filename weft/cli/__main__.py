"""Run the Weft CLI package directly."""

from __future__ import annotations

import sys

from weft.bootstrap import main

if __name__ == "__main__":
    sys.exit(main())
