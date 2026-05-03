from __future__ import annotations

import asyncio
from typing import Optional

from data_fetcher.simbrief.simbrief_client import SimBriefClient
from data_fetcher.simbrief.simbrief_models import SimBriefPerformanceSeed
from data_fetcher.simbrief.simbrief_normalizer import to_performance_seed

class SimBriefService:
    def __init__(self):
        self.client = SimBriefClient()

    async def get_performance_seed(self, username: str) -> SimBriefPerformanceSeed:
        raw = await self.client.fetch_latest_ofp(username=username)
        return to_performance_seed(raw)
    
async def main():
    service = SimBriefService()

    ofp = await service.get_performance_seed(username="adennis200")

    print(ofp.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())