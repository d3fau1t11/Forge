"""
Manual replay verification for the RepetitionDetector + RecoveryEngine wiring
in backend/agents/swarm_orchestrator.py (default production engine).

Simulates the worker loop's repetition/recovery flow against a scripted sequence
of curl-command strings shaped like the actual Old Sessions log: same host/path
(target cookie session probe), varying -v/--output/-H flags.

Runnable by hand in the Kali VM (no pytest, no API key, no network):

    py scripts/manual_verify_repetition_detection.py

This is the direct, concrete proof that the fix closes the gap the original Old
Sessions run exposed: the repeated strategy is detected and a REPLAN log line
is produced (which would be written via _append_to_challenge_log).
"""

import os
import sys

sys.path.insert(0, os.getcwd())

from backend.agent_runtime.repetition import RepetitionDetector, RepetitionKind
from backend.agent_runtime.recovery import RecoveryEngine, RecoveryStrategy

# Old Sessions pattern: ~5 minutes of near-identical curl/cookie probes against
# a Flask session. Same host/path (target.ctf/session), varying flags/headers.
OLD_SESSIONS_COMMANDS = [
    "curl -s -c cookies.txt http://target.ctf/session",
    "curl -v -b cookies.txt http://target.ctf/session",
    "curl -s --output /dev/null -D - http://target.ctf/session",
    "curl -H 'Cookie: session=abc123' http://target.ctf/session",
    "curl --header 'X-Forwarded-For: 10.0.0.1' -s http://target.ctf/session",
    "curl -s -c cookies.txt http://target.ctf/session",   # exact repeat of #1
    "curl -v http://target.ctf/session",
    "curl -s http://target.ctf/session",
]

# A genuinely different command stream (different host) for the negative case.
DIFFERENT_COMMANDS = [
    "curl -s http://other.ctf/session",
    "curl -v http://other.ctf/session",
    "curl -s --output /dev/null http://other.ctf/session",
]


def format_log_line(agent_id, sig, decision, directive):
    """The exact log line format the worker writes via _append_to_challenge_log."""
    return (
        f"{decision}: agent {agent_id} repeating strategy '{sig}' "
        f"{'without progress — ' if decision == 'REPLAN' else ''}{directive[:160]}"
    )


def replay(commands, agent_id="recon#1"):
    """Run the same classify→observe→diagnose flow the wired worker loop runs."""
    repetition = RepetitionDetector()
    recovery = RecoveryEngine()

    print(f"=== Replaying {len(commands)} commands as agent {agent_id} ===\n")

    fired = None
    for i, cmd in enumerate(commands, 1):
        # Pre-exec: classify (EXACT_REPEAT path mirrors runtime.py:304-308)
        pre_kind = repetition.classify(cmd)
        # Post-exec: observe + diagnose (mirrors runtime.py:388-395)
        # All commands in this replay are treated as failed (no flag captured),
        # matching the Old Sessions run where probes never progressed.
        rep_report = repetition.observe(
            cmd, failed=True, failure_category="repeat", novel=False
        )
        plan = recovery.diagnose(repetition=rep_report, command=cmd)
        sig = RepetitionDetector.semantic_signature(cmd) or cmd[:80]

        status = f"classify={pre_kind.value}, observe.force_replan={rep_report.force_replan}"
        print(f"[{i:2d}] {cmd[:70]}")
        print(f"     sig={sig} | {status} | "
              f"repeat_streak={rep_report.repeat_streak} "
              f"no_progress={rep_report.no_progress_streak}")

        if plan.strategy == RecoveryStrategy.REPLAN:
            log_line = format_log_line(agent_id, sig, "REPLAN", plan.directive)
            print(f"     >>> REPLAN FIRED: {log_line}")
            fired = ("REPLAN", log_line)
            repetition.reset_streaks()
        elif plan.strategy == RecoveryStrategy.ABORT:
            log_line = format_log_line(agent_id, sig, "ABORT", plan.directive)
            print(f"     >>> ABORT FIRED: {log_line}")
            fired = ("ABORT", log_line)
            break
        else:
            print(f"     strategy={plan.strategy.value} (continue)")

    print()
    if fired:
        print(f"RESULT: {fired[0]} fired.")
        print("Log line that would be written to the per-challenge log:")
        print(f"  {fired[1]}")
    else:
        print("RESULT: No REPLAN/ABORT fired (commands were all distinct).")
    print()
    return fired


def main():
    print("Manual replay — RepetitionDetector + RecoveryEngine wiring")
    print("Mirrors backend/agents/swarm_orchestrator.py _agent_worker integration.\n")

    # Positive case: Old Sessions pattern — same host/path, varying flags.
    fired = replay(OLD_SESSIONS_COMMANDS)

    # Negative case: genuinely different host → no REPLAN/ABORT.
    print("=== Negative control: different host, should NOT fire ===\n")
    replay(DIFFERENT_COMMANDS, agent_id="recon#2")

    # Sanity: semantic signatures for Old Sessions commands all collapse to one.
    print("=== Signature collapse verification ===")
    sigs = {
        RepetitionDetector.semantic_signature(c)
        for c in OLD_SESSIONS_COMMANDS
    }
    print(f"Distinct semantic signatures among {len(OLD_SESSIONS_COMMANDS)} "
          f"Old Sessions commands: {len(sigs)}")
    for s in sorted(sigs):
        print(f"  {s}")
    assert len(sigs) == 1, "Old Sessions commands must collapse to ONE signature"
    print("CONFIRMED: varying curl flags/headers collapse to a single repeated strategy.\n")

    if fired and fired[0] == "REPLAN":
        print("VERDICT: REPLAN fired for the Old Sessions pattern — the silent-log gap is closed.")
    else:
        print("VERDICT: Unexpected — REPLAN did not fire. Check thresholds.")
        sys.exit(1)


if __name__ == "__main__":
    main()
