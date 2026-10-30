"""
tests/test_privilege_classifier.py

Pure, side-effect-free tests for backend.execution.privilege_classifier.

The classifier is exercised through the real ``ExecutionRequest`` model and the
real tool registry.  No command is executed, no file or permission is modified,
and no database or audit-log write is performed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.execution.base import ExecutionRequest
from backend.execution.privilege_classifier import (
    OperationRisk,
    classify_operation,
)


def req(**kwargs):
    """Build an ExecutionRequest with safe defaults; caller supplies overrides."""
    return ExecutionRequest(command="", privilege_level="SAFE", **kwargs)


# ---------------------------------------------------------------------------
# Rule 1 & 2: the caller-supplied privilege_level is never trusted as safety.
# ---------------------------------------------------------------------------

class TestCallerLabelNeverMakesDangerousSafe(unittest.TestCase):
    def test_dangerous_command_with_safe_label_is_not_safe(self):
        for label in ("SAFE", "PRIVILEGED", "DANGEROUS", "", None):
            r = classify_operation(
                ExecutionRequest(command="sudo rm -rf /", privilege_level=label)
            )
            self.assertIsNot(r.risk, OperationRisk.SAFE, f"label={label!r}")
            self.assertEqual(r.risk, OperationRisk.DENY, f"label={label!r}")

    def test_privilege_wrappers_are_deny_even_with_safe_label(self):
        for cmd in ("sudo nmap 10.10.14.1", "su root -c id", "doas sh", "pkexec rm"):
            r = classify_operation(
                ExecutionRequest(command=cmd, privilege_level="SAFE")
            )
            self.assertEqual(r.risk, OperationRisk.DENY, cmd)

    def test_privilege_wrapper_argv_is_deny_even_with_safe_label(self):
        for argv in (["sudo", "nmap"], ["su", "root"], ["doas", "sh"]):
            r = classify_operation(
                ExecutionRequest(command="", argv=argv, privilege_level="SAFE")
            )
            self.assertEqual(r.risk, OperationRisk.DENY, str(argv))

    def test_privileged_command_not_safe_when_caller_supplies_safe_label(self):
        # sudo via argv is a privileged operation; the SAFE label must not downgrade it.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["sudo", "apt-get", "install", "nmap"],
                privilege_level="SAFE",
            )
        )
        self.assertEqual(r.risk, OperationRisk.DENY)


# ---------------------------------------------------------------------------
# Rule 3: arbitrary shell commands are not automatically SAFE.
# ---------------------------------------------------------------------------

class TestArbitraryShellNotSafe(unittest.TestCase):
    def test_arbitrary_shell_commands_are_not_safe(self):
        for cmd in ("whoami", "id", "ls -la", "cat /etc/passwd", "netstat -an"):
            r = classify_operation(
                ExecutionRequest(command=cmd, privilege_level="SAFE")
            )
            self.assertIsNot(r.risk, OperationRisk.SAFE, cmd)

    def test_unknown_binary_never_becomes_safe(self):
        r = classify_operation(
            ExecutionRequest(command="obscure_tool --run", privilege_level="SAFE")
        )
        self.assertEqual(r.risk, OperationRisk.UNKNOWN)

    def test_unknown_argv_executable_never_becomes_safe(self):
        r = classify_operation(
            ExecutionRequest(command="", argv=["obscure_tool", "--run"], privilege_level="SAFE")
        )
        self.assertEqual(r.risk, OperationRisk.UNKNOWN)


# ---------------------------------------------------------------------------
# Rule 4: shell chaining, pipelines, substitutions, redirections cannot bypass.
# ---------------------------------------------------------------------------

class TestShellConstructsCannotBypass(unittest.TestCase):
    def test_command_chaining_is_not_safe(self):
        for cmd in ("echo a; echo b", "make && ./run", "false || true", "echo a\necho b"):
            r = classify_operation(
                ExecutionRequest(command=cmd, privilege_level="SAFE")
            )
            self.assertIsNot(r.risk, OperationRisk.SAFE, cmd)

    def test_pipeline_is_approval_required(self):
        r = classify_operation(
            ExecutionRequest(command="curl -s http://t/ | grep flag", privilege_level="SAFE")
        )
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_command_substitution_is_not_safe(self):
        for cmd in ("echo $(whoami)", "echo `id`", 'echo "$(id)"'):
            r = classify_operation(
                ExecutionRequest(command=cmd, privilege_level="SAFE")
            )
            self.assertIsNot(r.risk, OperationRisk.SAFE, cmd)

    def test_variable_expansion_is_not_safe(self):
        for cmd in ("cat $HOME/notes", "cat ${HOME}/notes"):
            r = classify_operation(
                ExecutionRequest(command=cmd, privilege_level="SAFE")
            )
            self.assertIsNot(r.risk, OperationRisk.SAFE, cmd)

    def test_redirection_is_not_safe(self):
        for cmd in ("cat x > out.txt", "cat < in.txt"):
            r = classify_operation(
                ExecutionRequest(command=cmd, privilege_level="SAFE")
            )
            self.assertIsNot(r.risk, OperationRisk.SAFE, cmd)


# ---------------------------------------------------------------------------
# Rule 5: privilege wrappers are recognized conservatively.
# ---------------------------------------------------------------------------

class TestPrivilegeWrappersRecognized(unittest.TestCase):
    def test_sudo_su_doas_are_deny(self):
        for cmd in ("sudo nmap 10.10.14.1", "su root -c id", "doas sh", "pkexec rm"):
            r = classify_operation(
                ExecutionRequest(command=cmd, privilege_level="SAFE")
            )
            self.assertEqual(r.risk, OperationRisk.DENY, cmd)

    def test_shell_interpreter_is_approval_required(self):
        for cmd in ("bash -c id", "sh script.sh", "zsh -c 'echo hi'"):
            r = classify_operation(
                ExecutionRequest(command=cmd, privilege_level="SAFE")
            )
            self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED, cmd)

    def test_shell_interpreter_argv_is_approval_required(self):
        for argv in (["bash", "-c", "id"], ["sh", "script.sh"]):
            r = classify_operation(
                ExecutionRequest(command="", argv=argv, privilege_level="SAFE")
            )
            self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED, str(argv))


# ---------------------------------------------------------------------------
# Rule 6: unknown or unsupported operations never become SAFE.
# ---------------------------------------------------------------------------

class TestUnknownUnsupportedNeverSafe(unittest.TestCase):
    def test_unknown_command_is_not_safe(self):
        r = classify_operation(
            ExecutionRequest(command="not_a_real_tool --flag", privilege_level="SAFE")
        )
        self.assertEqual(r.risk, OperationRisk.UNKNOWN)

    def test_empty_command_is_deny(self):
        r = classify_operation(ExecutionRequest(command="", privilege_level="SAFE"))
        self.assertEqual(r.risk, OperationRisk.DENY)

    def test_unparsable_command_is_deny(self):
        r = classify_operation(ExecutionRequest(command="unclosed ' quote", privilege_level="SAFE"))
        self.assertEqual(r.risk, OperationRisk.DENY)


# ---------------------------------------------------------------------------
# Rule 7: known, bounded capability operations classified from actual definitions.
# ---------------------------------------------------------------------------

class TestKnownBoundedCapability(unittest.TestCase):
    def test_safe_capability_via_argv_is_safe(self):
        # curl is a registered SAFE tool; shell-free argv + verified capability binding.
        # curl's registered capabilities are web_testing/recon.
        r = classify_operation(
            ExecutionRequest(
                command="curl -i -s http://127.0.0.1/",
                argv=["curl", "-i", "-s", "http://127.0.0.1/"],
                capability="web_testing",
                tool_name="curl",
                privilege_level="SAFE",
            )
        )
        self.assertEqual(r.risk, OperationRisk.SAFE)

    def test_simple_shell_command_of_safe_tool_is_safe(self):
        # No shell syntax; curl is a registered safe tool.
        r = classify_operation(
            ExecutionRequest(command="curl -i -s http://127.0.0.1/", privilege_level="SAFE")
        )
        self.assertEqual(r.risk, OperationRisk.SAFE)

    def test_privileged_tool_is_approval_required_not_safe(self):
        # Register a temporary privileged tool to exercise the tool-definition branch.
        from backend.tools.registry import ToolMetadata, tool_registry

        tool_registry.register_tool(
            ToolMetadata(
                tool_name="privscan",
                capabilities=["priv_scan"],
                binary="privscan",
                version_command="privscan --version",
                installation_recipe="echo x",
                os_compatibility=["linux"],
                privilege_requirement="PRIVILEGED",
                risk_level="HIGH",
                timeout_seconds=60,
                args_template="{target}",
            )
        )
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["privscan", "target"],
                capability="priv_scan",
                tool_name="privscan",
                privilege_level="SAFE",
            )
        )
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_named_tool_not_bound_to_capability_is_not_safe(self):
        # tool_name given but not bound to the capability: cannot verify behaviour.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["curl", "http://x/"],
                capability="network_scanning",
                tool_name="curl",
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)


# ---------------------------------------------------------------------------
# Rule 8: argv parsing preserves argument boundaries; arg text is not shell syntax.
# ---------------------------------------------------------------------------

class TestArgvBoundariesPreserved(unittest.TestCase):
    def test_semicolon_inside_argv_element_is_not_shell_syntax(self):
        # argv elements are literal; a ';' inside one element is data, not chaining.
        # curl's registered capabilities are web_testing/recon.
        r = classify_operation(
            ExecutionRequest(
                command="curl http://h/; rm -rf /",  # display string only
                argv=["curl", "http://h/; rm -rf /"],
                capability="web_testing",
                tool_name="curl",
                privilege_level="SAFE",
            )
        )
        self.assertEqual(r.risk, OperationRisk.SAFE)

    def test_shell_looking_text_inside_argv_is_not_treated_as_chaining(self):
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["curl", "http://example.com; touch /tmp/x"],
                capability="web_testing",
                tool_name="curl",
                privilege_level="SAFE",
            )
        )
        self.assertEqual(r.risk, OperationRisk.SAFE)

    def test_argv_element_with_pipe_is_not_a_pipeline(self):
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["grep", "a|b", "notes.txt"],
                privilege_level="SAFE",
            )
        )
        # grep is not a registered tool -> UNKNOWN, not SAFE; the '|' is data.
        self.assertEqual(r.risk, OperationRisk.UNKNOWN)

    def test_quoted_semicolon_in_command_is_not_chaining(self):
        # The ';' is inside single quotes, so it is literal data to curl.
        r = classify_operation(
            ExecutionRequest(command="curl 'http://x; rm -rf /'", privilege_level="SAFE")
        )
        self.assertEqual(r.risk, OperationRisk.SAFE)


# ---------------------------------------------------------------------------
# Rule 9: deterministic, no execution or database side effects.
# ---------------------------------------------------------------------------

class TestDeterministicNoSideEffects(unittest.TestCase):
    def test_classification_is_deterministic(self):
        request = ExecutionRequest(
            command="curl -s http://x/ | grep flag",
            privilege_level="SAFE",
        )
        r1 = classify_operation(request)
        r2 = classify_operation(request)
        self.assertEqual(r1.risk, r2.risk)
        self.assertEqual(r1.reason, r2.reason)

    def test_same_operation_different_labels_gives_same_result(self):
        base = dict(command="sudo nmap 10.10.14.1")
        risks = {
            classify_operation(ExecutionRequest(**{**base, "privilege_level": l})).risk
            for l in ("SAFE", "PRIVILEGED", "DANGEROUS", "", None)
        }
        self.assertEqual(risks, {OperationRisk.DENY})

    def test_environment_assignment_prefix_is_not_safe(self):
        r = classify_operation(
            ExecutionRequest(command="VAR=value curl http://x", privilege_level="SAFE")
        )
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)


# ---------------------------------------------------------------------------
# Regression: concrete bypasses discovered while implementing.
# ---------------------------------------------------------------------------

class TestRegressionBypasses(unittest.TestCase):
    def test_sudo_as_a_mere_argument_is_not_a_wrapper(self):
        # 'sudo' quoted is just an argument to echo; not the executable.
        r = classify_operation(
            ExecutionRequest(command="echo sudo rm -rf /", privilege_level="SAFE")
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)

    def test_command_substitution_containing_sudo_is_not_safe(self):
        r = classify_operation(
            ExecutionRequest(command="echo $(sudo id)", privilege_level="SAFE")
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)


class TestFix04C1RInterpreterBindingNotSafe(unittest.TestCase):
    """Fix 04C-1R: interpreter execution must never be SAFE via a binding."""

    def test_python_interpreter_bound_to_unrelated_safe_tool_is_not_safe(self):
        # python -c ... bound to curl/web_testing (unrelated SAFE tool/capability).
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["python", "-c", "import os; os.system('id')"],
                capability="web_testing",
                tool_name="curl",
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_python_interpreter_bound_to_itself_as_safe_tool_is_not_safe(self):
        # Even if the registry later contains a SAFE tool whose binary is python,
        # arbitrary interpreter execution must never be SAFE (interpreter rule).
        from backend.tools.registry import ToolMetadata, tool_registry

        tool_registry.register_tool(
            ToolMetadata(
                tool_name="pyexec_probe",
                capabilities=["py_exec_probe"],
                binary="python",
                version_command="python --version",
                installation_recipe="echo x",
                os_compatibility=["linux", "windows"],
                privilege_requirement="SAFE",
                risk_level="LOW",
                timeout_seconds=60,
                args_template="{script}",
            )
        )
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["python", "-c", "import os; os.system('id')"],
                capability="py_exec_probe",
                tool_name="pyexec_probe",
                privilege_level="SAFE",
            )
        )
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)


class TestFix04C1RCapabilityToolMismatch(unittest.TestCase):
    """Fix 04C-1R: capability/tool binding must match the executable binary."""

    def test_curl_bound_to_unrelated_tool_capability_is_not_safe(self):
        # argv[0]=curl but tool_name=nmap / capability=network_scanning: binary
        # mismatch must not inherit nmap's SAFE classification.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["curl", "http://127.0.0.1/"],
                capability="network_scanning",
                tool_name="nmap",
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)


class TestFix04C1RArgumentValidation(unittest.TestCase):
    """Fix 04C-1R: argument-bearing operations are not SAFE without validation."""

    def test_curl_output_file_argument_not_safe(self):
        # curl -o /etc/shadow ... : file-writing argument.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["curl", "-o", "/etc/shadow", "http://127.0.0.1/"],
                capability="web_testing",
                tool_name="curl",
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_curl_long_output_option_not_safe(self):
        # curl --output /etc/shadow ... : file-writing argument.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["curl", "--output", "/etc/shadow", "http://127.0.0.1/"],
                capability="web_testing",
                tool_name="curl",
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_curl_output_file_simple_command_not_safe(self):
        # curl -o /etc/shadow ... as a simple (shell-free) command.
        r = classify_operation(
            ExecutionRequest(
                command="curl -o /etc/shadow http://127.0.0.1/", privilege_level="SAFE"
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_nmap_output_file_argument_not_safe(self):
        # nmap -oN /etc/out ... : file-writing argument.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["nmap", "-oN", "/etc/out", "127.0.0.1"],
                capability="network_scanning",
                tool_name="nmap",
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_strings_sensitive_file_argument_not_safe(self):
        # strings /etc/shadow ... : sensitive-filesystem access.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["strings", "/etc/shadow"],
                capability="file_analysis",
                tool_name="strings",
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_ffuf_output_file_argument_not_safe(self):
        # ffuf -o /tmp/out ... : file-writing argument.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=[
                    "ffuf",
                    "-o",
                    "/tmp/out",
                    "-u",
                    "http://127.0.0.1/FUZZ",
                    "-w",
                    "wl",
                ],
                capability="directory_enumeration",
                tool_name="ffuf",
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_simple_command_unvalidated_file_write_not_safe(self):
        # Simple shell-free command of a registered SAFE tool with unvalidated
        # file-writing arguments is not SAFE.
        r = classify_operation(
            ExecutionRequest(command="nmap -oN /etc/out 127.0.0.1", privilege_level="SAFE")
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)


class TestFix04C1REnvValidation(unittest.TestCase):
    """Fix 04C-1R: environment-controlled execution is never SAFE."""

    def test_env_dynamic_loading_cannot_be_safe(self):
        # LD_PRELOAD can alter dynamic-library loading before the target binary runs.
        r = classify_operation(
            ExecutionRequest(
                command="curl -s http://127.0.0.1/",
                env={"LD_PRELOAD": "/tmp/malicious.so"},
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_nonempty_environment_never_safe(self):
        # The classifier has no verified environment policy, so any supplied
        # environment cannot yield SAFE.
        r = classify_operation(
            ExecutionRequest(
                command="curl -s http://127.0.0.1/",
                env={"FOO": "bar"},
                privilege_level="SAFE",
            )
        )
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)


class TestFix04C1REnvAndArgBypass(unittest.TestCase):
    """Fix 04C-1R: combined env + binding must not be SAFE; argv literals stay literal."""

    def test_env_with_binding_not_safe(self):
        # Even with a verified capability binding, supplied env prevents SAFE.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["curl", "-s", "http://127.0.0.1/"],
                capability="web_testing",
                tool_name="curl",
                env={"LD_LIBRARY_PATH": "/tmp"},
                privilege_level="SAFE",
            )
        )
        self.assertIsNot(r.risk, OperationRisk.SAFE)
        self.assertEqual(r.risk, OperationRisk.APPROVAL_REQUIRED)

    def test_harmless_argv_literal_with_shell_chars_stays_safe_and_not_shell(self):
        # Harmless URL arg with shell-looking text stays literal in argv mode.
        r = classify_operation(
            ExecutionRequest(
                command="",
                argv=["curl", "http://example.com; touch /tmp/x"],
                capability="web_testing",
                tool_name="curl",
                privilege_level="SAFE",
            )
        )
        self.assertEqual(r.risk, OperationRisk.SAFE)


if __name__ == "__main__":
    unittest.main()
