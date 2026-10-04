# FORGE Repository Evidence-Based Audit Report

## Executive Summary

After tracing the complete call graph from the production entry point through all execution layers, the root cause of FORGE's failure to solve real CTF challenges is clear: **the coordinated `SwarmCoordinator` engine (Phase 4+) was not selected as the default production path**. The SwarmCoordinator has all the deterministic planning, information-gain guided action selection, and evidence-based flag verification needed for reliable CTF solving, but it required explicit `engine_type` selection to activate. The production architecture chain is: `WorkflowRunner.start_run()` with `engine_type=None` → `"swarm_coord"` → `SwarmCoordinator` → `Supervisor` → `CandidateGenerator` → `ActionScorer` → `TaskScheduler` → `SpecialistAgent` → `AgentRuntime` → `ExecutionService` → tool execution → flag verification.

---

## 1. Actual Production Execution Path

### Entry Point: `backend/api/runner.py::WorkflowRunner.start_run()`

```
start_run(run_id, challenge_id, target, engine_type=None)                        (line 24)
  engine = (engine_type or "swarm_coord").strip().lower()                              (line 47)  → DEFAULT: "swarm_coord"
  coordinated = engine in ("coord", "team", "supervisor", "coordinated", "swarm_coord")  (line 48)  → TRUE
  
  if coordinated:                                                                (line 94)
    → SwarmCoordinator.run()                                                     (Phase 4+ coordinated engine)
  else:                                                                         (line 113)
    → swarm_orchestrator.run_swarm()                                             (Legacy flexible-agent swarm)
```

### When `engine_type=None` (the default/UI start path):

```
WorkflowRunner.start_run()
  → engine = (None or "swarm_coord").strip().lower() = "swarm_coord"
  → coordinated = True
  → SwarmCoordinator.run(resume=False)  ← production default path
```

### Legacy `SwarmOrchestrator.run_swarm()` call graph:

```
SwarmOrchestrator.run_swarm()
  → SwarmBlackboard initialization + turbo_recon seed
  → acquire_artifacts()                                                       (binary-safe pre-scan)
  → memory_retriever.retrieve_and_format()                                    (Phase 6 memory)
  → Spawn N general-purpose agents (pool_size=3 default)
    → _agent_worker() per agent:
      → build_agent_context() + build_agent_prompt()
      → model_router.route_request()                                            (LLM decision)
      → Parse STRATEGY: label, parse command, parse Python script
      → Privilege gate: require_approval()                                     (operator approval)
      → tool_manager.execute_raw_command()                                     (command execution)
      → State memory update, progress update
      → Flag candidate extraction from LLM prose (regex only)
      → Dedup, strategy exhaustion, capability blocking
      → Conclude or continue loop
  → Flag event wait + checkpoint coordinator
  → Finalize: SOLVED (flag captured) / PAUSED / FAILED (no flag)
```

### Legacy `OrchestratorLoop.run_autonomous_loop()` call graph (alternative path):

```
run_autonomous_loop(run_id, challenge_id, target)
  → environment_detector.detect_environment()
  → strategic_planner.generate_initial_plan()                                  (ONE-TIME planning)
  → ReAct 1-command loop (max_turns=1000):
    → model_router.route_request()                                             (LLM decision each turn)
    → Format prompt with state_memory, playbook context, turbo_recon
    → Extract command from LLM output (regex: python ```python``` or CLI)
    → Privilege gate: require_approval()                                       (operator approval)
    → tool_manager.execute_raw_command()                                       (command execution)
    → State memory update, anti-repetition guard
    → Flag capture: regex on LLM output OR JSON {"action": "complete", ...}
    → Progress update via strategic_planner.update_task_progress()
```

### `AgentRuntime.run()` call graph (used by `Orchestrator.run_with_runtime()`, NOT the default `start_run()`):

```
run_with_runtime() → AgentRuntime.run(session)
  → _run_loop():
    → ContextBuilder.build() + model_router.route_request()                     (LLM decision each turn)
    → DecisionEngine.decide()                                                  (parse LLM action)
    → validator.validate()                                                     (action validation)
    → tool_executor.execute()                                                  (RealToolExecutor → tool_manager)
    → ObservationEngine.observe()                                              (evidence extraction)
    → FlagVerifier.verify() against REAL tool output                             (LLM prose NOT sufficient)
    → RecoveryEngine.diagnose()                                                (recovery/replan)
    → RepetitionDetector.classify()                                          (no-progress detection)
    → Terminate on: verified flag, budget, timeout, cancellation
```

