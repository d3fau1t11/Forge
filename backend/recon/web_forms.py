"""Deterministic web-form surface helpers.

Two pure, dependency-free helpers that answer the two questions the SSTI1 run
(challenge_3a8bfc5c) could not answer for itself:

1. :func:`extract_forms` — *which field names does the target's HTML actually
   expose?* In that run an agent posted to a field name it invented ("message")
   while the form exposed "content". The target redirected regardless (302 ->
   /announce) and rendered an EMPTY announcement for the unknown field, so every
   injection was a silent no-op that FORGE recorded as a failed technique.

2. :func:`verify_template_probe` — *did a self-checking payload actually render?*
   ``{{7*7}}`` is self-checking: a live template engine returns ``49``. Nothing
   previously checked, so "the payload never reached the sink" and "the technique
   does not work here" were indistinguishable.

Both run on every tool output in the swarm hot path, so they use the stdlib only
(``html.parser``, no bs4) and never raise.
"""

import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

# Cheap precondition markers — most tool output (nmap, strings, hexdumps, logs)
# is not HTML, and we skip the parse entirely rather than pay for it.
_FORM_MARKERS = ("<form", "<input", "<textarea", "<select")

# Named elements that submit a value. Buttons are included because a named
# submit button is a real parameter in HTML form encoding.
_FIELD_TAGS = {"input", "textarea", "select", "button"}

# Cap the field list rendered into the prompt so one pathological page cannot
# crowd out the rest of the shared context.
_MAX_FIELDS_DISPLAYED = 8


class _FormCollector(HTMLParser):
    """Collect ``<form>`` elements and their named fields.

    Only fields *inside* a form element are collected. Inputs that live outside
    any form (JS-driven pages) are deliberately ignored: HTML does not submit
    them, so reporting them would be a guess — and guessing field names is the
    exact failure this module exists to remove.
    """

    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self._base_url = base_url or ""
        self.forms: List[Dict[str, Any]] = []
        self._current: Optional[Dict[str, Any]] = None

    # -- helpers ---------------------------------------------------------

    def _resolve(self, action: str) -> str:
        """Per the HTML spec an absent/empty action means the current URL."""
        action = (action or "").strip()
        if not action:
            return self._base_url
        if not self._base_url:
            return action
        try:
            return urljoin(self._base_url, action)
        except Exception:
            return action

    # -- HTMLParser hooks ------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}

        if tag == "form":
            self._current = {
                "action": self._resolve(a.get("action", "")),
                "method": (a.get("method") or "GET").strip().upper() or "GET",
                "fields": [],
            }
            self.forms.append(self._current)
            return

        if tag not in _FIELD_TAGS or self._current is None:
            return

        name = (a.get("name") or "").strip()
        if not name:
            return

        ftype = (a.get("type") or "").strip().lower()
        if not ftype:
            # textarea/select have no type attribute; input defaults to text.
            ftype = "textarea" if tag == "textarea" else ("select" if tag == "select" else "text")

        field = {"name": name, "type": ftype, "id": (a.get("id") or "").strip()}
        # Same-name fields can repeat (checkbox groups); keep each occurrence once.
        if field not in self._current["fields"]:
            self._current["fields"].append(field)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


def extract_forms(html: str, base_url: str = "") -> List[Dict[str, Any]]:
    """Return the forms a page exposes, as
    ``[{"action": str, "method": str, "fields": [{"name", "type", "id"}]}]``.

    Pure and non-fatal: returns ``[]`` for non-HTML input or malformed markup
    rather than raising, because a parse failure must never break an agent turn.
    """
    if not html or not isinstance(html, str):
        return []
    low = html.lower()
    if not any(marker in low for marker in _FORM_MARKERS):
        return []

    collector = _FormCollector(base_url)
    try:
        collector.feed(html)
        collector.close()
    except Exception:
        # Malformed markup: keep whatever was collected before the failure.
        pass

    # Drop forms that expose no usable field — a fieldless <form> gives an agent
    # nothing to act on and would only add prompt noise.
    return [f for f in collector.forms if f.get("fields")]


