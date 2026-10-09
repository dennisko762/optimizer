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

    def set_target_state(
        self,
        *,
        flight_level: int | None = None,
        mach: float | None = None,
    ) -> dict:
        """
        Set the sim's target cruise state (flight level and/or Mach).

        Returns a plain dict: {"applied": bool, "errors": [...], plus
        per-target details}. The default implementation (e.g. remote sim
        bridge without write support) reports a clean failure so the UI
        can disable the Apply action instead of showing dead buttons.
        """
        return {
            "applied": False,
            "supported": False,
            "errors": [
                "This sim client does not support target-state commands "
                "(set_target_state)."
            ],
        }
