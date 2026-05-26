"""Main bridge loop — connects to pyMC, listens for DMs, dispatches to HA."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from meshcore import MeshCore, EventType

from .auth import Auth
from .commands import CommandRouter
from .config import Config, load_config
from .ha_client import HAClient

logger = logging.getLogger("meshcore_ha_bridge")


async def _create_meshcore_connection(config: Config) -> MeshCore:
    """Create a MeshCore connection based on config transport."""
    mc_cfg = config.meshcore
    transport = mc_cfg.transport

    if transport == "tcp":
        logger.info("Connecting via TCP to %s:%d", mc_cfg.host, mc_cfg.port)
        return await MeshCore.create_tcp(
            mc_cfg.host, mc_cfg.port, debug=mc_cfg.debug
        )
    elif transport == "serial":
        logger.info("Connecting via serial: %s @ %d baud", mc_cfg.serial_port, mc_cfg.baud_rate)
        return await MeshCore.create_serial(
            mc_cfg.serial_port, mc_cfg.baud_rate, debug=mc_cfg.debug
        )
    else:
        raise ValueError(f"Unsupported transport: {transport}")


async def run_bridge(config_path: str = "config.yaml") -> None:
    """Entry point: load config, connect, and run the bridge loop.

    Never returns unless cancelled — runs until KeyboardInterrupt.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # ── load config ──────────────────────────────────────────
    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    logger.info("Starting meshcore-homeassistant-bridge")
    logger.info("Allowlist: %d entries", len(config.allowlist))

    # ── initialise components ────────────────────────────────
    auth = Auth(config.allowlist)
    ha = HAClient(config.home_assistant)

    # ── connect to pyMC ──────────────────────────────────────
    try:
        mc = await _create_meshcore_connection(config)
    except Exception as exc:
        logger.error("Failed to connect to Meshcore: %s", exc)
        sys.exit(1)

    await mc.start_auto_message_fetching()
    logger.info("Connected to Meshcore — listening for DMs")

    # ── start HA client ──────────────────────────────────────
    await ha.start()
    entity_count = len(ha.entities)
    logger.info("Home Assistant: %d entities loaded", entity_count)

    router = CommandRouter(ha, config)

    # ── event handlers ───────────────────────────────────────

    async def on_connected(event: Any) -> None:
        payload = event.payload or {}
        reconnected = payload.get("reconnected", False)
        tag = "reconnected" if reconnected else "connected"
        logger.info("Meshcore %s", tag)

    async def on_disconnected(event: Any) -> None:
        payload = event.payload or {}
        reason = payload.get("reason", "unknown")
        logger.warning("Meshcore disconnected: %s", reason)

    async def on_dm(event: Any) -> None:
        """Handle an incoming DM (CONTACT_MSG_RECV)."""
        payload: dict[str, Any] = event.payload or {}
        pubkey_prefix: str = payload.get("pubkey_prefix", "")
        text: str = payload.get("text", "")
        path_len: int = payload.get("path_len", 0)

        if not text.strip():
            return

        # ── auth check ──
        result = auth.lookup(pubkey_prefix)
        if not result.allowed:
            logger.info(
                "Rejected DM from unauthorised prefix %s: %r",
                pubkey_prefix[:12],
                text[:60],
            )
            reply = (
                f"Unauthorised. Your key prefix: {pubkey_prefix[:12]}. "
                "Ask the admin to add you."
            )
            # Still reply so the sender knows they're not allowlisted.
            # But with a rate-limit consideration — only reply once per prefix
            # per session. For v1 we always reply.
            await mc.commands.send_msg(pubkey_prefix, reply)
            return

        sender_name = result.entry.name if result.entry else pubkey_prefix[:12]
        logger.info(
            "DM from %s (%s, %d hops): %r",
            sender_name,
            pubkey_prefix[:12],
            path_len,
            text[:80],
        )

        # ── dispatch command ──
        reply = await router.dispatch(text)
        logger.info("Reply to %s: %r", sender_name, reply[:80])

        # ── send reply ──
        send_result = await mc.commands.send_msg(pubkey_prefix, reply)
        if send_result.type == EventType.ERROR:
            logger.error("Failed to send reply: %s", send_result.payload)

    # ── subscribe ────────────────────────────────────────────
    mc.subscribe(EventType.CONNECTED, on_connected)
    mc.subscribe(EventType.DISCONNECTED, on_disconnected)
    mc.subscribe(EventType.CONTACT_MSG_RECV, on_dm)

    logger.info("Bridge running — press Ctrl+C to stop")

    # ── keep alive ───────────────────────────────────────────
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down…")
        await mc.stop_auto_message_fetching()
        await ha.stop()
        await mc.disconnect()
        logger.info("Disconnected")
