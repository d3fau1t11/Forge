"""
Tests for the Experience/Memory layer:
extraction, deterministic generalization (§4), retrieval + ranking (§6, §7),
agent-context injection (§8), the feedback loop (§12), provenance (§13),
playbook promotion (§10), and the clean empty state (§1/§2).

All state lives in the isolated unit-test database per the project directive.
"""
import os
import sys
import shutil
import tempfile
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import init_db, SessionLocal
from backend.database.models import ExperienceModel, ExperienceAttemptModel, MemoryUsageModel


CAPTURED_FLAG = "picoCTF{ss7i_tem9late_pwn}"
TARGET_URL = "http://blog.challenge.ctf:8080"
LEAKED_SECRET = "sup3rSecretAdminPass"
TARGET_IP = "10.10.14.99"


def _solved_board(**overrides):
    """A lightweight stand-in for a completed SwarmBlackboard (duck-typed)."""
    import types as _t
    board = _t.SimpleNamespace(
        run_id="run-unit-1",
        challenge_id="chal-unit-1",
        challenge_name="Template Terror",
        category="WEB",
        difficulty="MEDIUM",
        description="A blog renders your display name through a server-side template.",
        target_scope=f"{TARGET_URL} + {TARGET_IP}",
        discovered_endpoints={"/", "/profile", "/render"},
        extracted_headers={"Server": "Werkzeug/2.0 Python/3.9", "X-Powered-By": "Flask"},
        observed_cookies={"session": "eyJhbGciOiJub25lIn0.eyJ1c2VyIjoiZ3Vlc3QifQ."},
        deobfuscated_secrets=[{"admin_pw": LEAKED_SECRET}],
        candidate_tokens=set(),
        candidate_usernames={"admin"},
        artifact_classification=None,
        flag_captured=CAPTURED_FLAG,
        execution_history=[
            {"agent": "a1", "command": f"curl -s {TARGET_URL}/", "output": "Server: Werkzeug/2.0", "note": ""},
            {"agent": "a1", "command": f"curl -s '{TARGET_URL}/render?name=.php5'", "output": "400 Bad Request rejected", "note": ""},
            {"agent": "a1", "command": f"curl -s '{TARGET_URL}/render?name={{{{7*7}}}}'", "output": "Hello, 49", "note": "jinja render_template_string reflected"},
            {"agent": "a2", "command": f"curl -s '{TARGET_URL}/render?name={{{{config}}}}'", "output": f"...{CAPTURED_FLAG}...", "note": "flag in output"},
        ],
    )
    for k, v in overrides.items():
        setattr(board, k, v)
    return board


class MemoryTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self._clean_tables()
        self.tmp_dir = tempfile.mkdtemp(prefix="forge_mem_test_")
        from backend.knowledge.playbook_vault import PlaybookVault
        from backend.knowledge.experience_memory import ExperienceMemory
        from backend.knowledge.memory_retriever import MemoryRetriever
        from backend.knowledge.experience_extractor import ExperienceExtractor
        self.vault = PlaybookVault(base_dir=os.path.join(self.tmp_dir, "pb"))
        self.mem = ExperienceMemory(vault=self.vault)
        self.retriever = MemoryRetriever(memory=self.mem, vault=self.vault)
        self.extractor = ExperienceExtractor()

    def tearDown(self):
        try:
            self.mem.db_conn.close()
        except Exception:
            pass
        try:
            self.vault.db_conn.close()
        except Exception:
            pass
        self._clean_tables()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @staticmethod
    def _clean_tables():
        db = SessionLocal()
        try:
            # Children before parents — FK enforcement is ON.
            db.query(MemoryUsageModel).delete()
            db.query(ExperienceAttemptModel).delete()
            db.query(ExperienceModel).delete()
            db.commit()
        finally:
            db.close()


