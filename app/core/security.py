"""Simple API-key protection and secret masking."""
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


async def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """If APP_API_KEY is configured, require a matching X-API-Key header."""
    expected = get_settings().APP_API_KEY
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def mask(value: str, keep: int = 4) -> str:
    """Mask a secret for logging: 'abcd****'."""
    if not value:
        return "<empty>"
    return value[:keep] + "*" * max(len(value) - keep, 4)
