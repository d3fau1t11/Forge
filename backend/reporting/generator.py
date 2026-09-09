"""
FORGE Writeup / Report generator.

Authors a DETAILED, TECHNICAL CTF writeup for a solved (or in-progress) challenge.
The writeup is written by the LLM provider chain (Gemini FIRST for this task — see
the router capability ``report_generation``) from the REAL run telemetry FORGE
collected, never from fabricated content:

  * every tool execution (command + stdout/stderr/exit/status/duration) — ``ToolExecutionModel``
  * the swarm blackboard snapshot (endpoints, headers, cookies, decoded secrets,
    execution history) persisted in ``challenge.mission_plan["blackboard_state"]``
  * findings / evidence rows when present
  * the distilled ``ExperienceModel`` (technique, methodology, blue-team indicators)
  * the captured flag and the exact command whose real output contained it

The LLM prompt forbids inventing commands/endpoints/outputs/flags (rule §1 —
no demo/mock data). If every provider is exhausted, a deterministic, fully
grounded fallback writeup is rendered from the very same telemetry so the
operator always gets a real, non-empty writeup.
"""

import os
import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy import asc
from sqlalchemy.orm import Session

from backend.database.models import (
    ChallengeModel, TargetProfileModel, EvidenceModel, FindingModel,
    RunModel, ToolExecutionModel, ExperienceModel, ReportModel,
)
from backend.utils.workspace import resolve_safe_working_dir

logger = logging.getLogger("forge.reporting")

# Known CTF flag shapes — used to locate the exact command whose output produced
# the flag. Mirrors memory_models._FLAG_RE / swarm FLAG_REGEX intent (kept local
# to avoid an import cycle with the swarm).
_FLAG_RE = re.compile(
    r"\b(?:picoCTF|FLAG|flag|HTB|CTF|THM|pico|CSAW|ctf)\{[^}\n]{0,200}\}", re.IGNORECASE
)

_MAX_EXEC_IN_PROMPT = 25          # cap executions fed to the model (token budget)
_OUTPUT_TRIM_PROMPT = 800         # per-output trim in the LLM prompt
_OUTPUT_TRIM_MD = 1200            # per-output trim in the deterministic writeup

# Lightweight tech fingerprints for the recon section (deterministic fallback).
_TECH_FP = {
    "nginx": "nginx", "apache": "Apache", "httpd": "Apache", "werkzeug": "Werkzeug",
    "flask": "Flask", "express": "Express", "php": "PHP", "tomcat": "Tomcat",
    "microsoft-iis": "IIS", "gunicorn": "Gunicorn", "node": "Node.js", "django": "Django",
}


