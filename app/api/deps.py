"""Shared service instances (overridable in tests via app.dependency_overrides)."""
from functools import lru_cache

from app.services.claude_service import ClaudeService
from app.services.stock_service import StockService


@lru_cache
def get_stock_service() -> StockService:
    return StockService()


@lru_cache
def get_claude_service() -> ClaudeService:
    return ClaudeService()


@lru_cache
def get_board_service():
    from app.services.priceboard_service import PriceBoardService

    return PriceBoardService(get_stock_service())


def get_telegram_service():
    from app.services.telegram_service import TelegramService

    return TelegramService()
