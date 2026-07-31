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
        self._by_network_code: Dict[str, str] = {}

    def load_dir(self, dir_path: str | Path) -> None:
        for reason_code, pack in load_rulepack_dir(dir_path).items():
            validate_rulepack(pack)
            self._by_reason_code[reason_code] = pack
            self._by_hash[pack.content_hash()] = pack
            if pack.chargeback_right is not None:
                network_code = pack.chargeback_right.network_code
                clash = self._by_network_code.get(network_code)
                if clash is not None and clash != reason_code:
                    raise RulepackError(
                        f"network code {network_code!r} is claimed by both {clash!r} and "
                        f"{reason_code!r} -- a dispute filed under it could not be routed"
                    )
                self._by_network_code[network_code] = reason_code

    def resolve(self, code: str) -> str:
        """Map whatever code a caller supplied to the internal reason code.

        ARBITER's rulepacks are keyed by Amex's US-style reason codes (F29,
        C08, C02), but the code an Australian merchant actually reads off
        their "Resolve Disputes" screen -- and the only code that appears in
        the published Amex chargeback guide -- is the four-digit network form
        (4540, 4554, 4513). Both are the same dispute. Accepting only one
        dialect meant a caller holding a real Amex reason code from a real
        Amex screen could not file with it.

        Fails closed, like every other routing decision here: an
        unrecognised code raises rather than falling back to a default
        rulepack. Picking the wrong rulepack means the wrong predicates and
        the wrong evidence requirements entirely (CLAUDE.md invariant #6).
        """
        if code in self._by_reason_code:
            return code
        mapped = self._by_network_code.get(code)
        if mapped is not None:
            return mapped
        raise RulepackError(
            f"no rulepack loaded for reason_code={code!r} "
            f"(known: {sorted(self._by_reason_code)}, network codes: {sorted(self._by_network_code)})"
        )

    def latest(self, reason_code: str) -> RulePack:
        pack = self._by_reason_code.get(reason_code)
        if pack is None:
            # Accept the network form here too, so a caller that only ever
            # sees "4554" is not forced to know the mapping exists.
            pack = self._by_reason_code.get(self._by_network_code.get(reason_code, ""))
        if pack is None:
            raise RulepackError(f"no rulepack loaded for reason_code={reason_code!r}")
        return pack

    def by_hash(self, content_hash: str) -> Optional[RulePack]:
        return self._by_hash.get(content_hash)

    def reason_codes(self) -> list[str]:
        return sorted(self._by_reason_code)

    def network_codes(self) -> Dict[str, str]:
        """Amex network code -> internal reason code, for the API's
        rulepack listing and for callers that need to show both."""
        return dict(self._by_network_code)
