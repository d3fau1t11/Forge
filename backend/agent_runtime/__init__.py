"""
FORGE Agent Runtime — a HERMES-inspired, FORGE-native autonomous agent runtime.

Central principle: **THE MODEL IS NOT THE MEMORY.** FORGE owns the complete
session/trajectory state, so any external LLM provider can be replaced at any time
without losing the mission. Every agent turn is preserved as

    command → stdout/stderr → observation → state change → decision → next action

Public surface
--------------
    AgentRuntime, RunResult, RealToolExecutor        — the execution loop
    SessionManager, AgentSession, session_manager     — session lifecycle
    TrajectoryStore, TrajectorySearch,                — durable trajectory + FTS5 recall
        trajectory_store, trajectory_search
    MissionState, StateDelta                          — structured mission state
    ObservationEngine, Observation                    — evidence-only observations
    DecisionEngine, Decision, ProviderGateway,        — model decision + provider independence
        RouterProviderGateway
    Action, ActionType, ActionValidator, ExecResult   — actions (validation ≠ execution)
    RepetitionDetector, RepetitionKind                — repetition detection
    RecoveryEngine, RecoveryStrategy                  — failure recovery
    FlagVerifier, FlagStatus, FlagSource, FlagVerdict — flag candidate vs verified
"""

from backend.agent_runtime.state import MissionState, StateDelta
from backend.agent_runtime.observation import Observation, ObservationEngine
from backend.agent_runtime.action import (
    Action, ActionType, ActionValidator, ExecResult, ToolExecutor, ValidationResult,
)
from backend.agent_runtime.decision import (
    Decision, DecisionEngine, DecisionResult, ProviderCompletion, ProviderGateway,
    RouterProviderGateway,
)
from backend.agent_runtime.repetition import RepetitionDetector, RepetitionKind, RepetitionReport
from backend.agent_runtime.recovery import (
    RecoveryEngine, RecoveryPlan, RecoveryStrategy, FailureCategory,
)
from backend.agent_runtime.verifier import (
    FlagVerifier, FlagStatus, FlagSource, FlagVerdict, FLAG_REGEX, FALSE_FLAG_PATTERNS,
)
from backend.agent_runtime.context import ContextBuilder, BuiltContext
from backend.agent_runtime.trajectory import (
    TrajectoryStore, TrajectorySearch, trajectory_store, trajectory_search,
)
from backend.agent_runtime.session import AgentSession, SessionManager, session_manager
from backend.agent_runtime.runtime import AgentRuntime, RunResult, RealToolExecutor
from backend.agent_runtime.execution_backend import (
    CapabilityReport, ExecutionBackend, LocalExecutionBackend, execution_backend,
)
from backend.agent_runtime.learning import RuntimeBoardAdapter, RuntimeLearner, runtime_learner

__all__ = [
    "MissionState", "StateDelta",
    "Observation", "ObservationEngine",
    "Action", "ActionType", "ActionValidator", "ExecResult", "ToolExecutor", "ValidationResult",
    "Decision", "DecisionEngine", "DecisionResult", "ProviderCompletion", "ProviderGateway",
    "RouterProviderGateway",
    "RepetitionDetector", "RepetitionKind", "RepetitionReport",
    "RecoveryEngine", "RecoveryPlan", "RecoveryStrategy", "FailureCategory",
    "FlagVerifier", "FlagStatus", "FlagSource", "FlagVerdict", "FLAG_REGEX", "FALSE_FLAG_PATTERNS",
    "ContextBuilder", "BuiltContext",
    "TrajectoryStore", "TrajectorySearch", "trajectory_store", "trajectory_search",
    "AgentSession", "SessionManager", "session_manager",
    "AgentRuntime", "RunResult", "RealToolExecutor",
    "CapabilityReport", "ExecutionBackend", "LocalExecutionBackend", "execution_backend",
    "RuntimeBoardAdapter", "RuntimeLearner", "runtime_learner",
]
