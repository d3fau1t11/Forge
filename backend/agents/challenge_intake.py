"""Challenge Intake Module (Workstream F4/F5).

Pure, testable module for conversational challenge creation. No FastAPI imports,
no HTTP, no DB access.
"""
from __future__ import annotations

import json
from typing import Optional

from backend.providers.router import model_router
from backend.utils.challenge_normalize import (
    CATEGORIES,
    normalize_category,
    normalize_difficulty,
    normalize_targets,
)

# System instruction for the intake assistant. The model must emit ONLY the JSON
# shape defined below — no prose, no markdown fences.
INTAKE_SYSTEM_INSTRUCTION = (
    "You are the FORGE intake assistant. Your job is to collect the six fields "
    "needed to create a challenge by asking the operator ONLY for fields that "
    "are still missing. Never claim a field was provided when it was not. "
    "Never mention or invent flags, credentials, exploits, or any solution "
    "details. Always reply with EXACTLY the following JSON shape and nothing "
    "else:\n"
    "{\n"
    '  "reply": "string shown to the operator",\n'
    '  "fields": {\n'
    '    "name": null,\n'
    '    "platform": null,\n'
    '    "category": null,\n'
    '    "difficulty": null,\n'
    '    "target_address": null,\n'
    '    "description": null\n'
    '  },\n'
    '  "ready_to_create": false\n'
    "}\n"
    "Only set ready_to_create to true when ALL six fields have non-null values. "
    "The reply field should be a natural, conversational prompt for the next "
    "missing piece of information (or a confirmation when ready)."
)

# The six expected field keys — used for validation.
_EXPECTED_FIELDS = frozenset({
    "name", "platform", "category", "difficulty", "target_address", "description"
})


def build_intake_prompt(transcript: list[dict], fields: dict) -> str:
    """Build the prompt sent to the model for the next intake turn.

    Args:
        transcript: List of {"role": "user"|"assistant", "content": str} messages.
        fields: Current collected field values (may contain None for missing).

    Returns:
        A prompt string that includes the conversation so far and the
        currently-missing fields.
    """
    # Format the transcript for the model
    lines = ["Conversation so far:"]
    for msg in transcript:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        lines.append(f"{role}: {content}")

    # Identify missing fields
    missing = [k for k, v in fields.items() if v is None or (isinstance(v, str) and not v.strip())]
    lines.append("\nCurrently missing fields: " + (", ".join(missing) if missing else "none"))

    # Include current field values for context
    lines.append("\nCurrent field values:")
    for key in sorted(fields.keys()):
        val = fields.get(key)
        lines.append(f"  {key}: {val if val is not None else 'MISSING'}")

    lines.append(
        "\nProduce the JSON response now. Ask for the next missing field, "
        "or confirm readiness if all six are present."
    )
    return "\n".join(lines)


def _parse_model_json(content: str) -> Optional[dict]:
    """Parse and validate a raw model JSON response.

    Strips optional ``` fences, parses JSON, validates the intake shape, converts
    empty-string field values to None, and normalizes category/difficulty/
    target_address. Returns None on ANY failure; never raises.
    """
    # Strip optional markdown fences
    content = (content or "").strip()
    if content.startswith("```"):
        # Strip ```json ... ``` or ``` ... ```
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None

    # Validate top-level structure
    if not isinstance(parsed, dict):
        return None

    # Validate 'reply' field
    reply = parsed.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return None

    # Validate 'fields' field
    model_fields = parsed.get("fields")
    if not isinstance(model_fields, dict):
        return None

    # Check for unknown keys
    if set(model_fields.keys()) - _EXPECTED_FIELDS:
        return None

    # Validate each field value is None or str
    for key, value in model_fields.items():
        if value is not None and not isinstance(value, str):
            return None

    # Validate 'ready_to_create' field
    ready_to_create = parsed.get("ready_to_create")
    if not isinstance(ready_to_create, bool):
        return None

    # Normalize category, difficulty, target_address
    normalized_fields = {}
    for key in _EXPECTED_FIELDS:
        value = model_fields.get(key)
        # Treat empty strings as missing
        if isinstance(value, str) and not value.strip():
            value = None
        if key == "category":
            normalized_fields[key] = normalize_category(value)
        elif key == "difficulty":
            # Only normalize if model provided a value; preserve None for unextracted fields
            normalized_fields[key] = normalize_difficulty(value) if value is not None else None
        elif key == "target_address":
            normalized_fields[key] = normalize_targets(value) if value else None
        else:
            normalized_fields[key] = value

    return {
        "reply": reply.strip(),
        "fields": normalized_fields,
        "ready_to_create": ready_to_create,
    }


