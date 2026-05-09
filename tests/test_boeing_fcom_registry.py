from __future__ import annotations

import unittest

from optimizer.config_loader import load_aircraft_config
from performance_engine.database.boeing_fcom_registry import (
    resolve_boeing_777_fcom_variant,
)


class BoeingFcomRegistryTests(unittest.TestCase):
    def test_b772_default_uses_configured_ge90_94b_fcom_variant(self) -> None:
        variant = resolve_boeing_777_fcom_variant(
            request_aircraft="B772",
            aircraft_cfg=load_aircraft_config("b772"),
            engine_variant=None,
        )

        self.assertEqual(variant, "b772_ge90_94b_faa")

    def test_b772_ge90_94b_request_uses_supported_fcom_variant(self) -> None:
        variant = resolve_boeing_777_fcom_variant(
            request_aircraft="B772",
            aircraft_cfg=load_aircraft_config("b772"),
            engine_variant="GE90-94B",
        )

        self.assertEqual(variant, "b772_ge90_94b_faa")

    def test_b772_pw4000_request_does_not_fall_through_to_ge90_fcom(self) -> None:
        variant = resolve_boeing_777_fcom_variant(
            request_aircraft="B772",
            aircraft_cfg=load_aircraft_config("b772"),
            engine_variant="PW4090",
        )

        self.assertIsNone(variant)

    def test_b772_trent800_request_does_not_fall_through_to_ge90_fcom(self) -> None:
        variant = resolve_boeing_777_fcom_variant(
            request_aircraft="B772",
            aircraft_cfg=load_aircraft_config("b772"),
            engine_variant="Trent 895",
        )

        self.assertIsNone(variant)


if __name__ == "__main__":
    unittest.main()
