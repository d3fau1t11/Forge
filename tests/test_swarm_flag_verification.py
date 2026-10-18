"""Regression tests for the default swarm engine's flag-verification lifecycle.

Background (read-only audit): the default ``swarm_orchestrator`` engine promoted
*any* ``FLAG_REGEX`` match found in tool output straight to ``flag_captured`` —
without ever invoking the authoritative :class:`FlagVerifier`. Two production
false captures followed:

* "Old Sessions" — a failed script raised ``IndexError``; the regex scraped
  ``flag{")[1].split("}`` out of the traceback and FORGE announced success.
* "Crack the Gate 1" — an agent-generated script printed ``flag{test}`` (exit 0)
  and it was accepted, while the real flag was missed.

These tests exercise the real ``SwarmOrchestrator._check_tool_output_for_flags``
promotion path (not the verifier in isolation). A raw regex match must never be
sufficient to capture a flag, fire the flag event, or stop the swarm.
"""

import os
import base64
import asyncio
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.agents.swarm_orchestrator import SwarmOrchestrator, SwarmBlackboard
from backend.agent_runtime.verifier import (
    FlagVerifier, FlagSource, FlagStatus, EvidenceOrigin,
)
from backend.tools.manager import ToolExecutionResult


def _board() -> SwarmBlackboard:
    return SwarmBlackboard("chal_flagverify", "run_flagverify", "http://target.ctf")


def _result(*, stdout="", stderr="", status="SUCCESS", exit_code=0, execution_failure=False):
    return ToolExecutionResult(
        tool_name="bash", capability="custom_command", command="",
        status=status, stdout=stdout, stderr=stderr, exit_code=exit_code,
        execution_failure=execution_failure,
    )


