"""Aggregate all sub-routers into a single ``main_router`` for ``bot.main``."""

from aiogram import Router

from .links import router as links_router
from .media import router as media_router
from .repeat import router as repeat_router
from .start import router as start_router

main_router = Router()
main_router.include_router(start_router)
main_router.include_router(repeat_router)
main_router.include_router(media_router)
main_router.include_router(links_router)

__all__ = ["main_router"]
