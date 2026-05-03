from abc import ABC, abstractmethod

from data_fetcher.sim.sim_models import LiveSimState



class SimClient(ABC):
    @abstractmethod
    async def get_live_state(self) -> LiveSimState:
        raise NotImplementedError