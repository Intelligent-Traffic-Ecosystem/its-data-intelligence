"""
Pytest configuration — adds the project root to sys.path so that
``from src.xxx import ...`` works without installing the package.
"""

from __future__ import annotations

import sys
from pathlib import Path

# traffic-predictor/ is the project root for this module
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
