"""Pytest defaults for GDPDU backend."""
from __future__ import annotations

import os

# Overview highlight tests expect narratives to build when no snapshot table is present.
os.environ.setdefault("OVERVIEW_NARRATIVE_ALLOW_REBUILD", "1")