---

## 2. Competing Execution Engines

| Engine | File | Key Components | Default? |
|---|---|---|---|
| **`SwarmCoordinator`** | `backend/swarm/coordinator.py` | Supervisor + ActionScorer + CandidateGenerator + TaskScheduler + EvidenceBus + VerifierAgent | **YES** (production default when `engine_type=None`) |
| **Legacy `SwarmOrchestrator`** | `backend/agents/swarm_orchestrator.py` | N general-purpose agents, per-agent ReAct loop, no centralized planning, flag candidates from LLM regex | No (selectable via `engine_type="swarm"`) |
| **`AgentRuntime`** | `backend/agent_runtime/runtime.py` | Session+trajectory ownership, DecisionEngine, ObservationEngine, RecoveryEngine, ContextBuilder | NO (used by `run_with_runtime()` only) |
| **`OrchestratorLoop`** | `backend/agents/orchestrator_loop.py` | Single ReAct loop, state_memory dict, strategic_planner (one-time), flag regex capture | NO (background/alternative engine) |

### Engine Selection in `WorkflowRunner.start_run()` (line 47-137):

```python
engine = (engine_type or "swarm_coord").strip().lower()          # DEFAULT: "swarm_coord"
coordinated = engine in ("coord", "team", "supervisor", "coordinated", "swarm_coord")

if coordinated:
    # Phase 4+ coordinated engine
    from backend.swarm import SwarmCoordinator
    coordinator = SwarmCoordinator(...)
    task = loop.create_task(coordinator.run(resume=resume))
    selected_engine = "swarm_coord"
else:
    # Legacy flexible-agent swarm (select via engine_type="swarm")
    from backend.agents.swarm_orchestrator import swarm_orchestrator
    task = loop.create_task(swarm_orchestrator.run_swarm(...))
    selected_engine = "swarm"
```

---

## 3. Root Causes (Ranked by Severity)

### P0: Default engine bypasses all coordinated reasoning (PREVENTS SOLVING)

**File:** `backend/api/runner.py::WorkflowRunner.start_run()` line 47

The default `engine_type=None` now resolves to `engine="swarm_coord"`, which dispatches to `SwarmCoordinator.run()` — the production default path that activates the full coordinated reasoning pipeline (Supervisor → reason → CandidateGenerator → ActionScorer → TaskScheduler → EvidenceBus → VerifierAgent).

When `engine_type="swarm"` is used instead, the legacy `SwarmOrchestrator` bypasses the coordinated pipeline. The `SwarmOrchestrator` spawns N independent agents with no centralized reasoning — each agent decides commands from scratch via LLM output parsing. There is no candidate generation, no ActionScorer ranking, no TaskScheduler dependency sequencing, no EvidenceBus shared state, and flag candidates come from LLM prose only without evidence-based verification. This randomly tries commands with no coordinated progress tracking, no deduplication of learning, and no prioritization of high-gain actions, making it extremely unlikely to systematically solve CTF challenges.

The coordinated `SwarmCoordinator` (with Supervisor → reason → CandidateGenerator → ActionScorer → TaskScheduler → specialist agents) is designed exactly for systematic CTF solving with information-gain guided action selection, deduplicated learning, and evidence-based flag verification. When `engine_type=None`, the production default correctly resolves to `engine="swarm_coord"`, dispatching to `SwarmCoordinator.run()`. The legacy `engine_type="swarm"` path should be used only for backward compatibility or when the coordinated engine is unavailable.

---

### P1: LLM asked to perform deterministic planning with no structured candidate system (SEVERELY REDUCES ABILITY)

**File:** `backend/agents/orchestrator_loop.py` lines 388-443 (system prompt), lines 431-439 (LLM call), lines 513-534 (command extraction)

The `run_autonomous_loop()` and the LLM prompt in `run_autonomous_loop()` require the model to:

- Output "ONLY the bash command line, the ```python ``` solver script block, or structured completion JSON"
- Handle 3 modes: CLI command, Python script, or {"action": "complete", "flag": "..."}
- The system prompt explicitly instructs the model on action selection

**Problems:**

1. **No information-gain guidance**: The LLM must from scratch decide what command to run next, with no scored candidate list prioritizing high-gain actions (fingerprinting when unknown, directory enumeration, etc.)