class ReportGenerator:
    """Builds a technical CTF writeup from real run telemetry (LLM-authored; deterministic fallback)."""

    # ------------------------------------------------------------------ #
    # Context gathering (shared by the AI path and the deterministic path)
    # ------------------------------------------------------------------ #

    def gather_context(self, db: Session, challenge_id: str) -> Optional[Dict[str, Any]]:
        """Collect all REAL telemetry for a challenge. Returns None if the challenge
        does not exist. Nothing here is synthesised — every field is a stored row."""
        challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
        if not challenge:
            return None

        targets = db.query(TargetProfileModel).filter(
            TargetProfileModel.challenge_id == challenge_id).all()
        # Multi-target artifacts/URLs/IPs are joined with '+' per no_demo_data rule §4.
        target_str = " + ".join(t.current_address for t in targets if t.current_address) or "Unknown Target"

        runs = db.query(RunModel).filter(RunModel.challenge_id == challenge_id).all()
        run_ids = [r.id for r in runs]
        execs: List[ToolExecutionModel] = []
        if run_ids:
            execs = (db.query(ToolExecutionModel)
                     .filter(ToolExecutionModel.run_id.in_(run_ids))
                     .order_by(asc(ToolExecutionModel.created_at))
                     .all())

        findings = db.query(FindingModel).filter(FindingModel.challenge_id == challenge_id).all()
        evidence = db.query(EvidenceModel).filter(EvidenceModel.challenge_id == challenge_id).all()

        experience = (db.query(ExperienceModel)
                      .filter(ExperienceModel.source_challenge_id == challenge_id)
                      .order_by(ExperienceModel.created_at.desc())
                      .first())

        mission_plan = challenge.mission_plan if isinstance(challenge.mission_plan, dict) else {}
        blackboard = mission_plan.get("blackboard_state") or {}
        headers = blackboard.get("extracted_headers") or {}

        flag = challenge.flag or ""
        flag_step: Optional[ToolExecutionModel] = None
        if flag:
            for e in execs:
                if flag in (e.stdout or "") or flag in (e.stderr or ""):
                    flag_step = e
                    break
        # If not in ToolExecutionModel, try the blackboard execution history.
        flag_cmd_from_history = ""
        if not flag_step and flag:
            for h in (blackboard.get("execution_history") or []):
                if flag in (h.get("output") or ""):
                    flag_cmd_from_history = h.get("command", "")
                    break

        return {
            "challenge": challenge,
            "target_str": target_str,
            "runs": runs,
            "execs": execs,
            "findings": findings,
            "evidence": evidence,
            "experience": experience,
            "blackboard": blackboard,
            "headers": headers,
            "flag": flag,
            "flag_step": flag_step,
            "flag_cmd_from_history": flag_cmd_from_history,
        }

    # ------------------------------------------------------------------ #
    # LLM prompt construction
    # ------------------------------------------------------------------ #

    def _pick_execs(self, execs: List[ToolExecutionModel], flag_step) -> List[ToolExecutionModel]:
        picked = list(execs[:_MAX_EXEC_IN_PROMPT])
        if flag_step is not None and all(p.id != flag_step.id for p in picked):
            picked.append(flag_step)  # always include the flag-yielding step
        return picked

    def _fmt_execs_for_prompt(self, execs, flag, flag_step) -> str:
        if not execs:
            return "(no tool executions were recorded for this run)"
        lines = []
        for i, e in enumerate(self._pick_execs(execs, flag_step), 1):
            body = (e.stdout or "").strip() or (e.stderr or "").strip() or "(no output captured)"
            if len(body) > _OUTPUT_TRIM_PROMPT:
                body = body[:_OUTPUT_TRIM_PROMPT] + f"…[truncated {len(body) - _OUTPUT_TRIM_PROMPT} chars]"
            mark = "  <-- FLAG APPEARS IN THIS OUTPUT" if (
                flag and (flag in (e.stdout or "") or flag in (e.stderr or ""))) else ""
            lines.append(
                f"[{i}] agent={e.agent} status={e.status} exit={e.exit_code} ({int(e.duration_ms or 0)}ms){mark}\n"
                f"    $ {e.command}\n"
                f"    output: {body}"
            )
        extra = len(execs) - min(len(execs), _MAX_EXEC_IN_PROMPT)
        if extra > 0:
            lines.append(f"(+{extra} more executions omitted for brevity)")
        return "\n".join(lines)

    def build_prompt(self, ctx: Dict[str, Any]) -> Tuple[str, str]:
        ch = ctx["challenge"]
        bb = ctx["blackboard"]
        exp = ctx["experience"]
        headers = ctx["headers"] or {}
        endpoints = bb.get("discovered_endpoints") or []
        cookies = bb.get("observed_cookies") or {}
        secrets = bb.get("deobfuscated_secrets") or []
        usernames = bb.get("candidate_usernames") or []

        system = (
            "You are FORGE's senior red-team report author. Write a DETAILED, TECHNICAL CTF writeup "
            "in GitHub-flavored Markdown for the operator's own solved challenge.\n\n"
            "STRICT RULES:\n"
            "- Use ONLY the telemetry provided by the user below. Do NOT invent commands, endpoints, "
            "HTTP responses, credentials, or flags. If a section lacks supporting data, say so in one line.\n"
            "- Reproduce the real commands and the real flag EXACTLY as given.\n"
            "- Be concrete: explain WHY each step worked, cite the actual tool output as evidence, and "
            "walk the exploit reasoning end to end. No generic filler, no vague 'the framework analyzed…'.\n"
            "- Use fenced code blocks for commands (```bash) and tool output (```text).\n\n"
            "REQUIRED SECTIONS (use exactly these headings, in order):\n"
            "# CTF Writeup: <name> (<CATEGORY>)\n"
            "## Challenge Information\n"
            "## Executive Summary\n"
            "## 1. Reconnaissance & Enumeration\n"
            "## 2. Vulnerability Analysis\n"
            "## 3. Exploitation — Attack Chain\n"
            "## 4. Evidence & Decoded Artifacts\n"
            "## 5. Flag Extraction & Verification\n"
            "## 6. Detection & Remediation (Blue Team)\n"
            "## 7. Reusable Methodology\n"
            "## Timeline\n"
        )

        p: List[str] = ["## CHALLENGE",
                        f"Name: {ch.name}",
                        f"Platform: {getattr(ch, 'platform_name', '') or 'FORGE CTF Framework'}",
                        f"Category: {ch.category}",
                        f"Difficulty: {ch.difficulty}",
                        f"Target(s): {ctx['target_str']}",
                        f"Working directory: {ch.working_directory or '(unset)'}",
                        f"Status: {ch.status}",
                        f"Flag: {ctx['flag'] or '(not captured)'}"]
        if ch.description:
            p.append(f"Description: {ch.description}")

        p.append("\n## RECON / BLACKBOARD STATE (observed, real)")
        p.append(f"Discovered endpoints: {', '.join(endpoints) if endpoints else '(none recorded)'}")
        if headers:
            p.append("Response headers:\n" + "\n".join(f"  {k}: {v}" for k, v in list(headers.items())[:20]))
        if cookies:
            p.append("Cookies: " + "; ".join(f"{k}={v}" for k, v in list(cookies.items())[:10]))
        if secrets:
            p.append("Decoded secrets/artifacts: " + "; ".join(str(s)[:200] for s in secrets[:10]))
        if usernames:
            p.append("Candidate usernames: " + ", ".join(map(str, usernames[:10])))

        if ctx["findings"]:
            p.append("\n## FINDINGS (logged)")
            for f in ctx["findings"]:
                p.append(f"- [{f.severity}] {f.title} ({f.vulnerability_class or 'n/a'}) @ "
                         f"{f.endpoint or '-'}: {f.description}")

        if exp is not None:
            p.append("\n## DISTILLED TECHNIQUE (FORGE experience memory)")
            p.append(f"Technique: {exp.technique}")
            if exp.generalized_strategy:
                p.append(f"Strategy: {exp.generalized_strategy}")
            if exp.applicable_conditions:
                p.append(f"Applicable when: {exp.applicable_conditions}")
            if exp.detection_indicators:
                p.append("Detection/remediation knowledge (JSON): " + json.dumps(exp.detection_indicators)[:800])

        p.append("\n## TOOL EXECUTION TRACE (chronological ground truth)")
        p.append(self._fmt_execs_for_prompt(ctx["execs"], ctx["flag"], ctx["flag_step"]))

        if ctx["flag"]:
            vcmd = (ctx["flag_step"].command if ctx["flag_step"] else ctx["flag_cmd_from_history"]) \
                or "(command not identified in trace)"
            p.append(f"\n## FLAG\nValue: {ctx['flag']}\nProduced by command: {vcmd}")

        p.append("\nWrite the complete technical writeup now, following the required sections and rules.")
        return system, "\n".join(p)

    # ------------------------------------------------------------------ #
    # AI author (Gemini-first chain) with deterministic fallback
    # ------------------------------------------------------------------ #

    async def craft_writeup(self, db: Session, challenge_id: str) -> Tuple[str, str]:
        """Return (markdown, generated_by). Tries the LLM chain (Gemini first for the
        ``report_generation`` capability); on total exhaustion falls back to a
        deterministic writeup rendered from the same real telemetry."""
        ctx = self.gather_context(db, challenge_id)
        if ctx is None:
            return "", ""
        system, user = self.build_prompt(ctx)
        try:
            from backend.providers.router import model_router
            resp = await model_router.route_request(
                prompt=user,
                capability="report_generation",
                system_instruction=system,
                target_model="gemini-3.6-flash",
                max_tokens=4096,
            )
            if resp and not resp.is_refusal and (resp.content or "").strip():
                generated_by = f"{resp.provider_name}:{resp.model_name}"
                content = resp.content.strip() + (
                    f"\n\n---\n*Authored by FORGE via **{generated_by}** from real run telemetry.*\n")
                return content, generated_by
            logger.warning("[ReportGenerator] AI author unavailable (%s); deterministic fallback.",
                            getattr(resp, "refusal_reason", "no response"))
        except Exception as e:
            logger.warning("[ReportGenerator] AI author failed (%s); deterministic fallback.", e)
        return self.render_deterministic(ctx), "deterministic-fallback"

    # ------------------------------------------------------------------ #
    # Deterministic fallback — fully grounded in real telemetry
    # ------------------------------------------------------------------ #

    @staticmethod
    def _infer_tech(headers: Dict[str, str]) -> List[str]:
        blob = " ".join(f"{k}: {v}" for k, v in headers.items()).lower()
        out: List[str] = []
        for needle, label in _TECH_FP.items():
            if needle in blob and label not in out:
                out.append(label)
        return out

    def render_deterministic(self, ctx: Dict[str, Any]) -> str:
        ch = ctx["challenge"]
        bb = ctx["blackboard"]
        exp = ctx["experience"]
        flag = ctx["flag"]
        execs = ctx["execs"]
        headers = ctx["headers"] or {}
        endpoints = bb.get("discovered_endpoints") or []
        cookies = bb.get("observed_cookies") or {}
        secrets = bb.get("deobfuscated_secrets") or []

        md: List[str] = [f"# CTF Writeup: {ch.name} ({(ch.category or '').upper()})\n"]

        md.append("## Challenge Information")
        md.append(f"- **Platform / Competition**: {getattr(ch, 'platform_name', '') or 'FORGE CTF Framework'}")
        md.append(f"- **Category**: {ch.category}")
        md.append(f"- **Difficulty**: {ch.difficulty}")
        md.append(f"- **Target(s)**: `{ctx['target_str']}`")
        md.append(f"- **Working Directory**: `{ch.working_directory or '(unset)'}`")
        md.append(f"- **Status**: {ch.status}")
        md.append(f"- **Flag**: `{flag or 'Not captured'}`")
        if getattr(ch, "duration_seconds", 0):
            md.append(f"- **Duration**: {ch.duration_seconds}s")

        md.append("\n## Executive Summary")
        summ: List[str] = []
        if exp and exp.technique:
            summ.append(f"The challenge was solved via **{exp.technique}**.")
        summ.append(f"{len(execs)} tool execution(s) were recorded"
                    + (f" against `{ctx['target_str']}`." if ctx["target_str"] != "Unknown Target" else "."))
        if flag:
            summ.append(f"The flag `{flag}` was recovered from live tool output (verified below).")
        md.append(" ".join(summ) if summ else "*No run telemetry recorded yet.*")

        md.append("\n## 1. Reconnaissance & Enumeration")
        recon_written = False
        techs = self._infer_tech(headers)
        if techs:
            md.append(f"- **Stack fingerprinted**: {', '.join(techs)}"); recon_written = True
        if endpoints:
            md.append(f"- **Endpoints discovered ({len(endpoints)})**: "
                      + ", ".join(f"`{e}`" for e in endpoints[:30])); recon_written = True
        if headers:
            md.append("- **Notable response headers**:")
            md.append("```http")
            md += [f"{k}: {v}" for k, v in list(headers.items())[:20]]
            md.append("```"); recon_written = True
        if cookies:
            md.append("- **Cookies observed**: " + "; ".join(f"`{k}`" for k in list(cookies.keys())[:10]))
            recon_written = True
        if not recon_written:
            md.append("*No reconnaissance telemetry was recorded for this run.*")

        md.append("\n## 2. Vulnerability Analysis")
        if ctx["findings"]:
            for f in ctx["findings"]:
                md.append(f"- **[{f.severity}] {f.title}** — {f.vulnerability_class or 'n/a'} @ `{f.endpoint or '-'}`")
                if f.description:
                    md.append(f"  - {f.description}")
        elif exp:
            md.append(f"- **Identified technique**: {exp.technique}")
            if exp.applicable_conditions:
                md.append(f"- **Applicable conditions**: {exp.applicable_conditions}")
        else:
            md.append("*No structured findings were logged; the exploited weakness is evidenced by the attack chain below.*")

        md.append("\n## 3. Exploitation — Attack Chain")
        if execs:
            for i, e in enumerate(execs, 1):
                is_flag = flag and (flag in (e.stdout or "") or flag in (e.stderr or ""))
                if is_flag:
                    tag = " 🏁 **(flag-yielding step)**"
                elif (e.status or "").upper() in ("FAILED", "TIMEOUT"):
                    tag = " ⚠️ _(failed / blocked — kept for context)_"
                else:
                    tag = ""
                md.append(f"\n**Step {i}** — `{e.agent}` · status `{e.status}` · exit `{e.exit_code}` · {int(e.duration_ms or 0)}ms{tag}")
                md.append("```bash")
                md.append(e.command or "(no command recorded)")
                md.append("```")
                body = (e.stdout or "").strip() or (e.stderr or "").strip()
                if body:
                    if len(body) > _OUTPUT_TRIM_MD:
                        body = body[:_OUTPUT_TRIM_MD] + f"\n…[truncated {len(body) - _OUTPUT_TRIM_MD} chars]"
                    md.append("```text")
                    md.append(body)
                    md.append("```")
        else:
            hist = bb.get("execution_history") or []
            if hist:
                for i, h in enumerate(hist, 1):
                    md.append(f"\n**Step {i}** — `{h.get('agent', '')}` {h.get('note', '')}".rstrip())
                    md.append("```bash"); md.append(h.get("command", "")); md.append("```")
                    if h.get("output"):
                        md.append("```text"); md.append(str(h["output"])[:_OUTPUT_TRIM_MD]); md.append("```")
            else:
                md.append("*No tool executions were recorded for this run.*")

        md.append("\n## 4. Evidence & Decoded Artifacts")
        ev_written = False
        if secrets:
            md.append("- **Decoded secrets / artifacts**:")
            md += [f"  - `{str(s)[:200]}`" for s in secrets[:10]]; ev_written = True
        for ev in ctx["evidence"]:
            md.append(f"- **[{ev.agent}] {ev.evidence_type}** — source `{ev.source}`, "
                      f"confidence {int((ev.confidence or 0) * 100)}%")
            md.append("```text"); md.append((ev.content or "")[:_OUTPUT_TRIM_MD]); md.append("```")
            ev_written = True
        if not ev_written:
            md.append("*No separate evidence artifacts were persisted; the command trace above is the primary evidence.*")

        md.append("\n## 5. Flag Extraction & Verification")
        if flag:
            vcmd = ctx["flag_step"].command if ctx["flag_step"] else ctx["flag_cmd_from_history"]
            md.append(f"- **Flag**: `{flag}`")
            if vcmd:
                md.append("- **Verifying command** (the flag appeared in this command's real output):")
                md.append("```bash"); md.append(vcmd); md.append("```")
            m = _FLAG_RE.search(flag)
            md.append(f"- **Matched CTF flag pattern**: `{m.group(0) if m else flag}`")
        else:
            md.append("*Flag not captured for this challenge.*")

        md.append("\n## 6. Detection & Remediation (Blue Team)")
        di = (exp.detection_indicators if exp else None) or {}
        if any(di.get(k) for k in ("indicators", "logs", "investigation", "containment")):
            for label, key in (("Indicators", "indicators"), ("Log sources", "logs"),
                               ("Investigation", "investigation"), ("Containment / Remediation", "containment")):
                vals = di.get(key) or []
                if vals:
                    md.append(f"- **{label}**:")
                    md += [f"  - {v}" for v in vals]
        else:
            md.append("*No blue-team indicators have been derived for this technique yet.*")

        md.append("\n## 7. Reusable Methodology")
        if exp and exp.generalized_strategy:
            md.append(exp.generalized_strategy)
            md.append(f"\n_Stored in FORGE experience memory as `{exp.id}` "
                      f"(provenance: run `{exp.source_run_id}`)._")
        else:
            md.append("*No generalized strategy has been distilled for this solve yet.*")

        md.append("\n## Timeline")
        if execs:
            md.append(f"- **First recorded action**: {execs[0].created_at}")
            md.append(f"- **Last recorded action**: {execs[-1].created_at}")
        if getattr(ch, "started_at", None):
            md.append(f"- **Started**: {ch.started_at}")
        if getattr(ch, "completed_at", None):
            md.append(f"- **Completed**: {ch.completed_at}")
        if not execs and not getattr(ch, "started_at", None):
            md.append("*No timestamps recorded.*")

        md.append("\n---\n*Generated deterministically by FORGE from real run telemetry (AI author unavailable).*")
        return "\n".join(md)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def save_writeup(self, db: Session, challenge_id: str, content: str,
                     output_dir: Optional[str] = None) -> str:
        """Write the writeup markdown into the challenge working folder (or `output_dir`
        when explicitly given) and record a ReportModel. Returns the saved path."""
        challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
        if not challenge:
            return ""
        if output_dir:
            target_dir = os.path.abspath(output_dir)
        else:
            # Safe path resolution — never the project root/home (see backend.utils.workspace).
            target_dir = resolve_safe_working_dir(
                challenge.working_directory, challenge_id, challenge.category or "", challenge.name or "")
        os.makedirs(target_dir, exist_ok=True)

        safe_name = "".join(c if (c.isalnum() or c in (" ", "-", "_")) else "_"
                            for c in (challenge.name or "challenge")).strip().replace(" ", "_") or "challenge"
        report_path = os.path.join(target_dir, f"WRITEUP_{safe_name}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content or "")

        try:
            db.add(ReportModel(challenge_id=challenge_id,
                               title=f"WRITEUP_{safe_name}.md", file_path=report_path))
            db.commit()
        except Exception as e:
            logger.debug("[ReportGenerator] ReportModel persist skip: %s", e)
            db.rollback()
        return report_path

    # Back-compat: synchronous, deterministic README under reports/ (legacy callers).
    def generate_readme(self, db: Session, challenge_id: str, output_dir: str = "reports") -> str:
        ctx = self.gather_context(db, challenge_id)
        if ctx is None:
            return ""
        return self.save_writeup(db, challenge_id, self.render_deterministic(ctx), output_dir=output_dir)


report_generator = ReportGenerator()
