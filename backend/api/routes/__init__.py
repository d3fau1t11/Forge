"""FORGE API route package.

The former monolithic ``backend/api/routes.py`` is split into domain modules, each
owning its own ``APIRouter`` and only the imports it uses. This package re-exports a
single combining ``router`` so ``backend.main`` can keep doing
``from backend.api.routes import router as api_router`` unchanged, and every URL path
stays byte-for-byte identical.
"""

from fastapi import APIRouter

from . import (
    challenges,
    execution,
    privilege_and_approvals,
    providers_and_memory,
    runs_and_checkpoints,
    system,
    targets,
)

router = APIRouter()
router.include_router(challenges.router)
router.include_router(runs_and_checkpoints.router)
router.include_router(privilege_and_approvals.router)
router.include_router(execution.router)
router.include_router(targets.router)
router.include_router(providers_and_memory.router)
router.include_router(system.router)
