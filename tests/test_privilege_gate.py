"""Operator-approval gate coverage for EVERY agent command path.

The per-command privilege gate used to exist only inside
``SwarmOrchestrator._agent_worker``. Every other agent-reachable execution path
(the legacy ReAct loop in ``agents/orchestrator_loop.py``, the
``agent_runtime.RealToolExecutor``, and ``POST /tools/execute``) could run a command
— including ``rm -rf`` — with no approval, no pending-approval entry, and no operator
prompt. These tests pin the shared gate (``backend/privilege/gate.py``) and each of
those newly-gated call sites.

The security property under test is FAIL-CLOSED: only an explicit operator "approve"
allows execution. Timeout, no decision, a broken WebSocket, or any exception during
the wait must resolve to DENIED — never to a default-allow.

Isolation (project rule #5): ``backend/config.py`` calls ``load_dotenv(override=True)``
at import time, which clobbers an ``os.environ`` assignment made before it with the
``.env`` production url. DATABASE_URL is therefore RE-ASSERTED after that import so
this module really runs against ``test_forge.db``.
"""

import asyncio
import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

import backend.config  # noqa: E402  — runs load_dotenv(override=True)

# Re-assert AFTER the dotenv load so the production url in .env cannot win.
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.config import settings  # noqa: E402
from backend.database.models import (  # noqa: E402
    AgentStateModel, AuditLogModel, ChallengeModel, CheckpointModel, ReportModel,
    RunModel, ToolExecutionModel,
)
from backend.database.session import SessionLocal, get_engine, init_db  # noqa: E402
from backend.privilege.classify import classify_command_privilege  # noqa: E402
from backend.privilege.gate import SHARED_PENDING_APPROVALS, require_approval  # noqa: E402

DANGEROUS_CMD = "rm -rf /tmp/x"
FAST_TIMEOUT = 0.2


def _make_llm_response(content: str):
    resp = MagicMock()
    resp.is_refusal = False
    resp.refusal_reason = ""
    resp.content = content
    resp.model = "test-model"
    return resp


class _GateTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_db()
        self.assertTrue(
            str(get_engine().url).endswith("test_forge.db"),
            "gate tests must run against the isolated test database",
        )
        SHARED_PENDING_APPROVALS.clear()

    def tearDown(self):
        SHARED_PENDING_APPROVALS.clear()


# =========================================================================== #
# 1. The shared gate itself — fail-closed semantics
# =========================================================================== #

