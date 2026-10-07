"""python -m mmffc.mcp entry point."""

from __future__ import annotations

import sys

from mmffc.mcp.server import main

if __name__ == "__main__":
    sys.exit(main())
