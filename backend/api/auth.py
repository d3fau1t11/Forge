"""Local API bearer-token authentication for FORGE.

A single high-entropy bearer token is generated for each backend process
(see ``backend.config.settings.API_TOKEN``). This module exposes one reusable
FastAPI dependency, :func:`require_api_token`, which requires
``Authorization: Bearer <token>`` and compares the supplied token using
``secrets.compare_digest`` (constant-time).

This is authentication only (caller identity for this local backend). It is
orthogonal to, and does not replace, the existing agent privilege manager
(``backend.privilege.manager``).
"""

import secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from backend.config import settings

# auto_error=False so we can raise our own HTTP 401 (with WWW-Authenticate)
# for missing / malformed / invalid credentials instead of Starlette's 403.
bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    """Require a valid bearer token on protected routes.

    Returns the validated token string on success. Raises HTTP 401 for:
      * missing Authorization header
      * malformed / non-Bearer credentials
      * an invalid or wrong token

    The comparison is constant-time via ``secrets.compare_digest``.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(credentials.credentials, settings.API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials
