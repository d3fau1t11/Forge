import os
import re
import yaml
import sqlite3
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

logger = logging.getLogger("forge.playbook_vault")


def _broadcast_knowledge_event(event_type: str, playbook_id: str, category: str, extra: Dict = None):
    """Fire-and-forget WebSocket broadcast for knowledge updates."""
    try:
        import asyncio
        from backend.websocket.manager import ws_manager
        payload = {
            "event": "KNOWLEDGE_UPDATED",
            "type": event_type,
            "playbook_id": playbook_id,
            "category": category,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if extra:
            payload.update(extra)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(ws_manager.broadcast(payload))
        except RuntimeError:
            pass  # No event loop running (CLI context) — skip WS broadcast
    except Exception:
        pass  # Non-critical — never break vault operations for WS

PLAYBOOKS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "playbooks"))
CATEGORIES = ["web", "pwn", "crypto", "reverse", "forensics", "osint", "auto_generated", "pending_review"]

class PlaybookSchema(BaseModel):
    id: str
    category: str
    tags: List[str] = Field(default_factory=list)
    trigger_signatures: List[str] = Field(default_factory=list)
    exploit_template: str
    notes: str = ""
    source: str = "ingested"  # "ingested" | "generated" | "curated"
    confidence_score: float = 1.0
    times_used: int = 0
    success_rate: float = 1.0
    is_promoted: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class PlaybookVault:
    """File-based Playbook Vault with SQLite FTS5 search index and self-learning synthesis flywheel."""

    CONFIDENCE_PROMOTION_THRESHOLD = 0.8
    REUSE_PROMOTION_THRESHOLD = 3

    def __init__(self, base_dir: str = PLAYBOOKS_DIR):
        self.base_dir = base_dir
        self._ensure_directories()
        self.db_conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._init_fts_index()
        self.reload_index()

    def _ensure_directories(self):
        """Create category subdirectories under playbooks base dir."""
        for cat in CATEGORIES:
            cat_dir = os.path.join(self.base_dir, cat)
            os.makedirs(cat_dir, exist_ok=True)

    def _init_fts_index(self):
        """Initialize in-memory SQLite FTS5 search table."""
        with self.db_conn:
            self.db_conn.execute("DROP TABLE IF EXISTS playbook_fts")
            self.db_conn.execute("""
                CREATE VIRTUAL TABLE playbook_fts USING fts5(
                    id UNINDEXED,
                    category,
                    tags,
                    trigger_signatures,
                    notes,
                    exploit_template,
                    source UNINDEXED,
                    confidence_score UNINDEXED,
                    times_used UNINDEXED,
                    is_searchable UNINDEXED
                )
            """)

    def _is_searchable(self, playbook: PlaybookSchema) -> bool:
        """Auto-generated playbooks must pass the confidence/reuse threshold to be indexed in default search."""
        if playbook.source != "generated":
            return True
        if playbook.is_promoted:
            return True
        if playbook.confidence_score >= self.CONFIDENCE_PROMOTION_THRESHOLD:
            return True
        if playbook.times_used >= self.REUSE_PROMOTION_THRESHOLD:
            return True
        return False

    def save_playbook(self, playbook: PlaybookSchema) -> str:
        """Save a playbook as a YAML file in the appropriate directory and update FTS index."""
        target_dir = os.path.join(self.base_dir, "auto_generated" if playbook.source == "generated" else playbook.category)
        os.makedirs(target_dir, exist_ok=True)
        
        file_path = os.path.join(target_dir, f"{playbook.id}.yaml")
        data = playbook.model_dump()
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

        self._index_single_playbook(playbook)
        logger.info(f"Saved playbook `{playbook.id}` in `{target_dir}` (searchable={self._is_searchable(playbook)})")
        _broadcast_knowledge_event("PLAYBOOK_SAVED", playbook.id, playbook.category)
        return file_path

    def _index_single_playbook(self, playbook: PlaybookSchema):
        """Add or replace a single playbook entry in the FTS index."""
        searchable = 1 if self._is_searchable(playbook) else 0
        with self.db_conn:
            self.db_conn.execute("DELETE FROM playbook_fts WHERE id = ?", (playbook.id,))
            self.db_conn.execute("""
                INSERT INTO playbook_fts(
                    id, category, tags, trigger_signatures, notes, exploit_template,
                    source, confidence_score, times_used, is_searchable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                playbook.id,
                playbook.category,
                " ".join(playbook.tags),
                " ".join(playbook.trigger_signatures),
                playbook.notes,
                playbook.exploit_template,
                playbook.source,
                playbook.confidence_score,
                playbook.times_used,
                searchable
            ))

    def load_playbook(self, playbook_id: str) -> Optional[PlaybookSchema]:
        """Load a playbook from disk by id."""
        for root, _, files in os.walk(self.base_dir):
            for file in files:
                if file == f"{playbook_id}.yaml" or file == f"{playbook_id}.yml":
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f)
                            return PlaybookSchema(**data)
                    except Exception as e:
                        logger.error(f"Error loading playbook {file_path}: {e}")
        return None

    def reload_index(self):
        """Scan all YAML files in playbooks directory and rebuild the FTS index."""
        self._init_fts_index()
        count = 0
        for root, _, files in os.walk(self.base_dir):
            for file in files:
                if file.endswith(".yaml") or file.endswith(".yml"):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f)
                            if isinstance(data, dict) and "id" in data and "exploit_template" in data:
                                pb = PlaybookSchema(**data)
                                self._index_single_playbook(pb)
                                count += 1
                    except Exception as e:
                        logger.warning(f"Skipping malformed playbook {file_path}: {e}")
        logger.info(f"PlaybookVault indexed {count} playbooks.")

    def search_playbooks(self, query: str, category: Optional[str] = None, top_k: int = 2, include_unpromoted: bool = False) -> List[PlaybookSchema]:
        """Search playbooks via SQLite FTS5 matching keywords/signatures in <50ms."""
        if not query or not query.strip():
            return []

        clean_terms = re.findall(r"[A-Za-z0-9_\-\.]+", query)
        if not clean_terms:
            return []
        
        fts_query = " OR ".join(clean_terms)
        searchable_clause = "" if include_unpromoted else "AND is_searchable = 1"
        category_clause = "AND category = ?" if category else ""

        params = [fts_query]
        if category:
            params.append(category)

        sql = f"""
            SELECT id, rank FROM playbook_fts
            WHERE playbook_fts MATCH ? {searchable_clause} {category_clause}
            ORDER BY rank
            LIMIT {top_k}
        """

        results: List[PlaybookSchema] = []
        try:
            cursor = self.db_conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            for row in rows:
                pb_id = row[0]
                pb = self.load_playbook(pb_id)
                if pb:
                    results.append(pb)
        except Exception as e:
            logger.error(f"FTS Search failed for query '{query}': {e}")
            results = self._fallback_search(clean_terms, category, top_k, include_unpromoted)

        return results

    def _fallback_search(self, terms: List[str], category: Optional[str], top_k: int, include_unpromoted: bool) -> List[PlaybookSchema]:
        """Fallback Python in-memory search if FTS query encounters syntax anomaly."""
        matches = []
        for root, _, files in os.walk(self.base_dir):
            for file in files:
                if file.endswith(".yaml") or file.endswith(".yml"):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f)
                            pb = PlaybookSchema(**data)
                            if category and pb.category != category:
                                continue
                            if not include_unpromoted and not self._is_searchable(pb):
                                continue
                            
                            corpus = f"{' '.join(pb.tags)} {' '.join(pb.trigger_signatures)} {pb.notes}".lower()
                            score = sum(1 for t in terms if t.lower() in corpus)
                            if score > 0:
                                matches.append((score, pb))
                    except Exception:
                        pass
        matches.sort(key=lambda x: x[0], reverse=True)
        return [m[1] for m in matches[:top_k]]

    def synthesize_from_run(
        self,
        challenge_id: str,
        challenge_title: str,
        category: str,
        target_endpoint: str,
        winning_payload: str,
        winning_commands: List[str],
        flag: str
    ) -> Optional[PlaybookSchema]:
        """Self-learning synthesis flywheel: parameterize winning solve trajectory into a new auto_generated playbook."""
        try:
            clean_category = category.lower() if category else "web"
            if clean_category not in CATEGORIES:
                clean_category = "web"

            parameterized_code = winning_payload or "\n".join(winning_commands)
            if target_endpoint:
                clean_target = target_endpoint.strip()
                parameterized_code = parameterized_code.replace(clean_target, "{TARGET_URL}")
                port_match = re.search(r":(\d{2,5})", clean_target)
                if port_match:
                    port_str = port_match.group(1)
                    parameterized_code = parameterized_code.replace(f":{port_str}", ":{PORT}")

            if flag and flag in parameterized_code:
                parameterized_code = parameterized_code.replace(flag, "{FLAG}")

            tags = [clean_category, "auto_learned"]
            signatures = []

            lower_code = parameterized_code.lower()
            if "ssti" in lower_code or "jinja" in lower_code or "{{" in parameterized_code:
                tags.extend(["ssti", "template_injection"])
                signatures.extend(["Jinja2", "Werkzeug", "render_template_string"])
            elif "sqli" in lower_code or "union select" in lower_code or "' or 1=1" in lower_code:
                tags.extend(["sqli", "sql_injection"])
                signatures.extend(["SQL syntax", "UNION SELECT", "database error"])
            elif "pwntools" in lower_code or "p32(" in lower_code or "p64(" in lower_code:
                tags.extend(["pwn", "buffer_overflow"])
                signatures.extend(["ELF", "checksec", "ROP"])
            elif "jwt" in lower_code or "bearer" in lower_code:
                tags.extend(["jwt", "token_forgery"])
                signatures.extend(["eyJ", "RS256", "none algorithm"])
            else:
                tags.append("custom_exploit")
                signatures.append(challenge_title[:40])

            content_hash = hashlib.md5(f"{challenge_title}_{parameterized_code}".encode("utf-8")).hexdigest()[:8]
            pb_id = f"gen-{clean_category}-{content_hash}"

            playbook = PlaybookSchema(
                id=pb_id,
                category=clean_category,
                tags=list(set(tags)),
                trigger_signatures=list(set(signatures)),
                exploit_template=parameterized_code,
                notes=f"Auto-synthesized from successful solve of challenge '{challenge_title}' (Flag: {flag[:12]}...).",
                source="generated",
                confidence_score=0.3,
                times_used=0,
                success_rate=0.0,
                is_promoted=False
            )

            self.save_playbook(playbook)
            logger.info(f"[Flywheel] Successfully synthesized auto_generated playbook `{pb_id}` from challenge {challenge_id}")
            return playbook
        except Exception as e:
            logger.error(f"[Flywheel] Failed to synthesize playbook: {e}", exc_info=True)
            return None

    def record_playbook_use(self, playbook_id: str, success: bool):
        """Update usage stats and auto-promote if confidence threshold reached."""
        pb = self.load_playbook(playbook_id)
        if not pb:
            return

        pb.times_used += 1
        alpha = 0.3
        current_val = 1.0 if success else 0.0
        pb.success_rate = round((1 - alpha) * pb.success_rate + alpha * current_val, 2)
        
        if success:
            pb.confidence_score = min(1.0, round(pb.confidence_score + 0.2, 2))
        else:
            pb.confidence_score = max(0.1, round(pb.confidence_score - 0.1, 2))

        if pb.source == "generated" and (pb.times_used >= self.REUSE_PROMOTION_THRESHOLD or pb.confidence_score >= self.CONFIDENCE_PROMOTION_THRESHOLD):
            pb.is_promoted = True
            logger.info(f"[Flywheel] Auto-promoting playbook `{pb.id}` to primary searchable vault! (uses={pb.times_used}, conf={pb.confidence_score})")

        self.save_playbook(pb)
        _broadcast_knowledge_event(
            "PLAYBOOK_STATS_UPDATED", pb.id, pb.category,
            {"is_promoted": pb.is_promoted, "confidence": pb.confidence_score}
        )

    async def ingest_writeup(self, text_or_markdown: str, category: str = "web", auto_approve: bool = False) -> Optional[PlaybookSchema]:
        """Ingestion pipeline: extract structured playbook from raw writeup text using fast provider tier."""
        from backend.providers.router import model_router
        clean_category = category.lower() if category in CATEGORIES else "web"

        prompt = f"""
You are an expert CTF exploit extractor. Extract a structured, reusable exploit playbook from the following CTF write-up.
Return a STRICT JSON object with these exact keys:
{{
  "id": "short-kebab-case-identifier",
  "tags": ["tag1", "tag2", "tag3"],
  "trigger_signatures": ["signature1", "signature2", "error_message_or_technology"],
  "exploit_template": "parameterized Python/bash exploit code with {{TARGET_URL}}, {{PORT}}, etc.",
  "notes": "key insights on how this exploit works and bypass mechanisms"
}}

Writeup Content:
\"\"\"
{text_or_markdown[:4000]}
\"\"\"
"""
        try:
            resp = await model_router.route_and_generate(
                capability="fast_reasoning",
                prompt=prompt,
                system_prompt="You are a JSON-only CTF exploit synthesizer. Output pure JSON without markdown code fences."
            )
            raw = resp.text.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"^```\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            import json
            parsed = json.loads(raw)

            pb_id = parsed.get("id", f"ingested-{clean_category}-{hashlib.md5(text_or_markdown[:100].encode()).hexdigest()[:6]}")
            playbook = PlaybookSchema(
                id=pb_id,
                category=clean_category,
                tags=parsed.get("tags", [clean_category]),
                trigger_signatures=parsed.get("trigger_signatures", []),
                exploit_template=parsed.get("exploit_template", "# No exploit template extracted"),
                notes=parsed.get("notes", "Ingested from CTF write-up"),
                source="ingested",
                confidence_score=0.9 if auto_approve else 0.5,
                times_used=0,
                success_rate=1.0,
                is_promoted=auto_approve
            )

            target_cat = clean_category if auto_approve else "pending_review"
            target_dir = os.path.join(self.base_dir, target_cat)
            os.makedirs(target_dir, exist_ok=True)
            file_path = os.path.join(target_dir, f"{playbook.id}.yaml")
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(playbook.model_dump(), f, default_flow_style=False)

            if auto_approve:
                self._index_single_playbook(playbook)

            logger.info(f"Ingested write-up playbook `{pb_id}` to `{target_cat}`")
            return playbook
        except Exception as e:
            logger.error(f"Failed to ingest write-up: {e}")
            return None

playbook_vault = PlaybookVault()
