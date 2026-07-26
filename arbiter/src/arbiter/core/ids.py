"""Central place for ID generation so every entity uses the same scheme."""

from __future__ import annotations

import uuid


def new_id() -> str:
    return str(uuid.uuid4())
