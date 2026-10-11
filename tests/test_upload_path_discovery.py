"""Focused tests for upload-confirmation path discovery.

A server response of the form ``The file <name> has been uploaded Path: <path>`` tells
the agent where an uploaded file landed on the TARGET. That path is a discovered
endpoint, not a local file and never a flag. These tests exercise the whole deterministic
path (ObservationEngine → MissionState → SpecialistAgent evidence harvest) without any
network, subprocess, or database.
"""

import unittest

from backend.agent_runtime.action import ExecResult
from backend.agent_runtime.observation import ObservationEngine, scoped_endpoint
from backend.agent_runtime.state import MissionState
from backend.swarm.agents import SpecialistAgent
from backend.swarm.evidence import EvidenceType
from backend.swarm.roles import AgentRole
from backend.swarm.tasks import Task

# The exact response shape under test.
UPLOAD_RESPONSE = "The file avatar.png has been uploaded Path: uploads/flag\n"

TARGET = "http://target.ctf:8080"


class _FakeSession:
    """The only attribute ``_harvest_evidence`` needs is ``state``."""

    def __init__(self, state):
        self.state = state


class TestUploadConfirmationObservation(unittest.TestCase):
    def test_extracts_path_from_exact_response_into_new_files(self):
        obs = ObservationEngine().observe(ExecResult(stdout=UPLOAD_RESPONSE))
        self.assertIn("uploads/flag", obs.new_files)
        # …and the dedicated marker that ids it as a server-side upload path.
        self.assertEqual(obs.upload_paths, ["uploads/flag"])

    def test_ordinary_saved_file_is_not_an_upload_path(self):
        obs = ObservationEngine().observe(ExecResult(stdout="saved to /tmp/out.bin\n"))
        self.assertIn("/tmp/out.bin", obs.new_files)
        self.assertNotIn("/tmp/out.bin", obs.upload_paths)
        self.assertNotIn("/tmp/out.bin", obs.new_endpoints)

    def test_path_is_not_a_flag_candidate(self):
        obs = ObservationEngine().observe(ExecResult(stdout=UPLOAD_RESPONSE))
        self.assertEqual(obs.flag_candidates, [])
        self.assertNotIn("uploads/flag", obs.flag_candidates)


class TestScopedEndpoint(unittest.TestCase):
    def test_relative_path_is_anchored_to_challenge_origin(self):
        self.assertEqual(
            scoped_endpoint("uploads/flag", TARGET),
            "http://target.ctf:8080/uploads/flag",
        )

    def test_absolute_path_on_origin_is_kept(self):
        self.assertEqual(
            scoped_endpoint("/uploads/flag", TARGET),
            "http://target.ctf:8080/uploads/flag",
        )

    def test_off_origin_or_scheme_paths_are_refused(self):
        for bad in (
            "http://evil.example/uploads/flag",
            "https://evil.example/uploads/flag",
            "//evil.example/uploads/flag",
            "javascript:alert(1)",
            "data:text/html,x",
        ):
            with self.subTest(path=bad):
                self.assertEqual(scoped_endpoint(bad, TARGET), "")

    def test_absolute_url_without_configured_origin_is_refused(self):
        # With no origin we cannot verify scope, so an absolute URL is refused…
        self.assertEqual(scoped_endpoint("http://evil.example/x", ""), "")
        # …while a relative path (which cannot escape on its own) is allowed.
        self.assertEqual(scoped_endpoint("uploads/flag", ""), "uploads/flag")


class TestUploadConfirmationEvidence(unittest.TestCase):
    def _harvest(self, stdout, target=TARGET):
        state = MissionState(target=target)
        obs = ObservationEngine().observe(ExecResult(stdout=stdout, exit_code=0), state)
        state.apply_observation(obs)
        task = Task(mission_id="m1", role="web", objective="test upload path",
                    run_id="r1", challenge_id="c1")
        agent = SpecialistAgent(AgentRole.WEB)
        return state, agent._harvest_evidence(_FakeSession(state), task, {"endpoints": set()})

    def test_upload_path_is_published_as_scoped_endpoint(self):
        state, evidence = self._harvest(UPLOAD_RESPONSE)
        endpoints = [e for e in evidence if e.evidence_type == EvidenceType.ENDPOINT.value]
        self.assertTrue(any(e.title == "http://target.ctf:8080/uploads/flag" for e in endpoints),
                        f"expected a scoped endpoint, got: {[e.title for e in endpoints]}")
        # It was recognised via the upload-confirmation marker, and the raw path stayed
        # out of the generic endpoint bucket (so it is not published twice / as a plain URL).
        self.assertEqual(state.known_endpoints, [])
        self.assertIn("uploads/flag", state.upload_paths)

    def test_local_saved_file_is_not_published_as_endpoint(self):
        _, evidence = self._harvest("saved to /tmp/out.bin\n")
        self.assertFalse(any(e.evidence_type == EvidenceType.ENDPOINT.value for e in evidence))

    def test_upload_path_is_not_published_as_flag(self):
        _, evidence = self._harvest(UPLOAD_RESPONSE)
        self.assertFalse(any(e.evidence_type == EvidenceType.FLAG.value for e in evidence))


if __name__ == "__main__":
    unittest.main(verbosity=2)
