from abc import ABC, abstractmethod

from data_fetcher.sim.sim_models import LiveSimState


class SimClientError(RuntimeError):
    pass


class SimClient(ABC):
    @abstractmethod
    async def get_live_state(self) -> LiveSimState:
        raise NotImplementedError

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass