"""Focused tests for deterministic retrieval of discovered upload-confirmation paths.

When a successful upload response reports where a file landed on the TARGET
(``... has been uploaded Path: uploads/flag``), Forge must follow that path up with
ONE bounded, scoped GET — WITHOUT the model ever choosing that GET — and push the
response through the existing ObservationEngine + FlagVerifier pipeline.

Everything here uses a fake fetcher (and a scripted provider/tool executor for the
one end-to-end case), so NO network, subprocess, or real provider is required.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_forge.db")

from backend.database.session import init_db, SessionLocal
from backend.database.models import (
    AgentSessionModel, TrajectoryEventModel, SwarmMissionModel, SwarmTaskModel,
    SwarmEvidenceModel,
)

from backend.agent_runtime import AgentRuntime, ExecResult, session_manager
from backend.agent_runtime.decision import ProviderCompletion
from backend.swarm.agents import SpecialistAgent
from backend.swarm.coordinator import SwarmCoordinator
from backend.swarm.evidence import EvidenceBus, EvidenceType
from backend.swarm.limits import SwarmLimits
from backend.swarm.mission import SharedMissionState
from backend.swarm.roles import AgentRole
from backend.swarm.tasks import Task, TaskStatus

UPLOAD_RESPONSE = "The file avatar.png has been uploaded Path: uploads/flag\n"
TARGET = "http://target.ctf:8080"
VALID_FLAG = "picoCTF{upload_path_pulled}"


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #

class RecordingFetcher:
    """Fake fetcher: records each call and returns a scripted ExecResult.

    Signature matches the production seam: ``fetcher(url, timeout_seconds)``.
    """

    def __init__(self, result=None, by_url=None, error=None):
        self.calls = []
        self.result = result or ExecResult(status="SUCCESS", stdout="", exit_code=0)
        self.by_url = by_url or {}
        self.error = error

    async def __call__(self, url, timeout_seconds):
        self.calls.append((url, timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.by_url.get(url, self.result)


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    async def complete(self, *, prompt, system_instruction="", capability="general_reasoning",
                       urgency="normal", reasoning_depth="fast"):
        idx = self.calls
        self.calls += 1
        content = self.responses[idx] if idx < len(self.responses) else (
            self.responses[-1] if self.responses else None)
        if content is None:
            return ProviderCompletion(is_refusal=True, refusal_reason="exhausted (simulated).")
        return ProviderCompletion(content=content, provider_name="stub", model_name="stub-model",
                                  prompt_tokens=2, completion_tokens=2)


class ScriptedToolExecutor:
    def __init__(self, by_substring=None, default=None):
        self.by_substring = by_substring or {}
        self.default = default or ExecResult(status="SUCCESS", stdout="", exit_code=0)
        self.executed = []

    async def execute(self, action, *, cwd=None, timeout_seconds=120, canonical_target=None):
        cmd = action.display()
        self.executed.append(cmd)
        for key, res in self.by_substring.items():
            if key in cmd:
                return res
        return self.default


class RetrievalTestBase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        db = SessionLocal()
        try:
            for model in (TrajectoryEventModel, AgentSessionModel, SwarmEvidenceModel,
                          SwarmTaskModel, SwarmMissionModel):
                db.query(model).delete()
            db.commit()
        finally:
            db.close()

    def _session(self, *, target=TARGET, upload_paths=(), known_files=()):
        sess = session_manager.create(
            challenge_id="chal-upload", run_id=None, target_scope=target,
            objective="upload path retrieval test", agent_id="web", engine="swarm_coord",
            challenge_name="Upload Retrieval Test", category="web")
        sess.state.upload_paths = list(upload_paths)
        sess.state.known_files = list(known_files)
        session_manager.save(sess)
        return sess


# --------------------------------------------------------------------------- #
# Safety: refused targets never reach the fetcher
# --------------------------------------------------------------------------- #

class TestRetrievalRefusal(RetrievalTestBase):

    async def test_off_origin_absolute_url_is_refused(self):
        fetcher = RecordingFetcher()
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        sess = self._session(upload_paths=["http://evil.example/uploads/flag"])
        self.assertIsNone(await agent._retrieve_upload_paths(sess))
        self.assertEqual(fetcher.calls, [])

    async def test_protocol_relative_url_is_refused(self):
        fetcher = RecordingFetcher()
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        sess = self._session(upload_paths=["//evil.example/uploads/flag"])
        self.assertIsNone(await agent._retrieve_upload_paths(sess))
        self.assertEqual(fetcher.calls, [])

    async def test_invalid_scheme_is_refused(self):
        fetcher = RecordingFetcher()
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        for bad in ("javascript:alert(1)", "data:text/html,x"):
            with self.subTest(path=bad):
                sess = self._session(upload_paths=[bad])
                self.assertIsNone(await agent._retrieve_upload_paths(sess))
        self.assertEqual(fetcher.calls, [])

    async def test_missing_origin_is_refused(self):
        fetcher = RecordingFetcher()
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        # No configured challenge origin: even a bare relative path is refused.
        sess = self._session(target="", upload_paths=["uploads/flag"])
        self.assertIsNone(await agent._retrieve_upload_paths(sess))
        self.assertEqual(fetcher.calls, [])

    async def test_ordinary_local_saved_file_is_not_fetched(self):
        fetcher = RecordingFetcher()
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        # A local file is in known_files, never upload_paths → never a request target.
        sess = self._session(upload_paths=[], known_files=["/tmp/out.bin"])
        self.assertIsNone(await agent._retrieve_upload_paths(sess))
        self.assertEqual(fetcher.calls, [])


# --------------------------------------------------------------------------- #
# Retrieval mechanics + verification pipeline
# --------------------------------------------------------------------------- #

class TestRetrievalPipeline(RetrievalTestBase):

    async def test_upload_path_is_get_once_even_with_equivalent_paths(self):
        fetcher = RecordingFetcher(result=ExecResult(status="SUCCESS", stdout="ok", exit_code=0))
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        # Two distinct path strings that resolve to the SAME scoped URL.
        sess = self._session(upload_paths=["uploads/flag", "/uploads/flag"])
        await agent._retrieve_upload_paths(sess)
        self.assertEqual(fetcher.calls, [(f"{TARGET}/uploads/flag", 15)])

    async def test_valid_flag_in_response_reaches_verified_state(self):
        fetcher = RecordingFetcher(result=ExecResult(
            status="SUCCESS", exit_code=0, stdout=f"<html>{VALID_FLAG}</html>"))
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        sess = self._session(upload_paths=["uploads/flag"])
        verified = await agent._retrieve_upload_paths(sess)
        self.assertEqual(verified, VALID_FLAG)
        self.assertEqual(sess.state.verified_flag, VALID_FLAG)
        self.assertEqual(sess.verified_flag, VALID_FLAG)

    async def test_response_body_flows_through_real_observation(self):
        body = f"<html><a href='http://target.ctf:8080/admin'>admin</a> {VALID_FLAG}</html>"
        fetcher = RecordingFetcher(result=ExecResult(status="SUCCESS", exit_code=0, stdout=body))
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        sess = self._session(upload_paths=["uploads/flag"])
        await agent._retrieve_upload_paths(sess)
        # The ObservationEngine parsed the endpoint AND the verifier caught the flag.
        self.assertIn(f"{TARGET}/admin", sess.state.known_endpoints)
        self.assertEqual(sess.state.verified_flag, VALID_FLAG)

    async def test_existing_verification_is_not_overwritten(self):
        fetcher = RecordingFetcher(result=ExecResult(status="SUCCESS", exit_code=0,
                                                     stdout="picoCTF{a_new_one}"))
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        sess = self._session(upload_paths=["uploads/flag"])
        sess.state.set_verified_flag("picoCTF{already_verified}")
        session_manager.save(sess)
        self.assertIsNone(await agent._retrieve_upload_paths(sess))
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(sess.state.verified_flag, "picoCTF{already_verified}")

    async def test_response_without_flag_is_not_verified(self):
        fetcher = RecordingFetcher(result=ExecResult(
            status="SUCCESS", exit_code=0, stdout="<html>nothing to see</html>"))
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        sess = self._session(upload_paths=["uploads/flag"])
        self.assertIsNone(await agent._retrieve_upload_paths(sess))
        self.assertIsNone(sess.state.verified_flag)

    async def test_fetch_failure_does_not_crash(self):
        fetcher = RecordingFetcher(error=ConnectionError("boom"))
        agent = SpecialistAgent(AgentRole.WEB, fetcher=fetcher)
        sess = self._session(upload_paths=["uploads/flag"])
        self.assertIsNone(await agent._retrieve_upload_paths(sess))
        self.assertEqual(len(fetcher.calls), 1)

    async def test_production_get_argv_is_bounded_and_never_follows_redirects(self):
        captured = {}
        from backend.execution.service import execution_service
        from backend.execution.base import ExecutionResult

        async def fake_run_argv(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["kwargs"] = kwargs
            return ExecutionResult(status="SUCCESS", stdout="ok", exit_code=0)

        agent = SpecialistAgent(AgentRole.WEB)
        with patch.object(execution_service, "run_argv", new=fake_run_argv):
            res = await agent._default_http_get(f"{TARGET}/uploads/flag", timeout_seconds=15)
        self.assertIsInstance(res, ExecResult)
        argv = captured["argv"]
        self.assertEqual(argv[0], "curl")
        self.assertNotIn("-L", argv)                      # never follows redirects
        self.assertIn("--max-time", argv)
        self.assertIn("--max-filesize", argv)
        self.assertIn("--proto", argv)
        self.assertEqual(argv[-1], f"{TARGET}/uploads/flag")


# --------------------------------------------------------------------------- #
# End-to-end: the model never chooses GET, Forge still retrieves the path
# --------------------------------------------------------------------------- #

class TestUploadRetrievalEndToEnd(RetrievalTestBase):

    async def test_upload_confirmation_triggers_scoped_get_without_model_get(self):
        mission = SharedMissionState(mission_id="m-upload", target=TARGET, category="web")
        bus = EvidenceBus("m-upload", persist=False)
        executor = ScriptedToolExecutor(by_substring={
            "upload": ExecResult(command="curl -F file=@avatar.png http://target.ctf:8080/upload",
                                 status="SUCCESS", exit_code=0, stdout=UPLOAD_RESPONSE),
        })
        runtime = AgentRuntime(
            tool_executor=executor,
            provider_gateway=ScriptedProvider([
                "curl -F file=@avatar.png http://target.ctf:8080/upload",
                "BUDGET_EXHAUSTED: stop",
            ]),
            learn_on_completion=False)
        body = f"<html><a href='http://target.ctf:8080/admin'>x</a> {VALID_FLAG}</html>"
        fetcher = RecordingFetcher(result=ExecResult(status="SUCCESS", exit_code=0, stdout=body))
        agent = SpecialistAgent(AgentRole.WEB, runtime=runtime, fetcher=fetcher)
        task = Task(mission_id="m-upload", role="web", objective="probe the upload endpoint",
                    run_id="r-upload", challenge_id="chal-upload")

        result = await agent.execute(task, mission, bus, max_turns=4)

        # Forge issued the scoped GET deterministically (the model never did).
        self.assertEqual(fetcher.calls, [(f"{TARGET}/uploads/flag", 15)])
        self.assertTrue(all("uploads/flag" not in c for c in executor.executed),
                        f"model must not have requested the path: {executor.executed}")
        # The fetched body's flag reached the EXISTING verified-flag result path.
        self.assertEqual(result.verified_flag, VALID_FLAG)
        self.assertEqual(result.status, "COMPLETED")
        # The fetched body was observed: the new endpoint became harvestable evidence.
        self.assertTrue(
            any(e.evidence_type == EvidenceType.ENDPOINT.value
                and e.title == f"{TARGET}/admin" for e in result.evidence),
            f"expected the fetched endpoint as evidence, got: "
            f"{[(e.evidence_type, e.title) for e in result.evidence]}")
        # The upload path itself was published as a scoped endpoint, not a flag.
        self.assertTrue(any(e.evidence_type == EvidenceType.ENDPOINT.value
                            and e.title == f"{TARGET}/uploads/flag" for e in result.evidence))
        self.assertFalse(any(e.evidence_type == EvidenceType.FLAG.value
                             and e.title == f"{TARGET}/uploads/flag" for e in result.evidence))


# --------------------------------------------------------------------------- #
# Coordinator end-to-end: a retrieved upload-path flag completes the mission
# --------------------------------------------------------------------------- #

class TestCoordinatorEndToEnd(RetrievalTestBase):

    async def test_retrieved_upload_path_flag_completes_the_mission(self):
        fetcher = RecordingFetcher(result=ExecResult(
            status="SUCCESS", exit_code=0, stdout=f"<html>{VALID_FLAG}</html>"))

        def recon_runtime():
            executor = ScriptedToolExecutor(by_substring={
                "upload": ExecResult(command="curl -F file=@avatar.png http://target.ctf:8080/upload",
                                     status="SUCCESS", exit_code=0, stdout=UPLOAD_RESPONSE),
            })
            return AgentRuntime(
                tool_executor=executor,
                provider_gateway=ScriptedProvider([
                    "curl -F file=@avatar.png http://target.ctf:8080/upload",
                    "BUDGET_EXHAUSTED: stop",
                ]),
                learn_on_completion=False)

        def factory(role):
            if role == AgentRole.RECON:
                return SpecialistAgent(role, runtime=recon_runtime(), fetcher=fetcher)
            return SpecialistAgent(role, runtime=AgentRuntime(
                tool_executor=ScriptedToolExecutor(),
                provider_gateway=ScriptedProvider(["BUDGET_EXHAUSTED: noop"]),
                learn_on_completion=False))

        coord = SwarmCoordinator(
            challenge_id="chal-upload-e2e", run_id="r-upload-e2e", target=TARGET,
            category="web", challenge_name="Upload Retrieval E2E",
            limits=SwarmLimits(max_task_retries=0, max_concurrent_agents=1),
            persist=False, agent_factory=factory)

        res = await coord.run()

        # Forge (not the model) fetched the discovered path, and the coordinator's
        # AUTHORITATIVE flag path accepted it and stopped the mission.
        self.assertEqual(fetcher.calls, [(f"{TARGET}/uploads/flag", 15)])
        self.assertEqual(res.status, "COMPLETED")
        self.assertEqual(res.verified_flag, VALID_FLAG)
        self.assertEqual(coord.mission.verified_flag, VALID_FLAG)
        # Global stop fired and no task was left in-flight (§11).
        self.assertTrue(coord._stopped)
        self.assertTrue(all(t.status != TaskStatus.RUNNING.value for t in coord.scheduler.all()),
                        f"a task was left RUNNING: {[t.status for t in coord.scheduler.all()]}")
        # The verified flag was published on the evidence bus exactly once.
        verified = [e for e in coord.bus.all()
                    if e.evidence_type == EvidenceType.FLAG.value and e.title == VALID_FLAG]
        self.assertEqual(len(verified), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
