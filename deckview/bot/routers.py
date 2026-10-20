"""Composition root for modular Telegram feature routers."""

from __future__ import annotations

import time
from collections.abc import Callable

from aiogram import Router

from deckview.handlers.arena import create_arena_router
from deckview.handlers.battlegrounds import create_battlegrounds_router
from deckview.handlers.health import create_health_router
from deckview.handlers.high_resolution import create_high_resolution_router
from deckview.services.health_service import HealthService


def create_modular_router(
    *,
    is_admin: Callable[[int], bool] | None = None,
    started_at: float | None = None,
    image_theme_provider: Callable | None = None,
) -> Router:
    """Assemble migrated feature routers in deterministic priority order."""

    router = Router(name="deckview")
    router.include_router(create_high_resolution_router(image_theme_provider=image_theme_provider))
    router.include_router(create_arena_router())
    router.include_router(create_battlegrounds_router())
    router.include_router(
        create_health_router(
            service=HealthService(
                started_at=time.monotonic() if started_at is None else started_at
            ),
            is_admin=is_admin or (lambda _user_id: False),
        )
    )
    return router