class TestRequireApprovalFailClosed(_GateTestBase):

    async def test_dangerous_command_times_out_to_deny(self):
        """No operator response within the window => DENIED (rule: DANGEROUS windows
        are never extended, defaulted away, or bypassed)."""
        self.assertEqual(classify_command_privilege(DANGEROUS_CMD, "rm"), "DANGEROUS")

        approved, decision, sudo_pw = await require_approval(
            cmd=DANGEROUS_CMD,
            agent_id="test_agent",
            pending_approvals=SHARED_PENDING_APPROVALS,
            broadcast_fn=AsyncMock(),
            challenge_id="ch_gate",
            run_id="run_gate",
            timeout_seconds=FAST_TIMEOUT,
        )

        self.assertFalse(approved, "a DANGEROUS command with no response must NEVER be approved")
        self.assertIsNone(decision)
        self.assertIsNone(sudo_pw)

    async def test_dangerous_broadcast_failure_denies_without_raising(self):
        """A broken WebSocket must DENY — not propagate a crash that some outer
        `except: pass` could swallow into a default-allow path."""
        async def _broken_broadcast(_payload):
            raise RuntimeError("websocket disconnected")

        approved, decision, sudo_pw = await require_approval(
            cmd=DANGEROUS_CMD,
            agent_id="test_agent",
            pending_approvals=SHARED_PENDING_APPROVALS,
            broadcast_fn=_broken_broadcast,
            challenge_id="ch_gate",
            run_id="run_gate",
            timeout_seconds=FAST_TIMEOUT,
        )

        self.assertFalse(approved, "a broadcast failure must resolve to DENY, never allow")
        self.assertIsNone(decision)
        self.assertIsNone(sudo_pw)

    async def test_privileged_command_times_out_to_deny(self):
        cmd = "some-unregistered-binary --flag"
        self.assertEqual(classify_command_privilege(cmd, "some-unregistered-binary"), "PRIVILEGED")

        approved, decision, _ = await require_approval(
            cmd=cmd,
            agent_id="test_agent",
            pending_approvals=SHARED_PENDING_APPROVALS,
            broadcast_fn=AsyncMock(),
            challenge_id="ch_gate",
            timeout_seconds=FAST_TIMEOUT,
        )
        self.assertFalse(approved)
        self.assertIsNone(decision)

    async def test_safe_command_auto_approves_without_an_operator(self):
        """SAFE tools (registry-marked) need no round-trip and register no request."""
        broadcast = AsyncMock()
        approved, decision, sudo_pw = await require_approval(
            cmd="nmap -sV 127.0.0.1",
            agent_id="test_agent",
            pending_approvals=SHARED_PENDING_APPROVALS,
            broadcast_fn=broadcast,
            challenge_id="ch_gate",
            timeout_seconds=FAST_TIMEOUT,
        )

        self.assertTrue(approved)
        self.assertIsNone(decision)
        self.assertIsNone(sudo_pw)
        broadcast.assert_not_awaited()
        self.assertEqual(SHARED_PENDING_APPROVALS, {})

    async def test_pending_entry_registered_then_cleaned_up(self):
        """The request must be visible to the operator UI while pending and removed
        (with its single-use password wiped) once resolved."""
        seen = {}

        async def _capture(payload):
            seen["request_id"] = payload["request_id"]
            seen["event"] = payload["event"]
            # Respond as the operator would, through the same dict the gate polls.
            SHARED_PENDING_APPROVALS[payload["request_id"]]["decision"] = "approve"
            SHARED_PENDING_APPROVALS[payload["request_id"]]["event"].set()

        approved, decision, _ = await require_approval(
            cmd="sqlmap -u http://127.0.0.1:8000 --dump",
            agent_id="test_agent",
            pending_approvals=SHARED_PENDING_APPROVALS,
            broadcast_fn=_capture,
            challenge_id="ch_gate",
            run_id="run_gate",
            timeout_seconds=5,
        )

        self.assertEqual(seen["event"], "APPROVAL_REQUIRED")
        self.assertTrue(approved, "an explicit operator approve must allow execution")
        self.assertEqual(decision, "approve")
        self.assertEqual(SHARED_PENDING_APPROVALS, {}, "the pending entry must be cleaned up")

    async def test_sudo_password_returned_on_approve_and_wiped(self):
        async def _approve_with_password(payload):
            entry = SHARED_PENDING_APPROVALS[payload["request_id"]]
            self.assertTrue(entry["requires_sudo"])
            entry["decision"] = "approve"
            entry["sudo_password"] = "s3cr3t"
            entry["event"].set()

        approved, _, sudo_pw = await require_approval(
            cmd="sudo cat /etc/shadow",
            agent_id="test_agent",
            pending_approvals=SHARED_PENDING_APPROVALS,
            broadcast_fn=_approve_with_password,
            challenge_id="ch_gate",
            timeout_seconds=5,
        )
        self.assertTrue(approved)
        self.assertEqual(sudo_pw, "s3cr3t")
        self.assertEqual(SHARED_PENDING_APPROVALS, {})

    async def test_deny_discards_sudo_password(self):
        async def _deny_with_password(payload):
            entry = SHARED_PENDING_APPROVALS[payload["request_id"]]
            entry["decision"] = "deny"
            entry["sudo_password"] = "s3cr3t"
            entry["event"].set()

        approved, decision, sudo_pw = await require_approval(
            cmd="sudo cat /etc/shadow",
            agent_id="test_agent",
            pending_approvals=SHARED_PENDING_APPROVALS,
            broadcast_fn=_deny_with_password,
            challenge_id="ch_gate",
            timeout_seconds=5,
        )
        self.assertFalse(approved)
        self.assertEqual(decision, "deny")
        self.assertIsNone(sudo_pw, "a denied command's password must never be handed back")

    async def test_audit_row_reconciled_on_approve(self):
        """The classification-time audit row must end up reflecting the REAL decision."""
        agent_id = f"gate_audit_{uuid.uuid4().hex[:8]}"
        cmd = "sqlmap -u http://127.0.0.1:8000 --dump"

        async def _approve(payload):
            entry = SHARED_PENDING_APPROVALS[payload["request_id"]]
            entry["decision"] = "approve"
            entry["event"].set()

        approved, _, _ = await require_approval(
            cmd=cmd,
            agent_id=agent_id,
            pending_approvals=SHARED_PENDING_APPROVALS,
            broadcast_fn=_approve,
            challenge_id="ch_gate",
            timeout_seconds=5,
        )
        self.assertTrue(approved)

        db = SessionLocal()
        try:
            rows = db.query(AuditLogModel).filter(AuditLogModel.agent == agent_id).all()
            self.assertTrue(rows, "the gate must write an audit row")
            self.assertTrue(all(bool(r.approved) for r in rows),
                            "audit row must be reconciled to the operator's real approval")
        finally:
            db.close()

    async def test_audit_row_stays_false_on_timeout(self):
        agent_id = f"gate_audit_to_{uuid.uuid4().hex[:8]}"
        approved, _, _ = await require_approval(
            cmd=DANGEROUS_CMD,
            agent_id=agent_id,
            pending_approvals=SHARED_PENDING_APPROVALS,
            broadcast_fn=AsyncMock(),
            challenge_id="ch_gate",
            timeout_seconds=FAST_TIMEOUT,
        )
        self.assertFalse(approved)

        db = SessionLocal()
        try:
            rows = db.query(AuditLogModel).filter(AuditLogModel.agent == agent_id).all()
            self.assertTrue(rows)
            self.assertTrue(all(not bool(r.approved) for r in rows),
                            "a timed-out command must stay approved=False in the audit trail")
        finally:
            db.close()


