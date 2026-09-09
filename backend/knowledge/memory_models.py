"""
FORGE Experience/Memory data models + deterministic generalization.

This module is the CONTRACT layer for the experience memory system:

* ``AttemptRecord`` / ``ExperienceRecord`` — Pydantic DTOs describing a distilled,
  reusable FORGE experience (the shape stored in ``ExperienceModel``).
* ``RetrievedMemory`` — the unified retrieval result returned to the swarm,
  covering BOTH FORGE experiences and Playbook Vault entries.
* ``Generalizer`` — the deterministic sanitizer that enforces rule §4: a solved
  challenge must NEVER be stored as challenge-specific knowledge. Concrete
  targets, flags, credentials, tokens and secrets are stripped/parameterized so
  what remains is a *technique*, not a *solution*.

Everything here is deterministic (no LLM calls) so extraction stays fast enough
for a live competition (rule §17).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Source provenance weighting (§7). FORGE-verified experience outranks unproven
# external knowledge; repeated FORGE success outranks everything.
# ---------------------------------------------------------------------------

SOURCE_WEIGHTS: Dict[str, float] = {
    "forge_repeated_success": 1.6,   # VERY HIGH — proven across multiple runs
    "forge_success": 1.3,            # HIGH — FORGE solved it once, verified
    "external_writeup": 1.0,         # MEDIUM — imported but unverified here
    "forge_attempt": 0.7,            # LOW/MEDIUM — attempted, not yet verified
    "forge_failure": 0.55,           # still useful ("what NOT to repeat")
}

# Flags in any of the known CTF prefixes — used to scrub captured flags from
# stored experience. Mirrors swarm_orchestrator.FLAG_REGEX intent but local so
# this module has no import cycle with the swarm.
_FLAG_RE = re.compile(
    r"\b(?:picoCTF|FLAG|flag|HTB|CTF|THM|pico|CSAW|ctf)\{[^}\n]{0,200}\}",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_B64_RE = re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b")


# ---------------------------------------------------------------------------
# Deterministic generalizer (§4)
# ---------------------------------------------------------------------------

class Generalizer:
    """Strips challenge-specific secrets from text, leaving a reusable technique.

    Order matters: caller-supplied concrete secrets (target tokens, the flag,
    discovered credentials/tokens) are replaced FIRST with named sentinel tokens,
    then generic structural patterns (URLs, IPs, JWTs, hex/base64 blobs) are
    parameterized. Known exploit keywords (UNION SELECT, {{7*7}}, etc.) are left
    intact so the technique stays legible.
    """

    @staticmethod
    def _sub_literal(text: str, needle: str, sentinel: str) -> str:
        needle = (needle or "").strip()
        if not needle or len(needle) < 3:
            return text
        return re.sub(re.escape(needle), sentinel, text, flags=re.IGNORECASE)

    @classmethod
    def generalize(
        cls,
        text: str,
        *,
        target_tokens: Optional[List[str]] = None,
        flag: str = "",
        secrets: Optional[List[str]] = None,
        usernames: Optional[List[str]] = None,
    ) -> str:
        """Return a generalized copy of *text* with concrete artifacts removed."""
        if not text:
            return ""
        out = str(text)

        # 1) Named, challenge-specific values first (most specific).
        for tok in (target_tokens or []):
            tok = (tok or "").strip()
            if not tok:
                continue
            # A bare host:port / path / IP / URL target token.
            if tok.startswith("http"):
                out = cls._sub_literal(out, tok, "{TARGET_URL}")
            elif "/" in tok or "\\" in tok:
                out = cls._sub_literal(out, tok, "{ARTIFACT_PATH}")
            else:
                out = cls._sub_literal(out, tok, "{TARGET}")

        if flag:
            out = cls._sub_literal(out, flag, "{FLAG}")
        for s in (secrets or []):
            out = cls._sub_literal(out, s, "{SECRET}")
        for u in (usernames or []):
            out = cls._sub_literal(out, u, "{USER}")

        # 2) Generic structural patterns (order: most-specific token classes first).
        out = _FLAG_RE.sub("{FLAG}", out)
        out = _JWT_RE.sub("{JWT}", out)
        out = _URL_RE.sub("{TARGET_URL}", out)
        out = _IPV4_RE.sub("{HOST}", out)
        # host:port left over from a scrubbed URL (":{PORT}") — normalize ports.
        out = re.sub(r":(\d{2,5})\b", ":{PORT}", out)
        out = _LONG_HEX_RE.sub("{HEX}", out)
        out = _B64_RE.sub("{B64}", out)
        return out

    @classmethod
    def contains_secret(
        cls,
        text: str,
        *,
        target_tokens: Optional[List[str]] = None,
        flag: str = "",
        secrets: Optional[List[str]] = None,
    ) -> bool:
        """True if any concrete secret still appears — used by tests/guards (§4, §21)."""
        if not text:
            return False
        low = text.lower()
        if flag and flag.lower() in low:
            return True
        for tok in (target_tokens or []):
            tok = (tok or "").strip()
            if tok and len(tok) >= 4 and tok.lower() in low:
                return True
        for s in (secrets or []):
            if s and len(s) >= 4 and s.lower() in low:
                return True
        if _FLAG_RE.search(text) or _JWT_RE.search(text):
            return True
        if _URL_RE.search(text) or _IPV4_RE.search(text):
            return True
        return False


# ---------------------------------------------------------------------------
# Technique classification + blue-team indicators (deterministic heuristics)
# ---------------------------------------------------------------------------

# (matched_substrings, technique_label, tags, signatures, success_indicators)
_TECHNIQUE_RULES = [
    (("ssti", "jinja", "{{7*7}}", "{{7*7", "render_template_string", "twig", "freemarker"),
     "Server-Side Template Injection (SSTI)",
     ["ssti", "template_injection"],
     ["Jinja2", "Werkzeug", "render_template_string", "Twig"],
     [r"uid=\d+", r"49$", r"root:x:0:0"]),
    (("union select", "' or 1=1", "or 1=1--", "sqlmap", "information_schema", "' or '1'='1"),
     "SQL Injection",
     ["sqli", "sql_injection"],
     ["SQL syntax", "UNION SELECT", "information_schema", "sqlite3.OperationalError"],
     [r"(?:SQL syntax|mysql_fetch|sqlite3\.OperationalError|PG::)", r"\|\s*flag"]),
    (("../", "..%2f", "/etc/passwd", "?file=", "&file=", "?page=", "lfi", "php://filter", "php://"),
     "Local File Inclusion / Path Traversal",
     ["lfi", "path_traversal"],
     ["root:x:0:0", "php://filter", "../../"],
     [r"root:x:0:0", r"daemon:x:"]),
    (("multipart/form-data", "upload", ".php5", ".phtml", "content-type: image", "filename="),
     "File Upload Validation Bypass",
     ["upload", "file_upload_bypass"],
     ["multipart/form-data", "Content-Disposition", "uploads/"],
     [r"(?:uploaded|Upload successful)", r"\.ph(?:p|tml)"]),
    (("jwt", "eyj", "alg\":\"none", "rs256", "hs256", "bearer "),
     "JWT Forgery / Weak Signature",
     ["jwt", "token_forgery"],
     ["eyJ", "alg", "none algorithm", "HS256"],
     [r"(?:HTTP/1\.[01] 200|admin|role)"]),
    (("pwntools", "p32(", "p64(", "rop", "ret2", "checksec", "gdb", "pattern_offset"),
     "Binary Exploitation / Memory Corruption",
     ["pwn", "buffer_overflow", "rop"],
     ["ELF", "checksec", "ROP", "GOT"],
     [r"(?:\[\+\] Opening connection|Switching to interactive)"]),
    (("<script>", "onerror=", "xss", "document.cookie", "alert("),
     "Cross-Site Scripting (XSS)",
     ["xss"],
     ["<script>", "reflected", "innerHTML"],
     [r"(?:document\.cookie|alert\()"]),
    (("hydra", "brute", "wordlist", "rockyou", "password spray", "credential"),
     "Credential Brute-Force / Weak Auth",
     ["auth", "bruteforce"],
     ["401 Unauthorized", "Login", "Invalid password"],
     [r"(?:200 OK|Welcome|Dashboard)"]),
    (("xxe", "<!doctype", "<!entity", "system \"file"),
     "XML External Entity (XXE)",
     ["xxe"],
     ["<!ENTITY", "SYSTEM", "DOCTYPE"],
     [r"root:x:0:0"]),
    (("nc ", "reverse shell", "bash -i", "/dev/tcp/", "msfvenom", "rev shell"),
     "Command Injection / Reverse Shell",
     ["rce", "command_injection"],
     ["; id", "$(", "`id`", "/dev/tcp"],
     [r"uid=\d+\(", r"gid=\d+"]),
    (("xor", "aes", "rsa", "frequency analysis", "vigenere", "base64 -d", "openssl enc", "rot13"),
     "Cryptographic Weakness Exploitation",
     ["crypto"],
     ["ciphertext", "key", "IV", "mode"],
     [r"\{FLAG\}"]),
]


def classify_technique(evidence_text: str, category: str = "") -> Dict[str, Any]:
    """Deterministically map observed evidence to a generalized technique + tags.

    Falls back to a category-scoped generic label when nothing specific matches.
    """
    low = (evidence_text or "").lower()
    for needles, label, tags, sigs, indicators in _TECHNIQUE_RULES:
        if any(n in low for n in needles):
            return {
                "technique": label,
                "tags": list(dict.fromkeys(tags + ([category.lower()] if category else []))),
                "signatures": sigs,
                "success_indicators": indicators,
            }
    cat = (category or "general").lower()
    return {
        "technique": f"{cat.upper()} solve methodology",
        "tags": [cat, "custom_exploit"],
        "signatures": [],
        "success_indicators": [],
    }


# Blue-team knowledge derived from a red-team technique (§11). Deterministic —
# no SIEM, just reusable defensive pointers keyed by tag.
_DETECTION_BY_TAG: Dict[str, Dict[str, List[str]]] = {
    "ssti": {
        "indicators": ["Template engine errors in application logs", "Unusual arithmetic/format tokens in request params ({{ }}, ${ })"],
        "logs": ["web application logs", "WAF logs"],
        "investigation": ["Grep request params for template delimiters", "Review template rendering call sites for user input"],
        "containment": ["Sandbox the template engine", "Reject template metacharacters in input"],
    },
    "sqli": {
        "indicators": ["SQL syntax errors returned to client", "Spikes in UNION/OR-based query strings", "Abnormal DB error rates"],
        "logs": ["web access logs", "database query logs"],
        "investigation": ["Correlate 500s with query params", "Search access logs for UNION SELECT / information_schema"],
        "containment": ["Parameterize queries", "Deploy WAF rule for SQLi signatures"],
    },
    "lfi": {
        "indicators": ["Requests containing ../ or /etc/passwd", "Access to unexpected local file paths"],
        "logs": ["web access logs"],
        "investigation": ["Search access logs for traversal sequences", "Audit file-inclusion endpoints"],
        "containment": ["Whitelist include paths", "Disable url_include / restrict open_basedir"],
    },
    "upload": {
        "indicators": ["Executable/script files in upload directories", "Requests to uploaded files with script extensions"],
        "logs": ["web access logs", "file integrity monitoring"],
        "investigation": ["List recently written files in upload dirs", "Check content-type vs extension mismatches"],
        "containment": ["Enforce server-side content validation", "Store uploads outside webroot, disable execution"],
    },
    "jwt": {
        "indicators": ["Tokens with alg=none or weak HS256 secrets", "Privilege changes without re-auth"],
        "logs": ["authentication logs"],
        "investigation": ["Decode presented JWTs, inspect alg/claims", "Verify signature validation is enforced"],
        "containment": ["Reject alg=none", "Rotate signing keys, enforce strong secrets"],
    },
    "pwn": {
        "indicators": ["Service crashes / restarts", "Anomalous outbound connections from a service"],
        "logs": ["process telemetry", "core dumps", "network telemetry"],
        "investigation": ["Inspect crash logs / core dumps", "Review recent binary/service exploitation CVEs"],
        "containment": ["Enable ASLR/NX/stack canaries", "Restart with hardened flags, patch binary"],
    },
    "rce": {
        "indicators": ["Unexpected child processes from web/service user", "Reverse-shell connections (/dev/tcp, nc)"],
        "logs": ["process telemetry", "network telemetry", "web access logs"],
        "investigation": ["Trace process tree for spawned shells", "Correlate outbound connections with request logs"],
        "containment": ["Kill spawned processes", "Sanitize command inputs, restrict egress"],
    },
    "xss": {
        "indicators": ["Script payloads in stored fields", "Anomalous outbound requests carrying cookies"],
        "logs": ["web access logs"],
        "investigation": ["Search stored fields for <script>/onerror", "Review CSP violation reports"],
        "containment": ["Output-encode user content", "Deploy a strict Content-Security-Policy"],
    },
    "auth": {
        "indicators": ["High volume of failed logins", "Successful login after many failures"],
        "logs": ["authentication logs"],
        "investigation": ["Review auth logs for brute-force bursts", "Check for credential stuffing patterns"],
        "containment": ["Rate-limit / lockout", "Enforce MFA and strong passwords"],
    },
}


def derive_detection_indicators(tags: List[str]) -> Dict[str, List[str]]:
    """Merge blue-team detection knowledge for all matched technique tags (§11)."""
    merged: Dict[str, List[str]] = {"indicators": [], "logs": [], "investigation": [], "containment": []}
    for tag in tags or []:
        block = _DETECTION_BY_TAG.get(tag.lower())
        if not block:
            continue
        for key, vals in block.items():
            for v in vals:
                if v not in merged[key]:
                    merged[key].append(v)
    return merged


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class AttemptRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    sequence: int = 0
    approach: str = ""
    technique: str = ""
    outcome: str = "failure"           # success | failure
    reason: str = ""
    evidence: str = ""


class ExperienceRecord(BaseModel):
    """Distilled, generalized representation of a completed FORGE run (§2)."""
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    source: str = "forge_run"
    source_run_id: Optional[str] = None
    source_challenge_id: Optional[str] = None
    challenge_name: str = ""

    category: str = "web"
    difficulty: str = "MEDIUM"
    technique: str = ""
    tags: List[str] = Field(default_factory=list)

    target_characteristics: Dict[str, Any] = Field(default_factory=dict)
    initial_observations: str = ""
    observed_conditions: str = ""
    applicable_conditions: str = ""
    discovered_endpoints: List[str] = Field(default_factory=list)
    technologies: List[str] = Field(default_factory=list)
    vulnerabilities: List[str] = Field(default_factory=list)

    successful_techniques: List[str] = Field(default_factory=list)
    failed_techniques: List[Dict[str, str]] = Field(default_factory=list)
    commands_used: List[str] = Field(default_factory=list)
    important_tool_outputs: List[str] = Field(default_factory=list)
    successful_attack_chain: List[str] = Field(default_factory=list)
    verification_evidence: str = ""
    success_indicators: List[str] = Field(default_factory=list)
    prerequisites: List[str] = Field(default_factory=list)
    generalized_strategy: str = ""

    detection_indicators: Dict[str, Any] = Field(default_factory=dict)

    outcome: str = "success"
    confidence: float = 0.6

    attempts: List[AttemptRecord] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RetrievedMemory(BaseModel):
    """Unified retrieval result covering FORGE experiences AND Playbook Vault entries (§10)."""
    model_config = ConfigDict(populate_by_name=True)

    kind: str                      # "experience" | "playbook"
    id: str
    technique: str = ""
    category: str = ""
    applicable_conditions: str = ""
    strategy: str = ""
    outcome: str = "success"       # success | failure | reference
    success_indicators: List[str] = Field(default_factory=list)
    failed_approaches: List[str] = Field(default_factory=list)
    confidence: float = 0.6
    success_rate: float = 1.0
    source: str = "forge_run"
    provenance: Dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
