"""Configuration loader — reads and validates config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ── data models ──────────────────────────────────────────────────


@dataclass
class AllowlistEntry:
    pubkey: str  # 64-char hex Ed25519 public key
    name: str    # human-readable label for the owner


@dataclass
class MeshcoreConfig:
    transport: str = "tcp"         # "tcp" | "serial" | "ble"
    host: str = "127.0.0.1"       # TCP host (for transport=tcp)
    port: int = 5000               # TCP port
    serial_port: str = "/dev/ttyUSB0"  # for transport=serial
    baud_rate: int = 115200        # for transport=serial

    debug: bool = False


@dataclass
class HAConfig:
    url: str = "http://homeassistant.local:8123"
    token: str = ""
    request_timeout: float = 10.0  # seconds


@dataclass
class Config:
    meshcore: MeshcoreConfig = field(default_factory=MeshcoreConfig)
    home_assistant: HAConfig = field(default_factory=HAConfig)
    allowlist: list[AllowlistEntry] = field(default_factory=list)
    dm_reply_max_chars: int = 200  # Meshcore message size is limited


# ── loader ───────────────────────────────────────────────────────


def _parse_allowlist(raw: list[dict]) -> list[AllowlistEntry]:
    entries: list[AllowlistEntry] = []
    for item in raw:
        pubkey = str(item.get("pubkey", "")).strip().lower()
        name = str(item.get("name", "")).strip()
        if not pubkey:
            raise ValueError("allowlist entry missing 'pubkey'")
        if len(pubkey) != 64:
            raise ValueError(
                f"Invalid pubkey length ({len(pubkey)} chars, expected 64): {pubkey[:12]}…"
            )
        entries.append(AllowlistEntry(pubkey=pubkey, name=name or pubkey[:12]))
    return entries


def load_config(path: str | Path = "config.yaml") -> Config:
    """Load and validate a config.yaml file.

    Raises ValueError or FileNotFoundError on issues.
    """
    raw_path = Path(path)
    if not raw_path.exists():
        raise FileNotFoundError(f"Config file not found: {raw_path}")

    with open(raw_path, "r") as fh:
        raw = yaml.safe_load(fh) or {}

    # ── meshcore section ──
    mc_raw = raw.get("meshcore", {})
    mc = MeshcoreConfig(
        transport=str(mc_raw.get("transport", "tcp")).lower(),
        host=str(mc_raw.get("host", "127.0.0.1")),
        port=int(mc_raw.get("port", 5000)),
        serial_port=str(mc_raw.get("serial_port", "/dev/ttyUSB0")),
        baud_rate=int(mc_raw.get("baud_rate", 115200)),
        debug=bool(mc_raw.get("debug", False)),
    )

    # ── home_assistant section ──
    ha_raw = raw.get("home_assistant", {})
    ha = HAConfig(
        url=str(ha_raw.get("url", "")).rstrip("/"),
        token=str(ha_raw.get("token", "")),
        request_timeout=float(ha_raw.get("request_timeout", 10.0)),
    )
    if not ha.url:
        raise ValueError("home_assistant.url is required")
    if not ha.token:
        raise ValueError("home_assistant.token is required (long-lived access token)")

    # ── allowlist section ──
    allowlist = _parse_allowlist(raw.get("allowlist", []))
    if not allowlist:
        raise ValueError("allowlist must contain at least one entry")

    return Config(
        meshcore=mc,
        home_assistant=ha,
        allowlist=allowlist,
        dm_reply_max_chars=int(raw.get("dm_reply_max_chars", 200)),
    )
