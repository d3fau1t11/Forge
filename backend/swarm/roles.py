"""Specialist agent roles for the FORGE coordinated swarm (Phase 4).

A role is a *specialization*, expressed as data rather than behavior: an objective
template, a capability list, and the challenge categories / evidence keywords that
should activate it. The supervisor uses :data:`ROLE_PROFILES` to decide — purely
deterministically, so it works in tests with no LLM — which specialists to spawn
for a challenge and which follow-up tasks a new piece of evidence justifies.

Every specialist runs the SAME :class:`~backend.agent_runtime.runtime.AgentRuntime`;
the role only shapes the agent's objective, the context it is seeded with, and
which evidence it consumes. Nothing here is OS-specific.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class AgentRole(str, Enum):
    """The specialist roles a mission can activate (plus the SUPERVISOR)."""

    RECON = "recon"
    WEB = "web"
    FORENSICS = "forensics"
    CRYPTO = "crypto"
    PWN = "pwn"
    REV = "rev"
    VERIFIER = "verifier"
    SUPERVISOR = "supervisor"

    @classmethod
    def specialists(cls) -> List["AgentRole"]:
        return [cls.RECON, cls.WEB, cls.FORENSICS, cls.CRYPTO, cls.PWN, cls.REV, cls.VERIFIER]

    @classmethod
    def from_value(cls, value: str) -> "AgentRole":
        try:
            return cls(str(value).strip().lower())
        except Exception:
            return cls.RECON


@dataclass(frozen=True)
class RoleProfile:
    """Static specialization data for one role."""

    role: AgentRole
    objective: str                      # objective template seeded into the agent
    capabilities: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)   # challenge categories this role owns
    keywords: List[str] = field(default_factory=list)      # evidence/description activators


ROLE_PROFILES: Dict[AgentRole, RoleProfile] = {
    AgentRole.RECON: RoleProfile(
        role=AgentRole.RECON,
        objective=(
            "Perform reconnaissance on the target: discover live hosts, open ports, "
            "running services and their versions, and the technology stack. Report "
            "every service and version you identify."
        ),
        capabilities=["host_discovery", "port_scan", "service_enumeration", "technology_fingerprint"],
        categories=["web", "pwn", "network", "misc", "recon"],
        keywords=["port", "service", "http", "ssh", "ftp", "smb", "open", "nmap"],
    ),
    AgentRole.WEB: RoleProfile(
        role=AgentRole.WEB,
        objective=(
            "Analyze the web application: inspect HTTP responses, enumerate directories "
            "and parameters, test authentication, and probe for web vulnerabilities "
            "(injection, IDOR, upload bypass, SSTI, etc.). Extract any flag."
        ),
        capabilities=["http_inspect", "directory_enum", "parameter_discovery", "auth_test", "web_exploit"],
        categories=["web"],
        keywords=["http", "https", "url", "apache", "nginx", "php", "cookie", "login",
                  "endpoint", "parameter", "sql", "xss", "upload", "web server"],
    ),
    AgentRole.FORENSICS: RoleProfile(
        role=AgentRole.FORENSICS,
        objective=(
            "Investigate the provided artifacts: inspect file types and metadata, run "
            "strings, carve archives, examine packet captures, and look for hidden or "
            "steganographic data. Extract any flag."
        ),
        capabilities=["file_inspect", "metadata", "strings", "archive_extract", "stego_analysis", "pcap_analysis"],
        categories=["forensics", "stego", "misc"],
        keywords=["pcap", "image", "png", "jpg", "zip", "metadata", "strings", "stego",
                  "hidden", "exif", "wireshark", "capture", "carve"],
    ),
    AgentRole.CRYPTO: RoleProfile(
        role=AgentRole.CRYPTO,
        objective=(
            "Solve the cryptographic challenge: identify encodings and ciphers, analyze "
            "RSA/hash constructions, and decrypt or crack as required. Decode "
            "deterministically and extract any flag."
        ),
        capabilities=["encoding", "classical_cipher", "rsa_analysis", "hash_analysis", "cipher_analysis"],
        categories=["crypto"],
        keywords=["rsa", "aes", "base64", "hex", "cipher", "encrypt", "decrypt", "hash",
                  "xor", "modulus", "rot13", "key", "encoded"],
    ),
    AgentRole.PWN: RoleProfile(
        role=AgentRole.PWN,
        objective=(
            "Exploit the binary: analyze protections (NX/PIE/canary/RELRO), reason about "
            "the vulnerability class (overflow/format-string/UAF), and develop an "
            "exploitation strategy. Extract any flag."
        ),
        capabilities=["binary_analysis", "protection_check", "exploit_reasoning"],
        categories=["pwn", "binary"],
        keywords=["binary", "elf", "overflow", "buffer", "canary", "nx", "pie", "libc",
                  "rop", "shellcode", "format string", "gdb"],
    ),
    AgentRole.REV: RoleProfile(
        role=AgentRole.REV,
        objective=(
            "Reverse engineer the target: perform static and dynamic analysis, extract "
            "strings and constants, decompile logic, and recover the algorithm or key "
            "that yields the flag."
        ),
        capabilities=["static_analysis", "strings", "decompile", "reverse_reasoning"],
        categories=["rev", "reversing", "reverse"],
        keywords=["disassemble", "decompile", "ghidra", "objdump", "reverse", "assembly",
                  "function", "algorithm", "license", "keygen"],
    ),
    AgentRole.VERIFIER: RoleProfile(
        role=AgentRole.VERIFIER,
        objective=(
            "Independently audit, evaluate, and verify answer candidates against the "
            "challenge question, semantic requirements, supporting evidence, and provenance. "
            "Confirm whether candidates conclusively answer the challenge."
        ),
        capabilities=["answer_verification", "candidate_evaluation", "evidence_audit"],
        categories=["web", "crypto", "forensics", "pwn", "rev", "misc", "recon"],
        keywords=["verify", "flag", "candidate", "answer", "solve", "confirm", "verdict"],
    ),
}



def profile(role: AgentRole) -> RoleProfile:
    return ROLE_PROFILES.get(role, ROLE_PROFILES[AgentRole.RECON])


def roles_for_category(category: str) -> List[AgentRole]:
    """Which specialists a challenge *category* activates. RECON is always included."""
    cat = (category or "").strip().lower()
    activated: List[AgentRole] = [AgentRole.RECON]
    for role in AgentRole.specialists():
        if role is AgentRole.RECON:
            continue
        if cat and cat in ROLE_PROFILES[role].categories:
            activated.append(role)
    # A bare/unknown category (e.g. "misc") gets recon + web as a safe default surface.
    if len(activated) == 1 and cat not in ("crypto", "pwn", "rev", "forensics"):
        activated.append(AgentRole.WEB)
    return activated


def roles_activated_by(text: str) -> List[AgentRole]:
    """Which specialists a free-text signal (evidence/description) mentions, by keyword."""
    low = (text or "").lower()
    hits: List[AgentRole] = []
    for role in AgentRole.specialists():
        prof = ROLE_PROFILES[role]
        if any(kw in low for kw in prof.keywords):
            hits.append(role)
    return hits


def primary_role_for(text: str) -> Optional[AgentRole]:
    hits = roles_activated_by(text)
    return hits[0] if hits else None
