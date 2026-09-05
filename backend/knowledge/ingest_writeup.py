"""
Writeup Ingestion Engine for FORGE Playbook Vault
==================================================
Converts raw CTF writeups (markdown / text files) into structured FORGE Playbooks (.yaml).
Indexes them immediately into the Playbook Vault FTS search index.

Usage:
  py -m backend.knowledge.ingest_writeup path/to/writeup.md --category web --title "SSTI via Jinja"
"""

import sys
import os
import re
import yaml
import argparse
import logging
from typing import List, Dict, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.knowledge.playbook_vault import playbook_vault, PlaybookSchema, CATEGORIES

logger = logging.getLogger("forge.ingest_writeup")
logging.basicConfig(level=logging.INFO, format="[IngestWriteup] %(message)s")


def parse_writeup(file_path: str, category: str = "web", title: Optional[str] = None) -> PlaybookSchema:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # Extract title if not provided
    if not title:
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip()
        else:
            title = os.path.splitext(os.path.basename(file_path))[0]

    # Generate clean ID
    clean_id = f"{category}-{re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')}"

    # Extract code blocks
    code_blocks = re.findall(r"```(?:python|bash|sh|c|cpp|sql|html)?\n(.*?)```", content, re.DOTALL)
    exploit_template = ""
    if code_blocks:
        # Pick the largest code block as the exploit template
        exploit_template = max(code_blocks, key=len).strip()

    # Parameterize target URLs and ports if present
    exploit_template = re.sub(r"https?://[a-zA-Z0-9\.\-]+(?::\d+)?", "{TARGET_URL}", exploit_template)
    exploit_template = re.sub(r"(?:picoCTF|FLAG|CTF|HTB|THM)\{[^\}\s]+\}", "{FLAG}", exploit_template, flags=re.IGNORECASE)

    # Extract trigger signatures and keywords
    signatures = []
    tags = [category.lower(), "writeup"]

    keyword_map = {
        "sql injection": "sqli",
        "sqli": "sqli",
        "command injection": "rce",
        "remote code execution": "rce",
        "server-side template injection": "ssti",
        "ssti": "ssti",
        "jinja": "jinja2",
        "buffer overflow": "pwn",
        "bof": "pwn",
        "format string": "fmtstr",
        "heap": "heap",
        "xss": "xss",
        "csrf": "csrf",
        "lfi": "lfi",
        "rfi": "rfi",
        "jwt": "jwt",
        "deserialization": "deserialization",
        "pickle": "pickle",
        "pwntools": "pwntools",
        "sqlmap": "sqlmap",
        "nmap": "nmap",
        "gdb": "gdb"
    }

    content_lower = content.lower()
    for phrase, tag in keyword_map.items():
        if phrase in content_lower:
            tags.append(tag)

    # Dedup tags
    tags = list(dict.fromkeys(tags))

    # Extract headers as notes/signatures
    headers = re.findall(r"^#{1,3}\s+(.+)$", content, re.MULTILINE)
    signatures.extend(headers[:3])

    playbook = PlaybookSchema(
        id=clean_id,
        category=category.lower() if category.lower() in CATEGORIES else "web",
        tags=tags,
        trigger_signatures=signatures,
        notes=content[:500].strip(),
        exploit_template=exploit_template or content[:1000],
        source="human",
        confidence_score=1.0,
        times_used=1,
        success_rate=1.0,
        is_promoted=True
    )

    return playbook


def ingest_file(file_path: str, category: str = "web", title: Optional[str] = None) -> str:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Writeup file not found: {file_path}")

    playbook = parse_writeup(file_path, category, title)
    saved_path = playbook_vault.save_playbook(playbook)
    logger.info(f"Successfully ingested writeup '{file_path}' -> Playbook `{playbook.id}` at {saved_path}")
    return saved_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest raw CTF writeups into FORGE Playbook Vault")
    parser.add_argument("file", help="Path to raw markdown/text writeup file")
    parser.add_argument("--category", "-c", default="web", choices=list(CATEGORIES.keys()), help="Challenge category")
    parser.add_argument("--title", "-t", default=None, help="Custom title for the playbook")

    args = parser.parse_args()
    ingest_file(args.file, args.category, args.title)