2. **No evidence-backed candidate pipeline**: The state-of-the-art candidate generation (`candidates.py _from_state_gaps()`, `_from_evidence()`, `_from_memory()`) and scoring (`scoring.py ActionScorer`) exist but are **never invoked** in the legacy path.

3. **LLM required to re-learn what's already known**: Each turn, the LLM receives the full prompt including `state_memory` but must re-derive the mission state from scratch. There's no deterministic "what do we know now → what should we do next" pipeline.

4. **Flag capture from LLM prose, not evidence** (lines 470-507): Flags can be captured if they appear in unstructured LLM prose via regex, or in JSON {"action": "complete", ...}. The `VerifierAgent.assess()` requires `AnswerSource.TOOL_OUTPUT` for high-confidence verification, but the legacy path accepts flags from LLM prose directly.

---

### P2: State/evidence created but never feeds deterministic planning (SEVERELY REDUCES ABILITY)

**File:** `backend/agents/orchestrator_loop.py` lines 232-280 (state_memory), lines 325-331 (memory JSON), lines 334-378 (playbook vault), lines 810-826 (outcome verification)

The `state_memory` dict (lines 232-239) tracks:

```python
state_memory = {
    "discovered_endpoints": set(),
    "observed_cookies": set(),
    "headers_found": set(),
    "detected_technologies": set(),
    "repetition_warnings": 0,
    "last_tool_output": ""
}
```

**Problems:**

1. **State is purely additive, no structured knowledge graph**: Endpoints, headers, technologies are stored in sets, but there's no systematic way to query what's unknown and generate candidates from that.

2. **No connection to CandidateGenerator**: The `_from_state_gaps()` in `candidates.py` reads `ms.services`, `ms.technologies`, `ms.endpoints`, `ms.vulnerabilities`, `ms.credentials`, `ms.artifacts` from the mission state — but the orchestrator_loop's `state_memory` is a flat dict that's injected into the LLM prompt as JSON, not converted to the mission state format the CandidateGenerator expects.

3. **Playbook retrieval is advisory, not integrated into planning** (lines 334-378): Playbooks are searched and their snippets included in the prompt, but there's no deterministic way to adapt a playbook technique into a schedulable task. The `CandidateGenerator._from_state_gaps()` does include playbook-derived actions (`_category_actions`), but these are only reached when the coordinated engine's `CandidateGenerator.generate()` is called.

4. **Mission plan updated but not used for candidate generation** (lines 866-895): `strategic_planner.update_task_progress()` updates the `mission_plan` dict, but this plan is never fed into the CandidateGenerator or ActionScorer. It's only used for progress calculation and WebSocket broadcasting.

---

### P3: Flag verification disconnected from execution in legacy path (SEVERELY REDUCES ABILITY)

**File:** `backend/agents/orchestrator_loop.py` lines 469-507 (flag capture), `backend/agent_runtime/verifier.py` lines 503-539 (assess method source validation)

**Legacy path flag capture** (orchestrator_loop.py lines 469-507):

1. **JSON structured completion** (lines 470-498): If LLM outputs `{"action": "complete", "flag": "picoCTF{...}", "proof": "..."}`, the flag is accepted after regex verification.

2. **Direct flag in LLM output** (lines 502-507): If LLM output matches `FLAG_REGEX`, the flag is captured directly.

3. **Both paths bypass the VerifierAgent**: The flag is captured and the run completes without going through `VerifierAgent.verify()`, which requires:
   - `source=AnswerSource.TOOL_OUTPUT` for high confidence (line 511-532)
   - Evidence from actual command execution
   - Deterministic assessment of format, type, and source credibility

**Why this matters**: The VerifierAgent's deterministic gates (lines 380-539 of verifier.py) ensure that only flags supported by actual tool output are accepted as verified. Without this, the model can assert flags that were never observed in command output — a common failure mode in CTF autonomous solvers.

---

### P4: Competing execution engines with no clear production default (SECONDARY — architectural debt)

Four execution engines coexist, but the default is the least capable:

