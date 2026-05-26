"""Authentication — public key allowlist matching.

Meshcore ``CONTACT_MSG_RECV`` events include a ``pubkey_prefix`` field
(typically 6 bytes / 12 hex chars).  We match that prefix against the
configured allowlist to decide whether a sender is authorised.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import AllowlistEntry

# Meshcore pubkey_prefix is 6 bytes → 12 hex chars
PREFIX_LEN = 12


@dataclass
class AuthResult:
    """Outcome of an allowlist lookup."""

    allowed: bool
    entry: Optional[AllowlistEntry] = None
    matched_prefix: str = ""


class Auth:
    """Matches incoming pubkey prefixes against the allowlist."""

    def __init__(self, allowlist: list[AllowlistEntry]) -> None:
        # Build a dict: prefix → entry
        self._prefix_map: dict[str, AllowlistEntry] = {}
        for entry in allowlist:
            prefix = entry.pubkey[:PREFIX_LEN].lower()
            if prefix in self._prefix_map:
                existing = self._prefix_map[prefix].name
                raise ValueError(
                    f"Duplicate pubkey prefix {prefix}: "
                    f"'{entry.name}' conflicts with '{existing}'"
                )
            self._prefix_map[prefix] = entry

    def lookup(self, pubkey_prefix: str) -> AuthResult:
        """Check whether *pubkey_prefix* is in the allowlist.

        Args:
            pubkey_prefix: hex string from the mesh event (at least 12 chars).

        Returns:
            ``AuthResult`` with ``allowed=True`` and the matching entry
            if found; ``allowed=False`` otherwise.
        """
        prefix = pubkey_prefix.strip().lower()[:PREFIX_LEN]
        entry = self._prefix_map.get(prefix)
        if entry is not None:
            return AuthResult(allowed=True, entry=entry, matched_prefix=prefix)
        return AuthResult(allowed=False)