class TestExtractionAndGeneralization(MemoryTestBase):
    def test_generalization_strips_all_challenge_specific_secrets(self):
        """§4: a solved challenge must NEVER be stored with its concrete secrets."""
        import json
        rec = self.extractor.extract_from_board(_solved_board(), flag=CAPTURED_FLAG, outcome="success")
        blob = json.dumps(rec.model_dump())
        self.assertNotIn(CAPTURED_FLAG, blob, "flag leaked into experience")
        self.assertNotIn(TARGET_URL, blob, "target URL leaked into experience")
        self.assertNotIn(LEAKED_SECRET, blob, "secret leaked into experience")
        self.assertNotIn(TARGET_IP, blob, "target IP leaked into experience")
        # The reusable sentinel token must be present instead.
        self.assertIn("{TARGET_URL}", blob)

    def test_extracts_generalized_technique_and_attack_chain(self):
        rec = self.extractor.extract_from_board(_solved_board(), flag=CAPTURED_FLAG, outcome="success")
        self.assertIn("Template Injection", rec.technique)
        self.assertIn("ssti", rec.tags)
        self.assertTrue(rec.successful_attack_chain, "winning chain should not be empty")
        self.assertIn("flask", rec.technologies)

    def test_failed_approaches_are_recorded(self):
        """§5: FORGE must remember what FAILED, not only what worked."""
        rec = self.extractor.extract_from_board(_solved_board(), flag=CAPTURED_FLAG, outcome="success")
        self.assertTrue(rec.failed_techniques, "rejected attempts should be captured")
        joined = " ".join(f["approach"] for f in rec.failed_techniques)
        self.assertIn(".php5", joined)
        self.assertTrue(any(a.outcome == "failure" for a in rec.attempts))

    def test_blue_team_detection_indicators_derived(self):
        """§11: a successful attack should yield reusable defensive knowledge."""
        rec = self.extractor.extract_from_board(_solved_board(), flag=CAPTURED_FLAG, outcome="success")
        di = rec.detection_indicators
        for key in ("indicators", "logs", "investigation", "containment"):
            self.assertIn(key, di)
        self.assertTrue(di["indicators"])

    def test_failure_run_extraction(self):
        board = _solved_board(flag_captured="")
        rec = self.extractor.extract_from_board(board, flag="", outcome="failure")
        self.assertEqual(rec.outcome, "failure")
        self.assertLess(rec.confidence, 0.6)


class TestStoreRetrieveRank(MemoryTestBase):
    def _store(self, board=None, flag=CAPTURED_FLAG, outcome="success"):
        rec = self.extractor.extract_from_board(board or _solved_board(), flag=flag, outcome=outcome)
        return self.mem.store(rec)

    def test_store_and_search(self):
        exp_id = self._store()
        self.assertTrue(exp_id)
        hits = self.mem.search(query="web ssti template", category="web",
                               evidence="Werkzeug Flask jinja", top_k=5)
        self.assertTrue(any(h["id"] == exp_id for h in hits))

    def test_retrieval_returns_upload_memory_for_upload_evidence(self):
        """§21: upload evidence → upload memories returned."""
        upload_board = _solved_board(
            challenge_name="Upload Uploader", category="WEB",
            description="Profile picture upload.",
            discovered_endpoints={"/upload"},
            extracted_headers={"Server": "nginx"},
            execution_history=[
                {"agent": "a1", "command": f"curl -F 'file=@shell.phtml' {TARGET_URL}/upload -H 'Content-Type: multipart/form-data'",
                 "output": "Upload successful uploads/shell.phtml", "note": ""},
            ],
        )
        self._store(board=upload_board)
        self._store()  # an unrelated SSTI experience
        hits = self.mem.search(query="upload bypass", evidence="multipart/form-data upload filename=", top_k=5)
        self.assertTrue(hits)
        self.assertIn("Upload", hits[0]["technique"])

    def test_ranking_prefers_proven_success(self):
        """§7: repeatedly-successful / higher-confidence memory ranks higher."""
        proven = self._store()
        _weak = self._store()
        # Reinforce the "proven" one so it becomes repeated-success + higher confidence.
        self.mem.record_feedback(proven, success=True)
        hits = self.mem.search(query="web ssti template", category="web",
                               evidence="Werkzeug Flask jinja", top_k=5)
        ids = [h["id"] for h in hits]
        self.assertIn(proven, ids)
        self.assertLess(ids.index(proven), ids.index(_weak), "proven memory should outrank the weaker one")

    def test_no_secret_leak_in_prompt_context(self):
        self._store()
        ctx, mems = self.retriever.retrieve_and_format(
            evidence="Werkzeug Flask jinja render", category="web",
            technologies=["flask", "werkzeug"], query="web ssti", top_k=6)
        self.assertIn("RELEVANT FORGE MEMORY", ctx)
        self.assertNotIn(CAPTURED_FLAG, ctx)
        self.assertNotIn(TARGET_URL, ctx)
        self.assertTrue(mems)

    def test_empty_memory_is_clean(self):
        """§1/§2: no seeded/injected data — a fresh store is empty and returns nothing."""
        stats = self.mem.get_stats()
        self.assertEqual(stats["total_memories"], 0)
        self.assertEqual(self.mem.search(query="anything", evidence="anything"), [])