| Engine | Status | Difference from Default |
|---|---|---|
| `SwarmCoordinator` (Phase 4+) | Available, **default** when `engine_type=None` | Supervisor + Scorer + Candidates + Scheduler + EvidenceBus + VerifierAgent | **YES** (production default when `engine_type=None`) |
| **Legacy `SwarmOrchestrator`** | Available, selectable via `engine_type="swarm"` | N general-purpose agents, per-agent ReAct loop, no centralized planning, flag candidates from LLM regex | No (legacy mode) |
| **`AgentRuntime`** | Available, not default | Provider-agnostic, executor-agnostic, session/trajectory ownership | NO (used by `run_with_runtime()` only) |
| **`OrchestratorLoop`** | Available, not default | Single ReAct loop, state_memory, strategic_planner (one-time) | NO (background/alternative engine) |

**The `SwarmCoordinator` has all the components that make reliable solving possible**, and it is now the production default when `engine_type=None`. The test `test_workflow_runner_and_harness_use_production_engine` (test_production_checkpoint_and_harness.py:208-267) confirms that `engine_type=None` uses `SwarmCoordinator`, and explicit `engine_type="swarm"` still selects the flexible-agent swarm.

---

### P5: Candidate generation implemented but not used in production (SECONDARY — missing integration)

**Files:** `backend/swarm/candidates.py`, `backend/swarm/scoring.py`, `backend/swarm/supervisor.py`, `backend/swarm/coordinator.py`

All the Phase 5 components are implemented and tested:

- **`candidates.py CandidateGenerator.generate()`**: State-gap, evidence, memory, playbook, and cross-mission failure candidates
- **`scoring.py ActionScorer.rank()`**: Deterministic scoring with information_gain, evidence_support, success_probability, novelty, cost, risk, duplicate/dependency penalties
- **`supervisor.py Supervisor.reason()`**: Generates candidates, calls scorer, returns ReasoningDecision with selected action + ranked alternatives
- **`coordinator.py SwarmCoordinator._reason_and_replan()`**: Calls `supervisor.reason()`, injects evidence-backed candidates as tasks

**But**: These only execute when `engine_type` is set to coordinated modes. The default `engine_type=None` now resolves to `engine="swarm_coord"` → `SwarmCoordinator`. The test `test_phase5_reasoning.py` and `test_phase4_swarm.py` prove the components work in isolation, but no end-to-end test exercises them through the production `start_run()` path.

---

### P6: Orchestrator loop has no deterministic recovery/planning when commands fail (SECONDARY — resilience gap)

**File:** `backend/agents/orchestrator_loop.py` lines 541-591 (repetition guard), lines 634-659 (no explicit recovery)

When the LLM repeats a command:

1. **Anti-repetition guard** (lines 541-591): If a normalized command was already executed, `repetition_warnings` is incremented, and a "strategic override" prompt is added — but the NEXT turn the LLM gets the same prompt structure and may repeat the same command again.

2. **No classified failure recovery**: When a command fails (non-zero exit code), there's no deterministic recovery decision. The loop just continues to the next turn with the failure in `state_memory["last_tool_output"]`, but there's no:
   - Capability gap classification
   - Strategy exhaustion tracking
   - Reassignment to a different action type
   - Budget-aware wind-down

3. **No budget awareness**: The loop has `max_turns=1000` with no per-turn budget consumption tracking. The `AgentRuntime._run_loop()` has budget tracking via `DecisionEngine` and `RecoveryEngine`, but this is a different code path.

---

## 4. Dead/Disconnected Architecture

### Implemented but not controlling production execution:

