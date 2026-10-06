"""Entry point: ``python -m gree_mqtt``."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .app import App
from .config import load_config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    config = load_config()
    logging.getLogger().setLevel(config.log_level)
    logging.getLogger("aiomqtt").setLevel(logging.WARNING)

    async def runner() -> None:
        app = App(config)
        task = asyncio.create_task(app.run())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            logging.getLogger(__name__).info("Stopped")

    asyncio.run(runner())


if __name__ == "__main__":
    main()
