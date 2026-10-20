from __future__ import annotations

import unittest

from deckview.bot.routers import create_modular_router
from deckview.services.arena_service import ArenaService
from deckview.services.battlegrounds_service import BattlegroundsService


class ModularRouterTest(unittest.TestCase):
    def test_composition_root_registers_named_feature_routers(self) -> None:
        router = create_modular_router()
        self.assertEqual("deckview", router.name)
        self.assertEqual(
            ["high_resolution", "arena", "battlegrounds", "health"],
            [child.name for child in router.sub_routers],
        )
        self.assertEqual({"message", "callback_query"}, set(router.resolve_used_update_types()))

class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_arena_service_is_injectable_without_telegram(self) -> None:
        service = ArenaService(
            loader=lambda: {"stats": []},
            formatter=lambda data, period: f"{period}:{len(data['stats'])}",
        )
        self.assertEqual("hsreplay:0", await service.current_overview())

    async def test_battlegrounds_service_validates_period(self) -> None:
        service = BattlegroundsService(
            loader=lambda period: {"period": period},
            formatter=lambda data, period: data["period"],
        )
        self.assertEqual("past-seven", await service.overview("past-seven"))
        with self.assertRaises(ValueError):
            await service.overview("unknown")


if __name__ == "__main__":
    unittest.main()
