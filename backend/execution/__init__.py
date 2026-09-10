"""
backend.execution — Phase 3 Tool Runtime / Execution Layer.

Public API surface used by the rest of FORGE:

    from backend.execution.service import execution_service
    result = await execution_service.execute(request)

Base types are importable directly from this package:

    from backend.execution import ExecutionRequest, ExecutionResult

execution_service is NOT exported here to avoid a circular import at
package-init time (manager.py → execution.service → backends.local → manager.py).
Import it explicitly from backend.execution.service when needed.
"""
from backend.execution.base import (  # noqa: F401
    ExecutionRequest,
    ExecutionResult,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_TIMEOUT,
    STATUS_MISSING_TOOL,
    STATUS_KILLED,
)