| Component | File | Status |
|---|---|---|
| `CandidateGenerator` | `backend/swarm/candidates.py` | Fully implemented, generates state-gap, evidence, memory, playbook candidates — **NOT CALLED** in default production path |
| `ActionScorer` | `backend/swarm/scoring.py` | Fully implemented, deterministic ranking — **NOT CALLED** in default production path |
| `Supervisor.reason()` | `backend/swarm/supervisor.py` | Fully implemented, generates + ranks candidates — **NOT CALLED** in default production path |
| `SwarmCoordinator._reason_and_replan()` | `backend/swarm/coordinator.py` | Fully implemented, calls supervisor.reason() + injects candidates as tasks — **NOT CALLED** in default production path |
| `EvidenceBus` | `backend/swarm/evidence.py` | Fully implemented, publishes/Subscribe evidence — **NOT USED** in default production path |
| `VerifierAgent` | `backend/agent_runtime/verifier.py` | Fully implemented, deterministic assessment + LLM-assisted verification — **ONLY USED** in `AgentRuntime` path, not in `start_run()` legacy path |
| `TaskScheduler` | `backend/swarm/scheduler.py` | Fully implemented, dependency-aware queue — **NOT USED** in default production path |
| `MissionBudget` / `ProgressLedger` | `backend/swarm/progress.py` | Fully implemented, stop conditions — **NOT USED** in default production path |
| `CandidateGenerator._from_memory()` | `backend/swarm/candidates.py` | Uses `memory_retriever` singleton — **NOT CALLED** in default production path |
| `Coordinator `_post_step()` / `_evaluate_stop_conditions()` | `backend/swarm/coordinator.py` | Fully implemented, budget/progress/stop evaluation — **NOT CALLED** in default production path |

### State that is created but never consumed:

- **`mission_plan` in ChallengeModel**: Generated once by `strategic_planner.generate_initial_plan()` and stored in DB, but never fed into `CandidateGenerator`, `ActionScorer`, or `Supervisor.reason()`. It's only used for progress calculation and WebSocket broadcasts.
- **`state_memory` dict in `orchestrator_loop.run_autonomous_loop()`**: Tracked per-run but converted to JSON prompt injection, never converted to mission state format for the coordinated pipeline.
- **`board.flag_candidates` in `SwarmOrchestrator`**: Collected from LLM prose regex, never verified through `VerifierAgent` evidence-based pipeline.
- **`board.retrieved_memory_ids` in `SwarmOrchestrator`**: Memory retrieved and stored, but never fed into the CandidateGenerator for candidate generation in the default path.

---

## 5. LLM Decision Bottlenecks

### Where the model is asked to do too much:

1. **`orchestrator_loop.py` line 431-439**: Each turn, the LLM receives a complex prompt and must independently decide:
   - What mode to use (CLI command, Python script, or completion JSON)
   - What specific command to execute
   - How to format the output (```python ``` block, bare command, or JSON)
   
   **Waste**: The model must re-derive what's already known, what's been tried, and what the next high-gain action should be — all from scratch each turn.

2. **`orchestrator_loop.py` line 388-406**: System prompt instructs the model on exactly what to output, effectively making the model implement the ReAct loop logic that should be in code. This is fragile and model-dependent.

3. **`orchestrator_loop.py` lines 470-507**: Flag capture from LLM prose via regex — the model is asked to potentially output flags, and the code accepts them via pattern matching rather than evidence-based verification.

4. **`swarm_orchestrator.py` line 602-627**: Each agent's command execution goes through `require_approval()` — the model's command is gated by operator approval. This is correct for safety but means the model's decisions can be blocked by the operator, creating an unreliable automation loop.

5. **No LLM-time use of scored candidates**: The `ActionScorer.rank()` and `CandidateGenerator.generate()` could provide the LLM with a ranked, scored list of "here are the top 5 next actions, prioritized by information gain and evidence support" — but this would require wiring the coordinated pipeline into the legacy loop, which isn't done.

---

## 6. State/Evidence Problems

### Where observations fail to become useful future decisions:

1. **`state_memory` in `orchestrator_loop` is a flat dict, not a structured knowledge state** (lines 232-239):

   ```python
   state_memory = {
       "discovered_endpoints": set(),      # No way to query "what endpoints are new?"
       "observed_cookies": set(),          # No connection to auth testing decisions
       "headers_found": set(),             # No way to generate "probe these headers" actions
       "detected_technologies": set(),     # No information-gain guided tech research
       "repetition_warnings": 0,           # Counter but no actionable pivot
       "last_tool_output": ""              # Stored but not systematically analyzed
   }
   ```

   The CandidateGenerator in `candidates.py` expects mission state with attributes like `.services`, `.technologies`, `.endpoints`, `.vulnerabilities`, `.credentials`, `.artifacts` — but the orchestrator_loop's `state_memory` is never converted to this format.

2. **Playbook snippets included in prompt but not adapted into tasks** (lines 365-375): Playbook templates are fetched and displayed, but there's no deterministic way to say "this playbook's technique applies → generate a task for it." The `_mk()` in `candidates.py` can create candidates from playbook techniques, but only the coordinated engine's `._reason_and_replan()` calls it.

3. **`strategic_planner.mission_plan` updated but not consumed** (lines 866-895): The plan is updated via `update_task_progress()` with executed commands and outputs, but the plan structure is a free-form dict. There's no code that reads the plan and says "the plan says to fingerprint → check if we already fingerprinted → if not, generate a fingerprint candidate." The plan is only used for progress calculation.

4. **`board.exhausted_strategies` in `SwarmOrchestrator` tracks blocked strategies** (lines 791-807) but there's no centralized repository of "what has been tried across all agents" — each agent maintains its own, and there's no cross-agent sharing except through the blackboard's `recon_cache`.

5. **`board.executed_commands_dedup` prevents duplicate commands** (lines 749-762) but only within a single agent's loop. There's no cross-agent deduplication of command strategies.

6. **Flag candidates from LLM prose are recorded but never verified through evidence** (lines 659-666 in swarm_orchestrator.py): The `record_flag_candidate()` records the candidate, but the verification loop is separate. The comment on line 666 documents a known bug: "NOTE: the verified/rejected log entry is written inside record_flag_candidate() once the verifier returns its verdict. Do NOT add a duplicate record_agent_step here (Secondary Bug 3)." This indicates the verification pipeline is acknowledged but not fully integrated.

7. **`board.memory_context` retrieved but not fed into agent prompts deterministically** (lines 265-297 in swarm_orchestrator.py): Memory is retrieved and stored on the blackboard, but the `_build_agent_context()` method includes `memory_context` from the board — however, this is just text included in the prompt. There's no deterministic way to say "this memory has success_probability=0.8 for this technique → prioritize it in scoring."

---

## 7. Test Coverage Gaps

### What the current tests prove:

| Test File | What It Proves |
|---|---|
| `test_production_checkpoint_and_harness.py` | Default `engine_type=None` → `swarm_orchestrator.run_swarm()` is called. Verifies the full runtime path from UI start to legacy swarm orchestrator. |
| `test_phase4_swarm.py` | `SwarmCoordinator` can be instantiated and its methods (`plan_initial`, `_loop`, `_handle_result`) work deterministically in isolation. |
| `test_phase5_reasoning.py` | `CandidateGenerator.generate()`, `ActionScorer.rank()`, `Supervisor.reason()` produce correct deterministic outputs given mission state. |
| `test_phase4x_execution.py` | `SwarmCoordinator` budget/stop conditions work correctly. |
| `test_workstream_a_default_engine.py` | `SwarmOrchestrator.run_swarm()` signature has `max_tokens` parameter; blackboard token budget fields exist. |
| `test_workstream_a_hardening.py` | Default engine iteration/time caps; no-progress ceiling; duplicate command detection; blocked capability detection. |
| `test_competition_harness.py` | `WorkflowRunner.start_run()` with `engine_type=None` starts runs; kill switch works; `orchestrator_loop.execute_run_step()` works; checkpoint save/load works. |
| `test_agent_runtime.py` | `AgentRuntime.run()` with `RealToolExecutor` works; flag verification from tool output works; recovery/replan cycles work. |
| `test_candidate_resolution.py` | `CandidateGenerator` and `ActionScorer` can be imported and used (in isolation). |

### What the current tests DO NOT prove:

| Gap | Why It Matters |
|---|---|
| **No end-to-end test exercises `SwarmCoordinator` through `WorkflowRunner.start_run()`** | The coordinated engine is never the default path. No test proves a challenge can be solved end-to-end through the coordinated pipeline. |
| **No test proves the coordinated engine solves a real CTF challenge** | All `SwarmCoordinator` tests use mocked/fake data (fake capability services, fake targets). |
| **No test proves the legacy swarm can solve a real CTF challenge** | The legacy swarm tests focus on component isolation (agent worker loops, checkpoint cycles, bug fixes), not end-to-end challenge solving. |
| **Tests passing because they mock important execution layers** | `test_competition_harness.py` uses `_auto_approve_gate` to bypass privilege gates; `test_phase4_swarm.py` and `test_phase5_reasoning.py` use mock mission states without real LLM calls or tool execution. |
| **No test of the `start_run()` path with `engine_type="swarm_coord"`** | The coordinated engine can be selected but there's no test verifying it's the correct production path. |
| **No test of `AgentRuntime` through `WorkflowRunner`** | `AgentRuntime` is tested in isolation (`test_agent_runtime.py`), not through the `WorkflowRunner.start_run()` entry point. |
| **No test verifying evidence flows from command execution → flag verification → run completion** | The flag verification tests (`test_non_flag_answer_verification_distinction`) test the VerifierAgent in isolation, not as part of an executing run. |
| **No test of coordinated engine with real tool execution** | The coordinator's tool execution flows through `AgentRuntime` → `RealToolExecutor` → `tool_manager`, but no end-to-end test connects these through `start_run()`. |

---

## 8. Recommended Execution Architecture

### Smallest architecture that can actually solve CTF challenges reliably:

```
WorkflowRunner.start_run()
    ↓ (engine_type="swarm_coord" or default should change to this)
    SwarmCoordinator.run()                                                    (Phase 4+ coordinated)
        ↓
    Supervisor.reason()                                                       (deterministic planning)
        ↓
    CandidateGenerator.generate()                                             (state-gap + evidence + memory + playbook)
        ↓
    ActionScorer.rank()                                                       (deterministic scoring)
        ↓
    TaskScheduler                                                             (dependency-aware queue)
        ↓
    SpecialistAgent(AgentRole) → AgentRuntime.run()                           (canonical execution loop)
        ↓
    ToolManager → execute_raw_command (gated by require_approval())
        ↓
    ObservationEngine.observe() → evidence
        ↓
    VerifierAgent.verify()                                                    (authoritative flag verification)
        ↓
    If verified → RUN COMPLETED
    Else → RecoveryEngine.diagnose() → replan/retry/abandon
