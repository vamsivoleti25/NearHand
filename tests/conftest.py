"""Tests use the database path each test sets (llm.DB_PATH), not a per-visitor demo copy."""

import os

os.environ.setdefault("NEARHAND_PRIVATE_DEMO", "0")
