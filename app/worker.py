from __future__ import annotations

import asyncio

from app.config import load_settings
from app.services.harvester import HarvesterWorker


async def main() -> None:
    settings = load_settings()
    worker = HarvesterWorker(settings=settings, worker_id=settings.harvester.worker_id)
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
