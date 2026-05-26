"""Command parser and router.

Parses DM text, matches against known command patterns, dispatches to
Home Assistant API calls, and returns a reply string (truncated to the
configured max length).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from .config import Config
from .ha_client import HAClient, EntityInfo

# ── helpers ──────────────────────────────────────────────────────


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _join_entity_list(entities: list[EntityInfo], max_chars: int) -> str:
    """Format a list of entities as 'name: state, name: state, …'."""
    parts: list[str] = []
    budget = max_chars - 2  # reserve for possible "…"
    for e in entities:
        label = e.friendly_name or e.entity_id
        chunk = f"{label}: {e.state}"
        if parts:
            new_len = sum(len(p) + 2 for p in parts) + len(chunk)
        else:
            new_len = len(chunk)
        if new_len > budget:
            parts.append("…")
            break
        parts.append(chunk)
    return ", ".join(parts) if parts else "(none)"


# ── command result ───────────────────────────────────────────────


@dataclass
class CommandResult:
    reply: str


# ── command handler type ─────────────────────────────────────────

CommandHandler = Callable[[str, Any], Awaitable[CommandResult]]


# ── router ───────────────────────────────────────────────────────


class CommandRouter:
    """Parses DM text and dispatches to the right command handler."""

    def __init__(self, ha: HAClient, config: Config) -> None:
        self._ha = ha
        self._max_reply = config.dm_reply_max_chars

        # Ordered list of (regex, handler) — first match wins
        self._routes: list[tuple[re.Pattern[str], CommandHandler]] = [
            (re.compile(r"^help$", re.IGNORECASE), self._cmd_help),
            (re.compile(r"^light(s)?$", re.IGNORECASE), self._cmd_list_domain),
            (re.compile(r"^switch(es)?$", re.IGNORECASE), self._cmd_list_domain),
            (re.compile(r"^climate(s)?$", re.IGNORECASE), self._cmd_list_domain),
            (re.compile(r"^all$", re.IGNORECASE), self._cmd_list_all),
            (
                re.compile(
                    r"^(light|switch)\s+(.+?)\s+(on|off|toggle)$", re.IGNORECASE
                ),
                self._cmd_toggle,
            ),
            (
                re.compile(
                    r"^climate\s+(set|temp)\s+(\d+\.?\d*)$", re.IGNORECASE
                ),
                self._cmd_climate,
            ),
            (
                re.compile(r"^status\s+(.+)$", re.IGNORECASE),
                self._cmd_status,
            ),
        ]

    async def dispatch(self, text: str) -> str:
        """Parse *text* and return a reply string."""
        text = text.strip()
        if not text:
            return "Empty command. Send 'help' for usage."

        for pattern, handler in self._routes:
            m = pattern.match(text)
            if m:
                try:
                    result = await handler(text, m)
                    return _truncate(result.reply, self._max_reply)
                except Exception as exc:
                    return _truncate(f"Error: {exc}", self._max_reply)

        return _truncate(
            f"Unknown command: {text[:40]}. Send 'help' for usage.",
            self._max_reply,
        )

    # ── individual command handlers ──────────────────────────

    async def _cmd_help(self, _text: str, _match: re.Match) -> CommandResult:
        return CommandResult(
            "Commands: light <name> on|off|toggle, "
            "switch <name> on|off|toggle, "
            "climate set <temp>, "
            "status <entity>, "
            "lights|switches|all, help"
        )

    async def _cmd_list_domain(self, text: str, _match: re.Match) -> CommandResult:
        word = text.strip().lower().rstrip("s")  # "lights" → "light"
        entities = self._ha.list_by_domain(word)
        if not entities:
            return CommandResult(f"No {word} entities found.")
        listing = _join_entity_list(entities, self._max_reply - len(f"{word}s: "))
        return CommandResult(f"{word}s: {listing}")

    async def _cmd_list_all(self, _text: str, _match: re.Match) -> CommandResult:
        # Group by domain with counts
        counts: dict[str, int] = {}
        for e in self._ha.entities:
            counts[e.domain] = counts.get(e.domain, 0) + 1
        summary = ", ".join(f"{d}:{c}" for d, c in sorted(counts.items()))
        return CommandResult(f"All entities: {summary}")

    async def _cmd_toggle(self, _text: str, m: re.Match) -> CommandResult:
        domain = m.group(1).lower()        # "light" or "switch"
        name = m.group(2).strip()          # e.g. "kitchen"
        action = m.group(3).lower()        # "on", "off", or "toggle"

        # Fuzzy-match the entity
        entity = self._ha.fuzzy_match(domain, name)
        if entity is None:
            return CommandResult(f"No {domain} matching '{name}'.")

        # Determine service name
        if action in ("on", "off"):
            service = f"turn_{action}"
        else:
            service = "toggle"

        await self._ha.call_service(domain, service, entity_id=entity.entity_id)

        # Refresh and confirm
        updated = await self._ha.get_state(entity.entity_id)
        new_state = updated.state if updated else "?"
        label = entity.friendly_name or entity.entity_id
        return CommandResult(f"{label} → {new_state}")

    async def _cmd_climate(self, _text: str, m: re.Match) -> CommandResult:
        temp_str = m.group(2)
        temperature = float(temp_str)

        # Find the first climate entity
        climates = self._ha.list_by_domain("climate")
        if not climates:
            return CommandResult("No climate entities found.")

        entity = climates[0]  # if there's exactly one thermostat, use it
        await self._ha.call_service(
            "climate",
            "set_temperature",
            entity_id=entity.entity_id,
            extra_data={"temperature": temperature},
        )

        updated = await self._ha.get_state(entity.entity_id)
        new_temp = (
            updated.attributes.get("temperature", "?")
            if updated
            else "?"
        )
        label = entity.friendly_name or entity.entity_id
        return CommandResult(f"{label} set to {new_temp}°")

    async def _cmd_status(self, _text: str, m: re.Match) -> CommandResult:
        entity_id = m.group(1).strip()

        # If they gave a friendly name fragment, try fuzzy matching
        entity = self._ha.get_entity(entity_id)
        if entity is None:
            # Try fuzzy across all domains
            all_entities = self._ha.entities
            needle = entity_id.lower()
            for e in all_entities:
                if needle in e.name_for_match:
                    entity = e
                    break

        if entity is None:
            return CommandResult(f"Entity '{entity_id}' not found.")

        # Live state
        live = await self._ha.get_state(entity.entity_id)
        if live is None:
            return CommandResult(f"Entity '{entity.entity_id}' not found.")

        label = live.friendly_name or live.entity_id
        return CommandResult(f"{label}: {live.state}")
