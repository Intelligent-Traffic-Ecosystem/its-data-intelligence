import os
import sys

# MUST set env vars BEFORE importing any modules from src/
# Use 127.0.0.1 instead of localhost to avoid DNS resolution issues on Windows
os.environ["POSTGRES_URL"] = "postgresql://user:pass@127.0.0.1:5432/traffic"
# Test admin token used by tests/test_b3_endpoints.py (and any other admin tests).
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")

import pytest

# Add src/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
