"""Entry point: ``python -m meshcore_ha_bridge`` or ``meshcore-ha-bridge``."""

import asyncio
import sys

from .bridge import run_bridge


def main() -> None:
    try:
        asyncio.run(run_bridge())
    except KeyboardInterrupt:
        print("\nShutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
