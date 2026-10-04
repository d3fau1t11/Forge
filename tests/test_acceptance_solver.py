"""
Authoritative end-to-end acceptance test for FORGE's autonomous solver.

This test drives the REAL production chain from mission creation to a verified
flag. Exactly ONE thing is replaced: the LLM/model reasoning layer (a
deterministic router), because a live model API cannot participate in a hermetic
test. Every other layer is the production implementation:

    Mission / Challenge
      -> SwarmCoordinator                    (real)
        -> Supervisor                        (real, deterministic planning + reaction)
        -> CandidateGenerator / ActionScorer (real)
        -> TaskScheduler                     (real)
        -> SpecialistAgent                   (real)
          -> AgentRuntime                    (real)
            -> RouterProviderGateway         (real) -> deterministic router (reasoning ONLY)
            -> RealToolExecutor              (real)
            -> ToolManager                   (real)
            -> ExecutionService              (real)
            -> LocalBackend                  (real)
            -> ProcessManager                (real) -> REAL OS subprocess
        -> ObservationEngine                 (real)
        -> EvidenceBus                       (real)
        -> FlagVerifier / VerifierAgent      (real)
        -> COMPLETED with a verified flag

The local CTF fixture requires two dependent actions:

    Action 1: fetch /robots.txt     -> observation discovers the /hidden_admin endpoint
    Action 2: fetch /hidden_admin   -> real command output contains the flag

The deterministic router emits the second action ONLY after the first action's
observation has put ``/hidden_admin`` into the model prompt, so a passing run
proves adaptive reasoning rather than a scripted one-shot. The flag literal lives
ONLY in the fixture server; it is never given to the router or injected into any
state, evidence or shared mission state.

Operator approval: FORGE's real ``require_approval`` gate is exercised. The test
configures the operator's supported unattended mode (AUTO_APPROVE_PRIVILEGED) so
the mission can run autonomously; no gate, executor or execution backend is
mocked.
"""

import asyncio
import glob
import json
import os
import sys
import threading
import time
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from types import SimpleNamespace
from unittest.mock import patch

# Pin database URL before any backend imports.
os.environ["DATABASE_URL"] = "sqlite:///./test_acceptance_e2e.db"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.swarm.coordinator import SwarmCoordinator, MissionResult
from backend.swarm.limits import SwarmLimits
from backend.swarm.evidence import EvidenceType
from backend.database.session import init_db, SessionLocal
from backend.database.models import (
    ChallengeModel,
    SwarmMissionModel,
    SwarmEvidenceModel,
    SwarmTaskModel,
    TrajectoryEventModel,
)


# ── Local CTF fixture ────────────────────────────────────────────────────── #

