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
    STATUS_CANCELLED,
    STATUS_BLOCKED_CAPABILITY,
    STATUS_TARGET_MISMATCH,
)

# Phase 4.x — interactive execution, target modelling, capability discovery,
# acquisition, and OCR. These are cycle-free at import time (none import
# backend.tools.manager or backend.execution.service at module load), so unlike
# execution_service they can be re-exported here.
from backend.execution.interactive import (  # noqa: F401
    InteractiveSession,
    InteractiveSessionManager,
    InteractiveSessionSpec,
    ReadResult,
    interactive_manager,
)
from backend.execution.targets import (  # noqa: F401
    TargetType,
    Target,
    TargetMismatch,
    TargetDetector,
    target_detector,
    detect_target,
    detect_targets,
)
from backend.execution.capabilities import (  # noqa: F401
    Capability,
    CapabilityService,
    ProviderKind,
    ProviderSpec,
    CAPABILITY_REGISTRY,
    capability_service,
    AVAILABLE,
    ALTERNATIVE_AVAILABLE,
    ACQUIRABLE,
    BLOCKED,
)
from backend.execution.acquisition import (  # noqa: F401
    AcquisitionMethod,
    AcquisitionPlan,
    AcquisitionResult,
    AcquisitionPlanner,
    acquisition_planner,
)
from backend.execution.ocr import (  # noqa: F401
    OCRService,
    OCRResult,
    ocr_service,
)
