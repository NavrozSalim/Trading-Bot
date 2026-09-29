"""Project entrypoint: python main.py --setup"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_bot.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