class TestFeedbackProvenancePromotion(MemoryTestBase):
    def _store(self, outcome="success"):
        rec = self.extractor.extract_from_board(_solved_board(), flag=CAPTURED_FLAG, outcome=outcome)
        return self.mem.store(rec)

    def test_feedback_updates_success_statistics(self):
        """§12: a memory that helped gets reinforced; one that didn't is penalised."""
        exp_id = self._store()
        before = self.mem.get(exp_id)
        self.mem.record_feedback(exp_id, success=True, note="worked")
        after = self.mem.get(exp_id)
        self.assertEqual(after["times_used"], 1)
        self.assertEqual(after["times_successful"], before["times_successful"] + 1)
        self.assertGreaterEqual(after["confidence"], before["confidence"])
        # A failure decrements confidence and bumps the failure counter.
        self.mem.record_feedback(exp_id, success=False, note="did not apply")
        after2 = self.mem.get(exp_id)
        self.assertEqual(after2["times_failed"], 1)
        self.assertLess(after2["confidence"], after["confidence"])

    def test_retrieval_records_usage_event(self):
        exp_id = self._store()
        self.mem.record_retrieval([exp_id], run_id="run-x", challenge_id="chal-x")
        detail = self.mem.get(exp_id, with_children=True)
        self.assertEqual(detail["times_retrieved"], 1)
        self.assertTrue(any(u["event"] == "retrieved" for u in detail["usage_log"]))

    def test_provenance_links_back_to_run(self):
        """§13: memory retains a traceable link to its originating run/challenge."""
        exp_id = self._store()
        detail = self.mem.get(exp_id)
        self.assertEqual(detail["source"], "forge_run")
        self.assertEqual(detail["source_run_id"], "run-unit-1")
        self.assertEqual(detail["source_challenge_id"], "chal-unit-1")

    def test_promotion_to_playbook(self):
        """§10: a repeatedly-successful experience is promoted into the Playbook Vault."""
        exp_id = self._store()
        # First feedback pushes times_successful to 2 → eligible for promotion.
        self.mem.record_feedback(exp_id, success=True)
        detail = self.mem.get(exp_id)
        self.assertIsNotNone(detail["promoted_playbook_id"])
        promoted = self.vault.load_playbook(detail["promoted_playbook_id"])
        self.assertIsNotNone(promoted, "promoted playbook should exist in the vault")
        # The promoted playbook must also be generalized (no leaked flag/target).
        import json
        self.assertNotIn(CAPTURED_FLAG, json.dumps(promoted.model_dump()))


class TestAgentContextInjection(MemoryTestBase):
    def test_memory_appears_in_agent_prompt(self):
        """§8: retrieved memory is injected into the agent prompt as reference."""
        from backend.agents.agent_prompt import make_context_from_env, build_agent_prompt
        rec = self.extractor.extract_from_board(_solved_board(), flag=CAPTURED_FLAG, outcome="success")
        self.mem.store(rec)
        memory_context, mems = self.retriever.retrieve_and_format(
            evidence="Werkzeug Flask jinja", category="web", technologies=["flask"], query="web ssti", top_k=6)
        self.assertTrue(mems)
        ctx = make_context_from_env(
            env_info={}, challenge_name="X", platform="", category="WEB", difficulty="EASY",
            description="d", target_url="http://t", working_directory=".", max_iterations=10,
            max_minutes=10, flag_pattern="FLAG{...}", memory_context=memory_context)
        system, user = build_agent_prompt(ctx)
        self.assertIn("RELEVANT FORGE MEMORY", user)
        self.assertIn("Template Injection", user)
        # Injecting memory must not leak the source challenge's flag/target.
        self.assertNotIn(CAPTURED_FLAG, user)


if __name__ == "__main__":
    unittest.main()