# =========================================================================== #
# 2. Legacy ReAct loop (agents/orchestrator_loop.py) — all four call sites
# =========================================================================== #

class TestLegacyLoopIsGated(_GateTestBase):

    async def test_denied_dangerous_command_never_executes(self):
        """The legacy loop's main execution call must not run a denied DANGEROUS
        command. Drives the REAL loop with the REAL classifier and gate; the operator
        simply never answers, so the approval window closes and execution is skipped.
        """
        from backend.agents.orchestrator_loop import orchestrator_loop
        from backend.agents import orchestrator_loop as loop_module

        db = SessionLocal()
        try:
            ch = ChallengeModel(
                name="Privilege Gate Legacy Loop",
                category="web",
                difficulty="EASY",
                description="Gate coverage fixture (test DB only).",
                working_directory=".",
                platform_name="TEST",
            )
            db.add(ch)
            db.commit()
            run = RunModel(challenge_id=ch.id, status="RUNNING",
                           current_phase="recon", current_agent="orchestrator")
            db.add(run)
            db.commit()
            challenge_id, run_id = ch.id, run.id
        finally:
            db.close()

        # Turn 1 runs the DANGEROUS command and is denied; turn 2 sees the kill switch
        # so the loop terminates instead of pivoting to a different (approved) command.
        cancelled = MagicMock(side_effect=[False, True, True, True, True, True, True, True])

        try:
            with patch.object(loop_module, "model_router") as mock_router, \
                 patch.object(loop_module, "tool_manager") as mock_tm, \
                 patch.object(loop_module.workflow_runner, "is_cancelled", cancelled), \
                 patch.object(settings, "CHECKPOINT_TIMEOUT_SECONDS", FAST_TIMEOUT):
                mock_router.route_request = AsyncMock(
                    return_value=_make_llm_response(DANGEROUS_CMD)
                )
                mock_tm.execute_raw_command = AsyncMock()

                await asyncio.wait_for(
                    orchestrator_loop.run_autonomous_loop(run_id, challenge_id, "http://127.0.0.1:8000"),
                    timeout=60,
                )

                mock_router.route_request.assert_awaited()
                mock_tm.execute_raw_command.assert_not_awaited()
        finally:
            db = SessionLocal()
            try:
                # Child rows hold FKs onto runs.id / challenges.id (foreign_keys=ON), so
                # clear them before the parents. The challenge itself is removed through
                # the ORM so its cascade (targets/runs/evidence/findings) also fires.
                for model in (AgentStateModel, CheckpointModel, ToolExecutionModel):
                    db.query(model).filter(model.run_id == run_id).delete()
                db.query(ReportModel).filter(ReportModel.challenge_id == challenge_id).delete()
                db.flush()
                ch_obj = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
                if ch_obj is not None:
                    db.delete(ch_obj)
                db.commit()
            finally:
                db.close()


# =========================================================================== #
# 3. agent_runtime.RealToolExecutor — COMMAND / PYTHON_SCRIPT / shell TOOL_CALL
# =========================================================================== #

