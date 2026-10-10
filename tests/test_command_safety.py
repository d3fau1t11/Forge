"""
tests/test_command_safety.py

Command-execution safety hardening tests (default swarm command path).

Target-free by design: no network, no challenges, no external targets, no package
installs.  Real subprocess execution is mocked.  The tests exercise:

  * the shell-syntax classifier (chaining, substitution, expansion, redirection),
  * shell-free argv composition for capability templates (malicious target
    interpolation cannot become a second command),
  * explicit rejection of shell syntax in capability extra args,
  * the ProcessManager argv primitive using ``create_subprocess_exec`` (no shell),
  * preservation of the agent-authored shell route and target typo-correction.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Allow running as `python -m unittest discover tests` from the project root ──
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"


def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Shell-syntax classifier
# ─────────────────────────────────────────────────────────────────────────────

class TestShellSyntaxClassifier(unittest.TestCase):

    def test_pipeline_requires_shell(self):
        from backend.execution.command_safety import find_shell_syntax, requires_shell
        self.assertTrue(requires_shell("cat dump.bin | strings | grep -i flag"))
        self.assertEqual(find_shell_syntax("cat a | grep b"), "pipeline")

    def test_command_chaining_requires_shell(self):
        from backend.execution.command_safety import find_shell_syntax, requires_shell
        for cmd in ("echo a; echo b", "make && ./run", "false || true", "echo a\necho b"):
            self.assertTrue(requires_shell(cmd), cmd)
        self.assertEqual(find_shell_syntax("a && b"), "command_chaining")
        self.assertEqual(find_shell_syntax("a; b"), "command_chaining")

    def test_command_substitution_requires_shell(self):
        from backend.execution.command_safety import find_shell_syntax
        self.assertEqual(find_shell_syntax("echo $(whoami)"), "command_substitution")
        self.assertEqual(find_shell_syntax("echo `id`"), "command_substitution")
        # Substitution stays live INSIDE double quotes.
        self.assertEqual(find_shell_syntax('echo "$(id)"'), "command_substitution")
        self.assertEqual(find_shell_syntax('echo "`id`"'), "command_substitution")

    def test_redirection_and_background_require_shell(self):
        from backend.execution.command_safety import find_shell_syntax
        self.assertEqual(find_shell_syntax("cat x > out.txt"), "redirection")
        self.assertEqual(find_shell_syntax("cat < in.txt"), "redirection")
        self.assertEqual(find_shell_syntax("sleep 5 &"), "background")

    def test_variable_expansion_requires_shell(self):
        from backend.execution.command_safety import find_shell_syntax
        self.assertEqual(find_shell_syntax("cat $HOME/notes"), "variable_expansion")
        self.assertEqual(find_shell_syntax("cat ${HOME}/notes"), "variable_expansion")
        self.assertIsNone(find_shell_syntax("cat '$HOME'"))  # single quotes are literal

    def test_quoted_metacharacters_are_literal(self):
        from backend.execution.command_safety import requires_shell
        self.assertFalse(requires_shell("grep 'a;b' notes.txt"))
        self.assertFalse(requires_shell('grep "a|b" notes.txt'))
        self.assertFalse(requires_shell('grep "a&b" notes.txt'))

    def test_ordinary_valid_ctf_commands(self):
        from backend.execution.command_safety import find_shell_syntax, requires_shell
        # Single program + plain args: safe to run without a shell.
        for cmd in (
            "nmap -sV -F 10.10.14.1",
            "curl -i -s http://127.0.0.1:8888/",
            'python "C:\\work dir\\solve.py"',
            "strings -n 8 sample.bin",
            "grep -o 'flag{.*}' output.txt",
        ):
            self.assertFalse(requires_shell(cmd), cmd)
        # A genuine pipeline legitimately requires the shell.
        self.assertEqual(find_shell_syntax("curl -s http://t/ | grep flag"), "pipeline")

    def test_split_simple_command(self):
        from backend.execution.command_safety import split_simple_command
        self.assertEqual(split_simple_command("nmap -sV -F 10.10.14.1"),
                         ["nmap", "-sV", "-F", "10.10.14.1"])
        self.assertEqual(split_simple_command('python "C:\\work dir\\solve.py"'),
                         ["python", "C:\\work dir\\solve.py"])

    def test_split_simple_command_rejects_shell_syntax(self):
        from backend.execution.command_safety import split_simple_command, UnsupportedShellSyntax
        for cmd in ("a; rm -rf /", "a | b", "echo $(id)", "cat $HOME/x", "sleep 1 &"):
            with self.assertRaises(UnsupportedShellSyntax):
                split_simple_command(cmd)

    def test_parse_extra_args(self):
        from backend.execution.command_safety import parse_extra_args, UnsupportedShellSyntax
        self.assertEqual(parse_extra_args("--data 'a=b c'"), ["--data", "a=b c"])
        self.assertEqual(parse_extra_args(None), [])
        self.assertEqual(parse_extra_args("  "), [])
        for bad in ("--x; rm -rf /", "--x && y", "$(touch /tmp/p)", "a | b"):
            with self.assertRaises(UnsupportedShellSyntax):
                parse_extra_args(bad)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Shell-free argv composition
# ─────────────────────────────────────────────────────────────────────────────

class TestArgvComposition(unittest.TestCase):

    def test_build_argv_keeps_target_as_one_literal_token(self):
        from backend.execution.command_safety import build_argv
        argv = build_argv("curl", "-i -s {target}", target="http://h/; rm -rf /")
        self.assertEqual(argv, ["curl", "-i", "-s", "http://h/; rm -rf /"])
        # The payload is one element; it is never split into a command word.
        self.assertNotIn("rm", argv)
        self.assertNotIn("-rf", argv)

    def test_build_argv_rejects_shell_extra_args(self):
        from backend.execution.command_safety import build_argv, UnsupportedShellSyntax
        with self.assertRaises(UnsupportedShellSyntax):
            build_argv("curl", "-i -s {target}", target="http://h/",
                       extra_args="; touch /tmp/x")

    def test_substitute_template_keeps_value_inside_one_token(self):
        from backend.execution.command_safety import substitute_template
        self.assertEqual(
            substitute_template("-o {target}.log {target}", {"{target}": "evil; rm"}),
            ["-o", "evil; rm.log", "evil; rm"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Capability execution is shell-free
# ─────────────────────────────────────────────────────────────────────────────

class TestCapabilityExecutionIsShellFree(unittest.TestCase):
    """execute_capability must never surface an untrusted target/extra arg to a shell."""

    def _env(self):
        env = MagicMock()
        env.detect_environment.return_value = {"installed_tools": {}}
        return env

    def test_malicious_target_is_a_single_argv_element(self):
        import backend.tools.manager as mgr
        from backend.tools.manager import ToolManager
        from backend.execution.base import ExecutionResult
        captured = {}

        async def fake_run_argv(argv, **kwargs):
            captured["argv"] = list(argv)
            return ExecutionResult(status="SUCCESS", stdout="ok", exit_code=0,
                                   command=" ".join(argv))

        with patch.object(mgr, "environment_detector", self._env()), \
             patch.object(mgr.shutil, "which", return_value="/usr/bin/nmap"), \
             patch.object(mgr, "execution_service") as svc:
            svc.run_argv = AsyncMock(side_effect=fake_run_argv)
            res = _run(ToolManager().execute_capability(
                "network_scanning", "127.0.0.1; touch /tmp/pwned"))

        self.assertEqual(captured["argv"][0], "nmap")
        # The entire payload is ONE element...
        self.assertIn("127.0.0.1; touch /tmp/pwned", captured["argv"])
        # ...and never decomposed into a separate command word.
        self.assertNotIn("touch", captured["argv"])
        # The shell-string path was never used.
        svc.run_command.assert_not_called()
        svc.run_argv.assert_called_once()
        self.assertEqual(res.status, "SUCCESS")

    def test_shell_syntax_in_extra_args_is_rejected_explicitly(self):
        import backend.tools.manager as mgr
        from backend.tools.manager import ToolManager
        with patch.object(mgr, "environment_detector", self._env()), \
             patch.object(mgr.shutil, "which", return_value="/usr/bin/nmap"), \
             patch.object(mgr, "execution_service") as svc:
            res = _run(ToolManager().execute_capability(
                "network_scanning", "10.10.14.1", extra_args="; touch /tmp/pwned"))
        self.assertEqual(res.status, "FAILED")
        self.assertEqual(res.failure_category, "UNSUPPORTED_SHELL_SYNTAX")
        self.assertTrue(res.execution_failure)
        svc.run_argv.assert_not_called()
        svc.run_command.assert_not_called()

    def test_ordinary_capability_composes_expected_argv(self):
        import backend.tools.manager as mgr
        from backend.tools.manager import ToolManager
        from backend.execution.base import ExecutionResult
        captured = {}

        async def fake_run_argv(argv, **kwargs):
            captured["argv"] = list(argv)
            return ExecutionResult(status="SUCCESS", stdout="ok", exit_code=0)

        with patch.object(mgr, "environment_detector", self._env()), \
             patch.object(mgr.shutil, "which", return_value="/usr/bin/nmap"), \
             patch.object(mgr, "execution_service") as svc:
            svc.run_argv = fake_run_argv
            _run(ToolManager().execute_capability("network_scanning", "10.10.14.1"))
        self.assertEqual(captured["argv"], ["nmap", "10.10.14.1", "-sV", "-F"])

    def test_generic_tool_target_with_space_is_one_token(self):
        import backend.tools.manager as mgr
        from backend.tools.manager import ToolManager
        from backend.execution.base import ExecutionResult
        captured = {}

        async def fake_run_argv(argv, **kwargs):
            captured["argv"] = list(argv)
            return ExecutionResult(status="SUCCESS", stdout="", exit_code=0)

        with patch.object(mgr, "environment_detector", self._env()), \
             patch.object(mgr.shutil, "which", return_value="/usr/bin/strings"), \
             patch.object(mgr, "execution_service") as svc:
            svc.run_argv = fake_run_argv
            _run(ToolManager().execute_capability("file_analysis", "my file; rm -rf /"))
        self.assertEqual(captured["argv"], ["strings", "-n", "8", "my file; rm -rf /"])

    def test_nmap_http_target_keeps_host_and_port_as_separate_args(self):
        import backend.tools.manager as mgr
        from backend.tools.manager import ToolManager
        from backend.execution.base import ExecutionResult
        captured = {}

        async def fake_run_argv(argv, **kwargs):
            captured["argv"] = list(argv)
            return ExecutionResult(status="SUCCESS", stdout="", exit_code=0)

        with patch.object(mgr, "environment_detector", self._env()), \
             patch.object(mgr.shutil, "which", return_value="/usr/bin/nmap"), \
             patch.object(mgr, "execution_service") as svc:
            svc.run_argv = fake_run_argv
            _run(ToolManager().execute_capability("network_scanning", "http://10.10.14.1:8080/"))
        self.assertEqual(captured["argv"], ["nmap", "10.10.14.1", "-sV", "-F", "-p", "8080"])


# ─────────────────────────────────────────────────────────────────────────────
# 4. ProcessManager argv primitive (shell=False)
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessManagerRunArgv(unittest.TestCase):

    def test_run_argv_uses_exec_not_shell(self):
        from backend.execution.process_manager import ProcessManager
        pm = ProcessManager()
        fake_proc = MagicMock()
        fake_proc.pid = 4321
        with patch("asyncio.create_subprocess_exec",
                   new=AsyncMock(return_value=fake_proc)) as exec_mock, \
             patch.object(ProcessManager, "_monitor",
                          new=AsyncMock(return_value=("out", "", 0))):
            res = _run(pm.run_argv(["curl", "http://h/; rm -rf /", "-s"]))
        exec_mock.assert_awaited_once()
        args, kwargs = exec_mock.await_args
        self.assertEqual(args, ("curl", "http://h/; rm -rf /", "-s"))
        self.assertNotIn("shell", kwargs)
        self.assertEqual(res, ("out", "", 0))

    def test_run_argv_empty_is_rejected_without_spawning(self):
        from backend.execution.process_manager import ProcessManager
        pm = ProcessManager()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock:
            res = _run(pm.run_argv([]))
        exec_mock.assert_not_awaited()
        self.assertEqual(res[2], -1)

    def test_run_still_uses_shell_for_agent_commands(self):
        from backend.execution.process_manager import ProcessManager
        pm = ProcessManager()
        fake_proc = MagicMock()
        fake_proc.pid = 999
        with patch("asyncio.create_subprocess_shell",
                   new=AsyncMock(return_value=fake_proc)) as shell_mock, \
             patch.object(ProcessManager, "_monitor",
                          new=AsyncMock(return_value=("ok", "", 0))):
            _run(pm.run("cat a | grep b"))
        shell_mock.assert_awaited_once()
        self.assertEqual(shell_mock.await_args.args[0], "cat a | grep b")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Raw (agent-authored) path preserved + target correction cannot inject
# ─────────────────────────────────────────────────────────────────────────────

class TestRawCommandPathPreserved(unittest.TestCase):

    def test_pipeline_is_not_split_and_reaches_shell_route(self):
        from backend.tools.manager import ToolManager
        from backend.execution.base import ExecutionResult
        captured = {}

        async def fake_run_command(command, **kwargs):
            captured["command"] = command
            return ExecutionResult(status="SUCCESS", stdout="flag", exit_code=0)

        with patch("backend.tools.manager.execution_service") as svc:
            svc.run_command = fake_run_command
            _run(ToolManager().execute_raw_command("curl -s http://t/ | grep -i flag"))
        self.assertEqual(captured["command"], "curl -s http://t/ | grep -i flag")

    def test_malicious_canonical_target_cannot_inject_via_correction(self):
        from backend.tools.manager import sanitize_and_correct_command_target
        cmd = "curl http://evi1.com/"  # near-miss of evil.com
        out = sanitize_and_correct_command_target(cmd, "http://evil.com; touch /tmp/pwned")
        self.assertEqual(out, cmd)
        self.assertNotIn("touch", out)

    def test_benign_canonical_target_still_corrects_typo(self):
        from backend.tools.manager import sanitize_and_correct_command_target
        out = sanitize_and_correct_command_target("curl http://evi1.com/", "http://evil.com")
        self.assertEqual(out, "curl http://evil.com/")


if __name__ == "__main__":
    unittest.main()