class TestSwarmFlagVerification(unittest.TestCase):

    def test_1_traceback_candidate_not_captured(self):
        """A flag-shaped value scraped from a FAILED command's traceback must not
        set flag_captured, fire the flag event, or count as a solved outcome."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            traceback_text = (
                "Traceback (most recent call last):\n"
                '  File "solve_agent_1_deadbeef.py", line 3, in <module>\n'
                '    print(flag{")[1].split("})\n'
                "IndexError: list index out of range"
            )
            failed = _result(stdout="", stderr=traceback_text,
                             status="FAILED", exit_code=1)
            await orch._check_tool_output_for_flags(
                traceback_text, board, "agent_1",
                command='python "solve_agent_1_deadbeef.py"', result=failed)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            # The value is retained as a non-authoritative candidate for review.
            self.assertIn('flag{")[1].split("}',
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_2_agent_script_printing_fake_flag_not_captured(self):
        """A generated script that prints flag{test} must not be accepted merely
        because its process exits 0 — the value is constructed in agent-authored
        source, not captured from the target."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            script_path = os.path.join("workdir", "solve_agent_1_abc12345.py")
            script_src = "print('debug placeholder')\nprint('flag{test}')\n"
            board.register_agent_source(script_path, script_src)

            succeeded = _result(stdout="flag{test}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "flag{test}\n", board, "agent_1",
                command=f'python "{script_path}"', result=succeeded)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("flag{test}", [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_3_local_script_unknown_source_fails_closed(self):
        """A value from agent-authored local code whose source we cannot inspect
        must fail closed (candidate only) even when the process exits 0."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            succeeded = _result(stdout="flag{scr1pt_0utput}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "flag{scr1pt_0utput}\n", board, "agent_1",
                command='python "solve_agent_1_unregistered.py"', result=succeeded)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("flag{scr1pt_0utput}", [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_4_genuine_target_output_is_captured(self):
        """A flag observed in a successful direct target command's output still
        reaches the authoritative verified success path."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            ok = _result(stdout="picoCTF{r3al_c4ptur3_0k}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "picoCTF{r3al_c4ptur3_0k}\n", board, "agent_2",
                command="curl -s http://target.ctf/flag", result=ok)

            self.assertEqual(board.flag_captured, "picoCTF{r3al_c4ptur3_0k}")
            self.assertTrue(board.flag_event.is_set())

        asyncio.run(flow())

    def test_5_script_source_naming_target_does_not_establish_provenance(self):
        """A generated solver whose *source* merely names the target hostname must
        NOT verify. A hostname in generated code (a URL/import/comment/string) is
        not proof the script's output came from the target — the script controls its
        own stdout. It remains candidate-only (fail closed)."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            script_path = os.path.join("workdir", "solve_agent_1_fetch.py")
            board.register_agent_source(
                script_path,
                "import requests\n"
                "# contact http://target.ctf for the real flag\n"
                "r = requests.get('http://target.ctf')\n"
                "print(r.text)\n")
            # The script prints a fabricated value, not the fetched text.
            ok = _result(stdout="picoCTF{f4br1c4t3d_by_scr1pt}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "picoCTF{f4br1c4t3d_by_scr1pt}\n", board, "agent_1",
                command=f'python "{script_path}"', result=ok)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("picoCTF{f4br1c4t3d_by_scr1pt}",
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_6_raw_tool_output_source_without_verdict_never_captures(self):
        """Defence in depth: recording a tool_output candidate without an explicit
        VERIFIED verdict must fail closed."""
        async def flow():
            board = _board()
            await board.record_flag_candidate("flag{no_verdict_given}", "agent_9", "tool_output")
            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("flag{no_verdict_given}",
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_7_successful_local_command_with_fabricated_flag_not_captured(self):
        """A successful *local* command whose output merely contains a plausible
        flag-shaped value (not from the target, not echoed in the command) must not
        be captured solely because the command exited 0."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            # base64-decoded local data the agent supplied — output looks like a flag
            # but never came from the target.
            ok = _result(stdout="flag{l0c4l_f4br1c4t3d}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "flag{l0c4l_f4br1c4t3d}\n", board, "agent_1",
                command="printf 'ZmxhZ3tsMGM0bF9mNGJyMWM0dDNkfQ==' | base64 -d",
                result=ok)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("flag{l0c4l_f4br1c4t3d}",
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_8_inline_agent_code_constructed_flag_not_captured(self):
        """An inline interpreter one-liner that constructs a plausible flag at
        runtime (so it is not a literal substring of the command) must fail closed:
        the value is agent-authored code output, not a captured target response."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            ok = _result(stdout="picoCTF{1nl1n3_c0nstruct3d}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "picoCTF{1nl1n3_c0nstruct3d}\n", board, "agent_1",
                command="python3 -c \"print('picoCTF{'+'1nl1n3_c0nstruct3d'+'}')\"",
                result=ok)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("picoCTF{1nl1n3_c0nstruct3d}",
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_9_unfamiliar_prefix_from_target_is_captured(self):
        """A legitimate flag whose prefix is NOT in the hardcoded list must still be
        discovered (via the challenge's declared expected format) and verified when
        it is recovered from a target response."""
        async def flow():
            board = _board()
            board.flag_pattern = "FORGE{...}"
            orch = SwarmOrchestrator()
            ok = _result(stdout="FORGE{unf4m1l14r_but_r34l}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "FORGE{unf4m1l14r_but_r34l}\n", board, "agent_2",
                command="curl -s http://target.ctf/secret", result=ok)

            self.assertEqual(board.flag_captured, "FORGE{unf4m1l14r_but_r34l}")
            self.assertTrue(board.flag_event.is_set())

        asyncio.run(flow())

    def test_10_decoded_artifact_from_target_can_be_captured(self):
        """A flag recovered by deterministically decoding a target response reaches
        the same verification decision and can promote to captured."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            flag = "picoCTF{dec0d3d_fr0m_t4rg3t}"
            raw = base64.b64encode(flag.encode()).decode()
            ok = _result(stdout=raw, status="SUCCESS", exit_code=0)
            await orch._apply_decoded_directives(
                raw, board, "agent_3",
                command="curl -s http://target.ctf/hint", result=ok)

            self.assertEqual(board.flag_captured, flag)
            self.assertTrue(board.flag_event.is_set())

        asyncio.run(flow())

    def test_11_decoded_artifact_without_target_provenance_fails_closed(self):
        """The same decoded value, but produced by a local (non-target) command,
        must NOT be captured — it never reached the target."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            flag = "picoCTF{l0c4l_d3c0d3_f4k3}"
            raw = base64.b64encode(flag.encode()).decode()
            ok = _result(stdout=raw, status="SUCCESS", exit_code=0)
            await orch._apply_decoded_directives(
                raw, board, "agent_3",
                command="base64 -d blob.txt", result=ok)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn(flag, [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_12_multi_step_chain_without_validated_evidence_is_not_verified(self):
        """The dormant MULTI_STEP_CHAIN trusted-origin route is closed: supplying the
        enum value alone (with no validated, evidence-backed chain) must NOT verify,
        even for a successful action. A failed action also fails closed."""
        v = FlagVerifier()
        supplied_alone = v.assess(
            "picoCTF{mult1_st3p_0k}", source=FlagSource.TOOL_OUTPUT,
            command="python solve.py", action_succeeded=True,
            expected_format="picoCTF{...}",
            origin=EvidenceOrigin.MULTI_STEP_CHAIN)
        self.assertFalse(supplied_alone.is_verified)
        self.assertEqual(supplied_alone.status, FlagStatus.CANDIDATE)

        failed = v.assess(
            "picoCTF{mult1_st3p_fail}", source=FlagSource.TOOL_OUTPUT,
            command="python solve.py", action_succeeded=False,
            expected_format="picoCTF{...}",
            origin=EvidenceOrigin.MULTI_STEP_CHAIN)
        self.assertFalse(failed.is_verified)
        self.assertEqual(failed.status, FlagStatus.CANDIDATE)

        # And it must not be part of the trusted-origin set at all.
        from backend.agent_runtime.verifier import _TRUSTED_ORIGINS
        self.assertNotIn(EvidenceOrigin.MULTI_STEP_CHAIN, _TRUSTED_ORIGINS)

    def test_13_expected_format_is_not_decisive_authenticity(self):
        """Matching the declared expected format is supporting evidence only: a
        format-matching value with untrusted provenance (local observation) must
        not verify."""
        verified = FlagVerifier().assess(
            "FORGE{m4tch3s_f0rm4t}", source=FlagSource.TOOL_OUTPUT,
            command="curl -s http://target.ctf/flag", action_succeeded=True,
            target_scope="http://target.ctf",
            expected_format="FORGE{...}",
            origin=EvidenceOrigin.TARGET_RESPONSE)
        self.assertEqual(verified.status, FlagStatus.VERIFIED)

        untrusted = FlagVerifier().assess(
            "FORGE{m4tch3s_f0rm4t}", source=FlagSource.TOOL_OUTPUT,
            command="cat out.txt", action_succeeded=True,
            expected_format="FORGE{...}",
            origin=EvidenceOrigin.LOCAL_OBSERVATION)
        self.assertFalse(untrusted.is_verified)
        self.assertEqual(untrusted.status, FlagStatus.CANDIDATE)

    def test_14_missing_provenance_fails_closed(self):
        """A caller that supplies NO provenance (origin=UNKNOWN) must fail closed:
        a successful command plus a flag-shaped value is not proof. An explicit
        untrusted origin fails closed too."""
        missing = FlagVerifier().assess(
            "picoCTF{n0_pr0v3n4nc3}", source=FlagSource.TOOL_OUTPUT,
            command="curl -s http://target.ctf/flag", action_succeeded=True)
        self.assertFalse(missing.is_verified)
        self.assertEqual(missing.status, FlagStatus.CANDIDATE)

        explicit = FlagVerifier().assess(
            "picoCTF{explicit_0r1g1n}", source=FlagSource.TOOL_OUTPUT,
            command="curl -s http://target.ctf/flag", action_succeeded=True,
            origin=EvidenceOrigin.LOCAL_OBSERVATION)
        self.assertFalse(explicit.is_verified)
        self.assertEqual(explicit.status, FlagStatus.CANDIDATE)

    def test_15_agent_script_with_hostname_in_comment_is_candidate_only(self):
        """Audit case A: an agent-authored script constructs and prints a fake flag
        while mentioning the target hostname in a comment / unrelated string. The
        hostname must not establish provenance — candidate only."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            script_path = os.path.join("workdir", "solve_agent_2_hostname.py")
            board.register_agent_source(
                script_path,
                "# reachable at http://target.ctf but we just fabricate the flag\n"
                "NOTE = 'target.ctf is the target'\n"
                "print('picoCTF{f4k3_fr0m_c0mm3nt}')\n")
            ok = _result(stdout="picoCTF{f4k3_fr0m_c0mm3nt}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "picoCTF{f4k3_fr0m_c0mm3nt}\n", board, "agent_2",
                command=f'python "{script_path}"', result=ok)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("picoCTF{f4k3_fr0m_c0mm3nt}",
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_16_local_command_naming_hostname_is_candidate_only(self):
        """Audit case A/B: a local command that merely mentions the target hostname
        (e.g. grepping a local file) does not establish target-derived output, even
        when it succeeds and its output contains a flag-shaped string."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            ok = _result(stdout="picoCTF{gr3pp3d_l0c4lly}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "picoCTF{gr3pp3d_l0c4lly}\n", board, "agent_1",
                command="grep -r 'target.ctf' ./notes.txt", result=ok)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("picoCTF{gr3pp3d_l0c4lly}",
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_17_decoding_local_blob_with_target_url_is_candidate_only(self):
        """Audit case B: a local file containing a target URL AND a base64 blob must
        not become trusted merely because decoding succeeds. Provenance comes from
        how the data was obtained, not from its contents."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            flag = "picoCTF{l0c4l_bl0b_w1th_url}"
            raw = ("see http://target.ctf for context\n"
                   + base64.b64encode(flag.encode()).decode() + "\n")
            # The command is a local read, not a target fetch.
            ok = _result(stdout=raw, status="SUCCESS", exit_code=0)
            await orch._apply_decoded_directives(
                raw, board, "agent_1", command="cat local_blob.txt", result=ok)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn(flag, [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_18_agent_script_output_through_decoder_cannot_bypass(self):
        """Audit case B: agent-generated output routed through the decoder must fail
        closed — decoding does not launder agent-authored provenance."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            flag = "picoCTF{scr1pt_v14_d3c0d3r}"
            script_path = os.path.join("workdir", "solve_agent_3_decode.py")
            board.register_agent_source(script_path, "print('emit encoded data')\n")
            raw = base64.b64encode(flag.encode()).decode()
            ok = _result(stdout=raw, status="SUCCESS", exit_code=0)
            await orch._apply_decoded_directives(
                raw, board, "agent_3", command=f'python "{script_path}"', result=ok)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn(flag, [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_19_compound_command_composition_is_not_a_target_response(self):
        """Every command-composition bypass must fail closed: a target URL occurring
        *somewhere* in the command — an unrelated URL, a header value, a comment, a
        later compound stage — does not make the reported output a target response.

        None of these are a trustworthy target response, so a flag-shaped value in
        the output is retained as a candidate and never captured.
        """
        from backend.agent_runtime.verifier import command_reaches_target

        examples = [
            "curl http://evil.example/x -H 'Referer: http://target.ctf/'",
            "curl http://target.ctf/none; python3 -c \"print('picoCTF{'+'fake'+'}')\"",
            "curl http://target.ctf/x; base64 -d <<< ZmFrZQ==",
            "curl http://evil.example/ # http://target.ctf/",
            "curl http://target.ctf/x | base64 -d",
            "curl -L http://target.ctf/redirected",
            "wget -qO- http://target.ctf/redirected",
            "curl $(echo http://target.ctf/)",
        ]
        fabricated = "picoCTF{c0mp0s3d_byp4ss_f4k3}"

        async def flow():
            for command in examples:
                self.assertFalse(
                    command_reaches_target(command, "http://target.ctf"),
                    msg=f"command must not reach target: {command}")
                board = _board()
                orch = SwarmOrchestrator()
                ok = _result(stdout=fabricated + "\n", status="SUCCESS", exit_code=0)
                await orch._check_tool_output_for_flags(
                    fabricated + "\n", board, "agent_1", command=command, result=ok)
                self.assertIsNone(board.flag_captured, msg=f"captured on: {command}")
                self.assertFalse(board.flag_event.is_set(), msg=f"flag event on: {command}")
                self.assertIn(fabricated, [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_20_rejected_first_candidate_does_not_mask_later_supported_candidate(self):
        """A rejected (echoed) first candidate must not prevent a later, genuinely
        supported candidate in the same output from being evaluated and captured."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            echoed = "picoCTF{3ch03d_1n_c0mm4nd}"
            genuine = "picoCTF{l4t3r_g3nu1n3}"
            # The first value is echoed in the issued command (rejected); the second
            # is a fresh value from the same target response (verified).
            command = f'curl -s http://target.ctf/flag -H "X-Debug: {echoed}"'
            output = f"{echoed}\n{genuine}\n"
            ok = _result(stdout=output, status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                output, board, "agent_2", command=command, result=ok)

            self.assertEqual(board.flag_captured, genuine)
            self.assertTrue(board.flag_event.is_set())
            self.assertIn(echoed, [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_21_candidate_verdict_without_verified_status_never_captures(self):
        """Defence in depth: an explicit but non-VERIFIED verdict (a local
        observation) must not promote a candidate — no flag_captured, no event."""
        async def flow():
            board = _board()
            verdict = FlagVerifier().assess(
                "picoCTF{l0c4l_0bs3rv4t10n}", source=FlagSource.TOOL_OUTPUT,
                command="cat local.txt", action_succeeded=True,
                target_scope="http://target.ctf",
                origin=EvidenceOrigin.LOCAL_OBSERVATION)
            self.assertFalse(verdict.is_verified)
            await board.record_flag_candidate(
                "picoCTF{l0c4l_0bs3rv4t10n}", "agent_9", "tool_output", verdict=verdict)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("picoCTF{l0c4l_0bs3rv4t10n}",
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_22_agent_script_host_comment_runtime_constructed_flag_not_captured(self):
        """An agent-authored script mentions the challenge host in a comment and
        CONSTRUCTS the flag at runtime, so the value is not a literal in the
        registered source. The hostname is not provenance and the stdout is
        agent-controlled — the value stays a candidate (fail closed)."""
        async def flow():
            board = _board()
            orch = SwarmOrchestrator()
            script_path = os.path.join("workdir", "solve_agent_4_runtime.py")
            board.register_agent_source(
                script_path,
                "# the target is http://target.ctf (context comment only)\n"
                "print('picoCTF{' + 'run' + 't1m3_bu1lt' + '}')\n")
            ok = _result(stdout="picoCTF{runt1m3_bu1lt}\n", status="SUCCESS", exit_code=0)
            await orch._check_tool_output_for_flags(
                "picoCTF{runt1m3_bu1lt}\n", board, "agent_4",
                command=f'python "{script_path}"', result=ok)

            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("picoCTF{runt1m3_bu1lt}",
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_23_swarm_model_prose_candidate_only_never_captures(self):
        """The swarm's model-prose recorder (used for ``FLAG:`` lines and Gemini
        vision descriptions) can only ever record an unverified candidate: no
        flag_captured and no terminal flag event."""
        async def flow():
            board = _board()
            await board.record_flag_candidate(
                "picoCTF{llm_pr0s3_cl41m}", "agent_7", "llm_reported")
            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("picoCTF{llm_pr0s3_cl41m}",
                          [c["flag"] for c in board.flag_candidates])

        asyncio.run(flow())

    def test_24_caller_trusted_origin_cannot_override_agent_code_evidence(self):
        """A caller asserting a trusted TARGET_RESPONSE/DECODED_ARTIFACT origin must
        not override execution evidence that the value came from agent-authored
        code. Supplying the enum value alone (or over agent code) fails closed."""
        for claimed in (EvidenceOrigin.TARGET_RESPONSE, EvidenceOrigin.DECODED_ARTIFACT):
            with self.subTest(origin=claimed):
                v = FlagVerifier().assess(
                    "picoCTF{l4t3nt_0r1g1n}", source=FlagSource.TOOL_OUTPUT,
                    command="python solve.py", action_succeeded=True,
                    target_scope="http://target.ctf",
                    agent_source="print('not the flag')", agent_code=True,
                    origin=claimed)
                self.assertFalse(v.is_verified)
                self.assertEqual(v.status, FlagStatus.CANDIDATE)

    def test_25_claimed_target_origin_contradicted_by_command_fails_closed(self):
        """A caller claiming a TARGET_RESPONSE origin is not evidence by itself: when
        the producing command demonstrably never reached the target, the claim is
        downgraded and the value stays a candidate."""
        v = FlagVerifier().assess(
            "picoCTF{cl41m3d_t4rg3t}", source=FlagSource.TOOL_OUTPUT,
            command="cat local_flag.txt", action_succeeded=True,
            target_scope="http://target.ctf",
            origin=EvidenceOrigin.TARGET_RESPONSE)
        self.assertFalse(v.is_verified)
        self.assertEqual(v.status, FlagStatus.CANDIDATE)

    def test_26_claimed_target_origin_without_target_scope_fails_closed(self):
        """Without the challenge target scope the verifier cannot substantiate a
        claimed target origin, so it fails closed instead of trusting the enum."""
        v = FlagVerifier().assess(
            "picoCTF{n0_sc0p3_cl41m}", source=FlagSource.TOOL_OUTPUT,
            command="curl -s http://target.ctf/flag", action_succeeded=True,
            origin=EvidenceOrigin.TARGET_RESPONSE)
        self.assertFalse(v.is_verified)
        self.assertEqual(v.status, FlagStatus.CANDIDATE)


if __name__ == "__main__":
    unittest.main()
