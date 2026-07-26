"""In-memory rulepack registry, keyed by (reason_code, content_hash).

CLAUDE.md invariant #7: every decision pins rulepack_hash. This registry is
how a decision made against an older rulepack version can still be replayed
byte-for-byte later -- it never mutates or replaces a hash's rules in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from arbiter.core.errors import RulepackError
from arbiter.horn.clause import RulePack

from .loader import load_rulepack_dir
from .validate import validate_rulepack


class RulepackRegistry:
    def __init__(self) -> None:
        self._by_reason_code: Dict[str, RulePack] = {}
        self._by_hash: Dict[str, RulePack] = {}

    def load_dir(self, dir_path: str | Path) -> None:
        for reason_code, pack in load_rulepack_dir(dir_path).items():
            validate_rulepack(pack)
            self._by_reason_code[reason_code] = pack
            self._by_hash[pack.content_hash()] = pack

    def latest(self, reason_code: str) -> RulePack:
        pack = self._by_reason_code.get(reason_code)
        if pack is None:
            raise RulepackError(f"no rulepack loaded for reason_code={reason_code!r}")
        return pack

    def by_hash(self, content_hash: str) -> Optional[RulePack]:
        return self._by_hash.get(content_hash)

    def reason_codes(self) -> list[str]:
        return sorted(self._by_reason_code)
