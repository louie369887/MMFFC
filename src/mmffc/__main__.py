"""python -m mmffc entry point（与 mmffc.exe 同一统一入口）。"""

from __future__ import annotations

import sys

from mmffc.launcher import main

if __name__ == "__main__":
    sys.exit(main())