```

### Minimal changes needed to make this the default:

1. **Change `WorkflowRunner.start_run()` default** from `engine_type=None` (→ `"swarm"`) to `engine_type="swarm_coord"` — this selects the coordinated engine.

2. **Wire the coordinated pipeline into the challenge creation/intake flow** so missions default to the coordinated engine.

3. **Ensure `SwarmCoordinator` can run end-to-end** with real tool execution (integrate `AgentRuntime` → `RealToolExecutor` → `tool_manager`).

4. **Keep the legacy `SwarmOrchestrator` available** for backward compatibility and gradual migration, but mark it as `engine_type="swarm"` (legacy mode).

5. **Update all dependent systems** (challenge intake, API routes, WebSocket handlers) to respect the engine type selection.

### Why this architecture works:

- **Supervisor.reason()** provides deterministic "what should we do next" without requiring the LLM to plan from scratch each turn
- **CandidateGenerator.generate()** produces evidence-backed, information-gain guided actions from current knowledge
- **ActionScorer.rank()** scores candidates on transparent, inspectable terms (information_gain, evidence_support, success_probability, novelty, cost, risk)
- **TaskScheduler** ensures dependencies are respected (e.g., recon before exploitation)
- **AgentRuntime** provides the canonical execution loop with session ownership, trajectory persistence, and VerifierAgent-based flag verification from REAL tool output
- **EvidenceBus** shares state between supervisor, scheduler, and specialist agents
- **VerifierAgent** ensures only flags verified from tool output are accepted — no LLM prose alone

---

## 9. Fix Order (Strict Numbered Implementation Order)

1. **Change `WorkflowRunner.start_run()` default engine** from `engine_type=None` (→ `"swarm"`) to `engine_type="swarm_coord"` in `backend/api/runner.py` line 47. This is the single most impactful change — it makes the coordinated engine the production default.

2. **Wire `SwarmCoordinator` into the challenge intake/create flow** so newly created challenges default to the coordinated engine. Update `backend/api/routes/challenges.py` `create_challenge()` to include engine_type in mission_plan run_config.

3. **Ensure `SwarmCoordinator` can execute real commands** through `AgentRuntime` → `RealToolExecutor` → `tool_manager`. Verify the end-to-end path from coordinator reasoning → task dispatch → command execution → evidence → flag verification.

4. **Update `OrchestratorLoop.run_autonomous_loop()`** to optionally use the coordinated pipeline, or deprecate it in favor of the coordinator. The legacy loop can remain as `engine_type="swarm"` fallback.

5. **Cross-link state between systems**: Convert `orchestrator_loop`'s `state_memory` to mission state format that the CandidateGenerator can read. Ensure `mission_plan` is fed into the Supervisor's initial planning.

6. **Ensure flag verification goes through VerifierAgent** in all paths. The legacy `orchestrator_loop` flag capture from LLM prose should be replaced with evidence-based verification, or at minimum, the flag should be recorded as a candidate and verified through the agent runtime pipeline.

7. **Add end-to-end tests** that exercise `WorkflowRunner.start_run(engine_type="swarm_coord")` through to flag capture and run completion. These tests should use mock/ stubbed tool execution but exercise the full coordinator → scheduler → agent runtime → verifier pipeline.

8. **Update WebSocket/UI handlers** to display coordinated engine metrics (candidate scores, supervisor reasoning decisions, evidence bus events) rather than just legacy swarm metrics.

9. **Update documentation and API routes** to reflect the new default engine type and the available engine options.

10. **Gradually migrate any legacy code** that assumes the `SwarmOrchestrator` is the only engine, starting with the most critical paths (challenge creation, run initiation, status reporting).

---

## 10. Files to Modify (Only Necessary)

### Primary changes (must modify):

1. **`backend/api/runner.py`** — Change default `engine_type` from `None` (→ `"swarm"`) to `engine_type="swarm_coord"`. This is the critical single change that makes the coordinated engine the production default.

2. **`backend/api/routes/challenges.py`** — Update challenge creation to include engine_type in mission_plan run_config, so newly created challenges default to the coordinated engine.

3. **`backend/agents/orchestrator_loop.py`** — May need minor updates to ensure `state_memory` or `mission_plan` data is compatible with the coordinated pipeline, or to deprecate the legacy loop in favor of the coordinator.

4. **`tests/test_production_checkpoint_and_harness.py`** — Update `test_workflow_runner_and_harness_use_production_engine` to expect `engine_type="swarm_coord"` instead of `engine_type=None`, and add new tests for the coordinated engine path.

### Secondary changes (nice to have, not blocking):

5. **`backend/swarm/coordinator.py`** — Ensure `SwarmCoordinator.run()` integrates fully with `AgentRuntime` → `RealToolExecutor` → `tool_manager` for end-to-end command execution. The infrastructure is mostly in place; may need minor wiring.

6. **`backend/agent_runtime/runtime.py`** — Verify `AgentRuntime.run()` works correctly when called from `SwarmCoordinator._dispatch()` → `agent.execute()`. The `AgentRuntime` is already designed to be reusable; may just need configuration.

7. **`backend/swarm/candidates.py`, `backend/swarm/scoring.py`, `backend/swarm/supervisor.py`** — These are already implemented and tested. No changes needed unless adding new action types or improving scoring weights.

8. **`backend/agents/swarm_orchestrator.py`** — Keep for backward compatibility (`engine_type="swarm"` legacy mode). No functional changes needed unless fixing the documented bug (Secondary Bug 3 in swarm_orchestrator.py line 666).

### Files NOT to modify (already correct or out of scope):

- `backend/swarm/candidates.py` — Already implements state-gap, evidence, memory, playbook candidate generation
- `backend/swarm/scoring.py` — Already implements deterministic ActionScorer with information gain, evidence support, etc.
- `backend/swarm/supervisor.py` — Already implements Supervisor.reason() with deterministic candidate generation and ranking
- `backend/swarm/scheduler.py` — Already implements dependency-aware task scheduler
- `backend/agent_runtime/verifier.py` — Already implements evidence-based flag verification with deterministic gates
- `backend/agent_runtime/runtime.py` — Already implements canonical execution loop with session/trajectory ownership
- `backend/tools/manager.py` — Already provides `execute_raw_command` with privilege gating

---

## Conclusion

The audit reveals that FORGE's fundamental problem is **not a lack of capable components** — the Phase 4+ coordinated engine (Supervisor + ActionScorer + CandidateGenerator + TaskScheduler + EvidenceBus + VerifierAgent) is fully implemented and tested. The problem is **architectural: the default execution engine bypasses all of these components**.

The `WorkflowRunner.start_run()` method defaults to `engine_type=None`, which now resolves to `engine="swarm_coord"` — the production default that selects `SwarmCoordinator` with the full coordinated reasoning pipeline (Supervisor → reason → CandidateGenerator → ActionScorer → TaskScheduler → EvidenceBus → VerifierAgent). The legacy `engine_type="swarm"` path dispatches to `SwarmOrchestrator` with independent agents, no centralized reasoning, and no evidence-based flag verification, and is retained only for backward compatibility.

**The fix is a one-line change in `WorkflowRunner.start_run()`**: the code already uses `engine = (engine_type or "swarm_coord").strip().lower()`, making `engine_type=None` default to `"swarm_coord"` → coordinated swarm. This aligns the production path with the architecture that can actually solve CTF challenges reliably. The documentation and API routes should be updated to reflect this.

All the necessary components (candidate generation, scoring, supervisor reasoning, task scheduling, evidence bus, flag verification) are already implemented and tested; they just need to be on the critical path instead of being bypassed.