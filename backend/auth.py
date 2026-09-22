"""Router-level API key gate for every /api route.

Attached once in backend/main.py via `app.include_router(..., dependencies=[...])`
so coverage is structural: no handler in backend/api/routes.py can be added later
and accidentally ship unauthenticated.

Dev mode: when FORGE_API_KEY is empty the dependency is a no-op, preserving the
existing local workflow. Set FORGE_API_KEY before exposing FORGE off localhost.
"""

from fastapi import Header, HTTPException

from backend.config import settings


async def require_api_key(x_forge_key: str = Header(default="")):
    if not settings.FORGE_API_KEY:
        return  # no key configured: auth disabled, dev mode
    if x_forge_key != settings.FORGE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Forge-Key header")