class LocalCTFHandler(BaseHTTPRequestHandler):
    """Local CTF web handler with a flag reachable only through discovery."""

    def log_message(self, fmt, *args):  # noqa: A002 - stdlib signature
        pass  # suppress per-request console noise

    def _send(self, code, body, content_type="text/html"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Record the request so the test can prove REAL subprocess networking.
        try:
            self.server.request_log.append(self.path)  # type: ignore[attr-defined]
        except Exception:
            pass

        if self.path == "/":
            self._send(200, (
                "<!DOCTYPE html><html><head><title>FORGE Web Challenge</title></head>"
                "<body><h1>Authorized CTF Web Challenge Target</h1>"
                "<p>Welcome contestant. Probe endpoints to discover the hidden flag.</p>"
                "<p>Check /robots.txt for restricted areas.</p></body></html>"))
        elif self.path == "/robots.txt":
            self._send(200, "User-agent: *\nDisallow: /hidden_admin\n", "text/plain")
        elif self.path == "/hidden_admin":
            self._send(200, (
                "<!DOCTYPE html><html><body><h2>Admin Panel - Confidential</h2>"
                '<div id="flag">FLAG{forge_web_enum_success_1337}</div></body></html>'))
        else:
            self._send(404, "404 Not Found", "text/plain")


class _LoggedHTTPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_log = []


class LocalCTFServer:
    """Local CTF target server running in a daemon thread on an ephemeral port."""

    def __init__(self, host="127.0.0.1"):
        self.host = host
        self.server = _LoggedHTTPServer((self.host, 0), LocalCTFHandler)
        self.port = self.server.server_address[1]
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        try:
            self.server.shutdown()
        except Exception:
            pass
        try:
            self.server.server_close()
        except Exception:
            pass

    def get_url(self):
        return f"http://{self.host}:{self.port}"

    @property
    def requested_paths(self):
        return list(self.server.request_log)


# ── Deterministic, production-compatible model router ────────────────────── #

class DeterministicRouter:
    """Deterministic stand-in for ``backend.providers.router.ModelRouter``.

    Only MODEL REASONING is replaced. It implements the exact keyword signature
    the production ``RouterProviderGateway.complete`` and ``VerifierAgent`` call,
    and it never contains — or returns — the challenge flag. Its only job is to
    choose, from the evidence present in the prompt, which real command a
    specialist should run next.
    """

    def __init__(self, target_url):
        self.target_url = target_url.rstrip("/")
        # Attributes the production runtime reads off the router for budgeting.
        self.providers = {}
        self.DEFAULT_ROUTING_MAP = {}
        # Observability for assertions: (path chosen, was_endpoint_already_known)
        self.issued_paths = []
        self.issued_scripts = []
        self.verification_calls = 0

    # -- helpers ---------------------------------------------------------- #

    def _script(self, path):
        return (
            "```python\n"
            "import urllib.request\n"
            f"url = {self.target_url!r} + {path!r}\n"
            "req = urllib.request.Request(url, headers={'User-Agent': 'forge-e2e'})\n"
            "with urllib.request.urlopen(req, timeout=10) as resp:\n"
            "    print(resp.read().decode('utf-8', 'replace'))\n"
            "```"
        )

    @staticmethod
    def _response(content):
        return SimpleNamespace(
            content=content, is_refusal=False, refusal_reason=None,
            provider_name="deterministic", model_name="deterministic-router",
            prompt_tokens=0, completion_tokens=0,
        )

    # -- production interface --------------------------------------------- #

    async def route_request(self, *, prompt, capability="general_reasoning",
                            system_instruction=None, target_model=None,
                            speed_tier=None, **kwargs):
        prompt = prompt or ""

        # The verifier (Agent #4) asks for a structured verdict. It can never
        # elevate to VERIFIED by itself; deterministic FORGE code enforces that.
        if capability == "verification":
            self.verification_calls += 1
            if "FLAG{" in prompt or "flag{" in prompt:
                content = json.dumps({
                    "verdict": "RESOLVE",
                    "confidence": 0.95,
                    "answer_type": "flag",
                    "is_distractor": False,
                    "reasoning": "Flag-shaped literal captured verbatim from real tool output.",
                    "evidence_soundness": "tool_output",
                })
            else:
                content = json.dumps({
                    "verdict": "NEEDS_MORE_EVIDENCE",
                    "confidence": 0.4,
                    "answer_type": "string",
                    "is_distractor": False,
                    "reasoning": "No flag-shaped literal is present in the evidence.",
                    "evidence_soundness": "unknown",
                })
            return self._response(content)

        # Reasoning turn for a specialist. The endpoint is only present in the
        # prompt AFTER action 1's observation produced it — that dependency is
        # what makes the second action adaptive.
        endpoint_known = "/hidden_admin" in prompt
        if endpoint_known:
            path = "/hidden_admin"       # Action 2: exploit the discovered endpoint
        else:
            path = "/robots.txt"         # Action 1: discover the surface
        script = self._script(path)
        self.issued_paths.append((path, endpoint_known))
        self.issued_scripts.append(script)
        return self._response(script)


# ── Test ─────────────────────────────────────────────────────────────────── #

class TestForgeE2ESolver(unittest.TestCase):
    """End-to-end acceptance test for FORGE's autonomous solver."""

    CHALLENGE_NAME = "Local Web CTF Challenge"
    CATEGORY = "WEB"
    DESCRIPTION = "A local web CTF application exposing a discoverable flag."
    FLAG = "FLAG{forge_web_enum_success_1337}"
    FLAG_FORMAT = "FLAG{{}}"

    def setUp(self):
        for f in glob.glob("test_acceptance_e2e.db*"):
            try:
                os.remove(f)
            except OSError:
                pass
        init_db()
        self.db = SessionLocal()

        # Start the local CTF fixture on an ephemeral port.
        self.ctf_server = LocalCTFServer()
        self.ctf_server.start()
        self.target = self.ctf_server.get_url()
        self.flag = self.FLAG

        # Real operator approval gate, configured for unattended execution.
        from backend.config import settings
        self._prev_auto = getattr(settings, "AUTO_APPROVE_PRIVILEGED", False)
        settings.AUTO_APPROVE_PRIVILEGED = True

        # Replace ONLY the model reasoning layer. The router is deterministic and
        # does not contain the flag.
        self.router = DeterministicRouter(self.target)
        self.router_patch = patch("backend.providers.router.model_router", new=self.router)
        self.router_patch.start()

    def tearDown(self):
        try:
            self.router_patch.stop()
        except Exception:
            pass
        try:
            from backend.config import settings
            settings.AUTO_APPROVE_PRIVILEGED = self._prev_auto
        except Exception:
            pass
        try:
            self.db.close()
        except Exception:
            pass
        self.ctf_server.stop()
        for f in glob.glob("test_acceptance_e2e.db*"):
            try:
                os.remove(f)
            except OSError:
                pass

    # ------------------------------------------------------------------ #

    def test_full_solver_chain(self):
        """Mission → ... → real tool execution → evidence → replan → flag → COMPLETED."""
        # 1) Create the challenge/mission record (Mission creation).
        challenge_id = f"ch-e2e-{int(time.time() * 1000)}"
        db = SessionLocal()
        try:
            db.add(ChallengeModel(
                id=challenge_id, name=self.CHALLENGE_NAME, category=self.CATEGORY,
                difficulty="EASY", description=self.DESCRIPTION, status="QUEUED",
            ))
            db.commit()
        finally:
            db.close()

        mission_id = f"mission-e2e-{int(time.time())}"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(asyncio.wait_for(
                self._run_coordinator(mission_id=mission_id, challenge_id=challenge_id),
                timeout=180,
            ))
        finally:
            loop.close()

        print(f"[E2E] status={result.status} flag={result.verified_flag} "
              f"reason={result.reason} tasks(total={result.tasks_total}, "
              f"completed={result.tasks_completed}, failed={result.tasks_failed}) "
              f"evidence={result.evidence_count}")

        # 2) The mission MUST genuinely complete with the real flag.
        self.assertEqual(result.status, "COMPLETED", f"expected COMPLETED, got {result.status}: {result.reason}")
        self.assertEqual(result.verified_flag, self.FLAG)

        # 3) REAL subprocess execution — proven by the fixture's request log.
        paths = self.ctf_server.requested_paths
        self.assertIn("/hidden_admin", paths, f"flag endpoint was never fetched: {paths}")
        self.assertLess(paths.index("/robots.txt"), paths.index("/hidden_admin"),
                        f"discovery must precede exploitation: {paths}")

        # 4) Adaptive reasoning — action 2 depended on action 1's observation.
        self.assertGreaterEqual(len(self.router.issued_paths), 2,
                                f"expected >=2 model-driven actions, got {self.router.issued_paths}")
        self.assertEqual(self.router.issued_paths[0], ("/robots.txt", False))
        self.assertEqual(self.router.issued_paths[-1], ("/hidden_admin", True))
        self.assertNotIn(self.FLAG, "".join(self.router.issued_scripts),
                         "flag must never be provided to the model/router")

        # 5) The flag came from real command output recorded in the trajectory.
        self._assert_real_execution(mission_id)

        # 6) Evidence was published, including the discovered endpoint and the flag.
        mission_row = self._assert_persisted_mission(mission_id)
        self._assert_evidence(mission_row.id)

    # ------------------------------------------------------------------ #

    async def _run_coordinator(self, *, mission_id, challenge_id):
        coordinator = SwarmCoordinator(
            run_id=mission_id,
            challenge_id=challenge_id,
            target=self.target,
            category=self.CATEGORY,
            challenge_name=self.CHALLENGE_NAME,
            description=self.DESCRIPTION,
            flag_format=self.FLAG_FORMAT,
            workspace_root=os.getcwd(),
            limits=SwarmLimits(
                max_concurrent_agents=1,
                max_active_tasks=8,
                max_task_retries=0,
                task_timeout_seconds=30,
                # One action per specialist task: the RECON task discovers the
                # endpoint, then the coordinator must replan a follow-up task
                # that exploits it. This exercises the full adaptive loop rather
                # than letting a single agent run both steps.
                max_turns_per_task=1,
                max_total_tasks=8,
            ),
            enable_report=False,
            enable_reasoning=True,
        )
        return await coordinator.run(resume=False)

    def _assert_real_execution(self, mission_id):
        db = SessionLocal()
        try:
            commands = (db.query(TrajectoryEventModel)
                        .filter(TrajectoryEventModel.run_id == mission_id,
                                TrajectoryEventModel.event_type == "COMMAND")
                        .order_by(TrajectoryEventModel.sequence.asc()).all())
        finally:
            db.close()

        self.assertGreaterEqual(len(commands), 2,
                                "expected at least two real tool executions")
        stdouts = [c.stdout or "" for c in commands]
        discovery = [s for s in stdouts if "/hidden_admin" in s and "Disallow" in s]
        capture = [s for s in stdouts if self.FLAG in s]
        self.assertTrue(discovery, f"no real execution observed the endpoint: {stdouts}")
        self.assertTrue(capture, f"no real execution captured the flag: {stdouts}")
        # Real subprocess output, not an injection.
        self.assertTrue(any((c.exit_code == 0) for c in commands))

    def _assert_persisted_mission(self, mission_id):
        db = SessionLocal()
        try:
            row = db.query(SwarmMissionModel).filter(
                SwarmMissionModel.run_id == mission_id).first()
        finally:
            db.close()
        self.assertIsNotNone(row, f"mission row not found for run_id={mission_id}")
        self.assertEqual(row.status, "COMPLETED")
        self.assertEqual(row.verified_flag, self.FLAG)
        return row

    def _assert_evidence(self, mission_uuid):
        db = SessionLocal()
        try:
            rows = (db.query(SwarmEvidenceModel)
                    .filter(SwarmEvidenceModel.mission_id == mission_uuid).all())
        finally:
            db.close()
        self.assertGreaterEqual(len(rows), 2, "expected endpoint + flag evidence")
        types = {r.evidence_type for r in rows}
        self.assertIn(EvidenceType.ENDPOINT.value, types,
                      f"discovery evidence missing: {types}")
        self.assertIn(EvidenceType.FLAG.value, types,
                      f"flag evidence missing: {types}")
        flag_ev = [r for r in rows if r.evidence_type == EvidenceType.FLAG.value]
        self.assertTrue(any(self.FLAG in (r.title or "") or self.FLAG in (r.output or "")
                            for r in flag_ev))


if __name__ == "__main__":
    unittest.main()