async def interpret_operator_message(transcript: list[dict], fields: dict) -> Optional[dict]:
    """Interpret the operator's latest message and update intake state.

    Calls the model router with the chat_creation capability, parses the JSON
    response, validates it, normalizes category/difficulty/target_address, and
    returns a structured dict — or None on ANY failure.

    Args:
        transcript: Full conversation history including the latest user message.
        fields: Current field values before this turn.

    Returns:
        {"reply": str, "fields": dict, "ready_to_create": bool} on success,
        None on any parsing/validation error or model refusal.
    """
    # Deterministic, non-LLM fallback: extract a target from the operator's
    # latest message BEFORE the model is consulted. The call site
    # (backend/api/routes/challenges.py) builds the transcript in ascending
    # created_at order with the just-persisted user turn appended last, so the
    # last "user" entry is the operator's latest turn. This guarantees a
    # volunteered target is captured even when the model leaves
    # target_address null. Imported lazily to keep this module's top-level
    # import graph free of FastAPI/SQLAlchemy (see module docstring); there is
    # no circular import because challenges.py only imports this module inside
    # its route functions.
    from backend.api.routes.challenges import extract_target_from_text

    latest_user_text = ""
    for msg in reversed(transcript or []):
        if msg.get("role") == "user":
            latest_user_text = msg.get("content") or ""
            break
    deterministic_target = extract_target_from_text(latest_user_text)

    # Build the prompt
    prompt = build_intake_prompt(transcript, fields)

    try:
        # Route to the model with chat_creation capability
        response = await model_router.route_request(
            prompt=prompt,
            system_instruction=INTAKE_SYSTEM_INSTRUCTION,
            capability="chat_creation",
        )
    except Exception:
        # Any exception from the router -> treat as failure
        return None

    # Validate response object
    if response is None:
        return None
    if response.is_refusal:
        return None

    parsed = _parse_model_json(response.content)
    if parsed is None:
        return None

    # Prefer the model's target_address when it provided one (it may have
    # cleaned or combined it better). Only fall back to the deterministic
    # extraction when the model returned null, so a volunteered target is
    # never silently dropped.
    if deterministic_target and not parsed["fields"].get("target_address"):
        parsed["fields"]["target_address"] = normalize_targets(deterministic_target)

    return parsed


async def generate_opening_message() -> Optional[str]:
    """Generate the opening intake question with the model.

    Asks the model for the first question of a new intake session, then parses
    its response with the same validation used for operator turns. Returns the
    parsed reply string, or None on ANY failure (router error, refusal, or
    unparseable response). Never raises.
    """
    prompt = (
        "This is the very start of a new challenge intake session. There is no "
        "conversation history yet and no fields collected. Produce the opening JSON "
        "response now: greet the operator and ask for the three required fields — the "
        "challenge name, the category, and the difficulty. You may mention that the "
        "platform is optional. Ask for the opening information in a natural, "
        "conversational way."
    )

    try:
        response = await model_router.route_request(
            prompt=prompt,
            system_instruction=INTAKE_SYSTEM_INSTRUCTION,
            capability="chat_creation",
        )
    except Exception:
        # Any exception from the router -> treat as failure
        return None

    if response is None:
        return None
    if response.is_refusal:
        return None

    parsed = _parse_model_json(response.content)
    if parsed is None:
        return None
    return parsed["reply"]