class TestRealToolExecutorIsGated(_GateTestBase):

    def _denying_executor(self):
        """A RealToolExecutor using its PRODUCTION default gate (no stub injected)."""
        from backend.agent_runtime.runtime import RealToolExecutor
        executor = RealToolExecutor(tool_manager=MagicMock())
        return executor

    async def test_denied_command_never_executes(self):
        from backend.agent_runtime.action import Action, ActionType

        executor = self._denying_executor()
        action = Action(type=ActionType.COMMAND, command=DANGEROUS_CMD)

        with patch.object(settings, "CHECKPOINT_TIMEOUT_SECONDS", FAST_TIMEOUT):
            res = await asyncio.wait_for(executor.execute(action), timeout=30)

        executor.tool_manager.execute_raw_command.assert_not_called()
        self.assertEqual(res.status, "FAILED")
        self.assertTrue(res.execution_failure)
        self.assertEqual(res.failure_category, "PRIVILEGE_DENIED")
        self.assertIn("PRIVILEGE DENIED", res.stderr)

    async def test_denied_python_script_never_executes(self):
        from backend.agent_runtime.action import Action, ActionType

        executor = self._denying_executor()
        action = Action(type=ActionType.PYTHON_SCRIPT,
                        script="print('this must never run')\n")

        # Throwaway workspace: RealToolExecutor materialises the script on disk before
        # the gate runs, so a cwd of "." would overwrite the repo-root solve.py.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(settings, "CHECKPOINT_TIMEOUT_SECONDS", FAST_TIMEOUT):
                res = await asyncio.wait_for(executor.execute(action, cwd=tmp), timeout=30)

        executor.tool_manager.execute_raw_command.assert_not_called()
        self.assertEqual(res.status, "FAILED")
        self.assertEqual(res.failure_category, "PRIVILEGE_DENIED")

    async def test_denied_shell_capability_never_executes(self):
        """`interactive_open` runs its target as a command line, so a DANGEROUS target
        must be gated even though the capability name itself is registry-marked SAFE."""
        from backend.agent_runtime.action import Action, ActionType

        executor = self._denying_executor()
        action = Action(type=ActionType.TOOL_CALL, capability="interactive_open",
                        tool_args={"target": DANGEROUS_CMD})

        with patch.object(settings, "CHECKPOINT_TIMEOUT_SECONDS", FAST_TIMEOUT):
            res = await asyncio.wait_for(executor.execute(action), timeout=30)

        executor.tool_manager.execute_capability.assert_not_called()
        self.assertEqual(res.status, "FAILED")
        self.assertEqual(res.failure_category, "PRIVILEGE_DENIED")

    async def test_approved_command_still_executes(self):
        """Regression guard: the gate must not block an approved command."""
        from backend.agent_runtime.action import Action, ActionType

        async def _approve(cmd, **kwargs):
            return True, "approve", None

        from backend.agent_runtime.runtime import RealToolExecutor
        tm = MagicMock()
        tm.execute_raw_command = AsyncMock(return_value=MagicMock(
            tool_name="raw_cmd", capability="custom_command", command="python solve.py",
            status="SUCCESS", stdout="ok", stderr="", exit_code=0, duration_ms=1.0,
            execution_failure=False, failure_category=None))
        executor = RealToolExecutor(tool_manager=tm, approval_gate=_approve)

        res = await executor.execute(Action(type=ActionType.COMMAND, command="python solve.py"))
        tm.execute_raw_command.assert_awaited()
        self.assertEqual(res.status, "SUCCESS")


# =========================================================================== #
# 4. POST /tools/execute — an HTTP execution entry point reachable by an agent
# =========================================================================== #

class TestToolsExecuteRouteIsGated(_GateTestBase):

    async def test_denied_shell_capability_is_403_and_never_executes(self):
        """`execute_capability('interactive_open', target=<cmd>)` reaches the shell, so
        the route must gate it. A denied request must not touch the tool manager."""
        from fastapi import HTTPException
        from backend.api.routes import ExecuteToolRequest, execute_tool

        req = ExecuteToolRequest(capability="interactive_open", target=DANGEROUS_CMD)

        with patch("backend.api.routes.tool_manager") as mock_tm, \
             patch.object(settings, "CHECKPOINT_TIMEOUT_SECONDS", FAST_TIMEOUT):
            mock_tm.execute_capability = AsyncMock()
            with self.assertRaises(HTTPException) as ctx:
                await execute_tool(req)
            mock_tm.execute_capability.assert_not_called()

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("PRIVILEGE", ctx.exception.detail)

    async def test_approved_capability_still_runs(self):
        """Regression guard: a SAFE/approved capability is not blocked by the route gate."""
        from backend.api.routes import ExecuteToolRequest, execute_tool

        req = ExecuteToolRequest(capability="nmap", target="127.0.0.1")

        with patch("backend.api.routes.tool_manager") as mock_tm:
            mock_tm.execute_capability = AsyncMock(return_value={"status": "SUCCESS"})
            result = await execute_tool(req)

        mock_tm.execute_capability.assert_awaited_once()
        self.assertEqual(result, {"status": "SUCCESS"})


if __name__ == "__main__":
    unittest.main()