def describe_form(form: Dict[str, Any]) -> str:
    """One-line rendering used by both the challenge log and the agent prompt."""
    method = (form.get("method") or "GET").upper()
    action = form.get("action") or "/"
    fields = form.get("fields") or []
    shown = []
    for f in fields[:_MAX_FIELDS_DISPLAYED]:
        label = f.get("name", "")
        ident = f.get("id") or ""
        if ident:
            label = f"{label} (#{ident})"
        shown.append(label)
    if len(fields) > _MAX_FIELDS_DISPLAYED:
        shown.append(f"+{len(fields) - _MAX_FIELDS_DISPLAYED} more")
    return f"{method} {action} → {', '.join(shown)}" if shown else f"{method} {action} → (no named fields)"


# ── Self-checking injection probes ───────────────────────────────────────────
# ``{{7*7}}`` / ``${7*7}`` render to a predictable value only if the payload
# actually reached a template engine. Only +, - and * are recognised: / and %
# can raise (division by zero) inside the target, which would make an otherwise
# valid probe look like a delivery failure.
_PROBE_RES: Tuple[re.Pattern, ...] = (
    re.compile(r"\{\{\s*(\d{1,6})\s*([+\-*])\s*(\d{1,6})\s*\}\}"),
    re.compile(r"\$\{\s*(\d{1,6})\s*([+\-*])\s*(\d{1,6})\s*\}"),
)


def _eval_probe(a: str, op: str, b: str) -> Optional[int]:
    try:
        x, y = int(a), int(b)
    except (TypeError, ValueError):
        return None
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    return None


def _find_probes(command: str) -> List[Tuple[str, int]]:
    """Return ``[(payload_literal, expected_value)]`` for each probe in *command*."""
    found: List[Tuple[str, int]] = []
    seen = set()
    for pattern in _PROBE_RES:
        for m in pattern.finditer(command):
            expected = _eval_probe(m.group(1), m.group(2), m.group(3))
            if expected is None:
                continue
            literal = m.group(0)
            if literal in seen:
                continue
            seen.add(literal)
            found.append((literal, expected))
    return found


def _contains_token(text: str, value: int) -> bool:
    """True when *value* appears as a standalone number.

    Guards against incidental matches (a port number, byte count or CSS value
    containing the same digits) that would otherwise read as a successful render.
    """
    return re.search(rf"(?<!\d){re.escape(str(value))}(?!\d)", text) is not None


def verify_template_probe(command: str, output: str) -> Optional[Dict[str, Any]]:
    """Decide whether a self-checking payload in *command* actually rendered.

    Returns ``{"payload", "expected", "delivered", "reason"}``, or ``None`` when
    the command carries no self-checking probe or there is no output to judge.

    When a command carries several probes the FIRST undelivered one is reported —
    that is the actionable case (the caller surfaces it to the agents). Only if
    every probe rendered is a delivered probe returned.
    """
    if not command or not output:
        return None

    probes = _find_probes(command)
    if not probes:
        return None

    undelivered: List[Dict[str, Any]] = []
    delivered: List[Dict[str, Any]] = []

    for literal, expected in probes:
        record = {"payload": literal, "expected": expected}
        if _contains_token(output, expected) and literal not in output:
            delivered.append({**record, "delivered": True, "reason": "template executed"})
        elif literal in output:
            # Echoed back byte-for-byte: the value reached the page but no engine
            # evaluated it (a raw reflection, or an unparsed template body).
            undelivered.append({**record, "delivered": False,
                                "reason": "reflected verbatim without being executed"})
        else:
            # Neither the result nor the payload appears. For SSTI1 this was the
            # signature of posting to a field name the form does not expose.
            undelivered.append({**record, "delivered": False,
                                "reason": "neither the result nor the payload appears in the response"})

    return (undelivered or delivered or [None])[0]
