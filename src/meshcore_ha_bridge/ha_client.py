"""Async Home Assistant REST API client.

Uses ``httpx`` to talk to the HA REST API with a long-lived access token.
Caches the entity list at startup for fuzzy-matching in the command router.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from .config import HAConfig


@dataclass
class EntityInfo:
    """Lightweight snapshot of a single HA entity."""

    entity_id: str
    domain: str  # "light", "switch", "climate", …
    state: str
    friendly_name: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def name_for_match(self) -> str:
        """Lowercased friendly_name (or entity_id fallback) for fuzzy matching."""
        return (self.friendly_name or self.entity_id).lower()


class HAClient:
    """Async client for the Home Assistant REST API."""

    def __init__(self, config: HAConfig) -> None:
        self._base = config.url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
        }
        self._timeout = config.request_timeout
        self._client: Optional[httpx.AsyncClient] = None

        # Entity cache — populated by refresh_entities()
        self.entities: list[EntityInfo] = []
        self._entities_by_id: dict[str, EntityInfo] = {}

    # ── lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Create the HTTP client and load the initial entity cache."""
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers=self._headers,
            timeout=httpx.Timeout(self._timeout),
        )
        await self.refresh_entities()

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HAClient not started — call await client.start() first")
        return self._client

    # ── entity cache ─────────────────────────────────────────

    async def refresh_entities(self) -> list[EntityInfo]:
        """Fetch all states and rebuild the entity cache."""
        resp = await self._c.get("/api/states")
        resp.raise_for_status()
        raw: list[dict[str, Any]] = resp.json()

        entities: list[EntityInfo] = []
        by_id: dict[str, EntityInfo] = {}
        for item in raw:
            eid = str(item.get("entity_id", ""))
            if not eid:
                continue
            domain = eid.split(".", 1)[0] if "." in eid else ""
            attrs = item.get("attributes", {}) or {}
            ei = EntityInfo(
                entity_id=eid,
                domain=domain,
                state=str(item.get("state", "unknown")),
                friendly_name=str(attrs.get("friendly_name", "")),
                attributes=attrs,
            )
            entities.append(ei)
            by_id[eid] = ei

        self.entities = entities
        self._entities_by_id = by_id
        return entities

    def get_entity(self, entity_id: str) -> Optional[EntityInfo]:
        """Look up a cached entity by exact entity_id."""
        return self._entities_by_id.get(entity_id)

    def fuzzy_match(
        self, domain: str, name_fragment: str
    ) -> Optional[EntityInfo]:
        """Find the best entity matching *name_fragment* within *domain*.

        Matches against ``friendly_name`` (or entity_id as fallback),
        case-insensitive substring match.  Returns the first match.
        """
        needle = name_fragment.strip().lower()
        best: Optional[EntityInfo] = None

        for e in self.entities:
            if e.domain != domain:
                continue
            if needle in e.name_for_match:
                # Prefer an earlier entity if there are multiple matches;
                # exact name match beats substring.
                if needle == e.name_for_match:
                    return e
                if best is None:
                    best = e
        return best

    def list_by_domain(self, domain: str) -> list[EntityInfo]:
        """Return all cached entities for a domain (e.g. 'light', 'switch')."""
        return [e for e in self.entities if e.domain == domain]

    # ── service calls ────────────────────────────────────────

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: Optional[str] = None,
        extra_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Call a Home Assistant service.

        Example:
            await ha.call_service("light", "turn_on", entity_id="light.kitchen")
        """
        body: dict[str, Any] = {}
        if entity_id:
            body["entity_id"] = entity_id
        if extra_data:
            body.update(extra_data)

        resp = await self._c.post(f"/api/services/{domain}/{service}", json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_state(self, entity_id: str) -> Optional[EntityInfo]:
        """Fetch a single entity's current state (live, not cached)."""
        resp = await self._c.get(f"/api/states/{entity_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        item: dict[str, Any] = resp.json()
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        attrs = item.get("attributes", {}) or {}
        return EntityInfo(
            entity_id=entity_id,
            domain=domain,
            state=str(item.get("state", "unknown")),
            friendly_name=str(attrs.get("friendly_name", "")),
            attributes=attrs,
        )
