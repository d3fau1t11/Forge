"""Durable launch acceptance only. No dispatch, execution or filesystem access.

Call with a clean, engine-bound SQLAlchemy Session. Like existing workspace
writes, this service supports SQLite and reserves its writer with BEGIN IMMEDIATE
before reading any ownership, context or retry state. It owns one commit/rollback;
it must not be nested in a caller's transaction.

Version 1 snapshots are canonical UTF-8 JSON (sorted keys, compact separators,
finite values, no whitespace/content normalization). They copy only project
instructions/context, session context, and actual conversation message fields.
Messages with sequence <= the explicit cutoff are included in sequence order;
0 includes none. A positive cutoff must identify an existing message and cannot
exceed the session's durable last_sequence. Gaps include only extant records.
An initiating message, if given, must belong to this session and be included.
Snapshot timestamps follow the models' existing naive-UTC convention.

The row is append-only through this service and its ORM update guard. Snapshot
text and frozen outputs share no mutable objects with models or callers; decoding
returns a fresh dictionary. Direct SQL writes are outside this service contract.
Conversation provenance IDs remain traceability only, never verified evidence.

Keys are globally unique, case-sensitive and preserved verbatim. Retries compare
both identity and the exact accepted snapshot against current context through the
same cutoff, using the original creation time. Context edits conflict; later
messages beyond the cutoff do not. Matching retries work after archive or legacy
challenge/run deletion; new bindings require active workspace parents and live,
compatible challenge/run records. A run can belong to only one binding. Run status
is not authorization to execute and is neither interpreted nor changed here.
This database transaction does not resolve F-03 execution/deletion coordination.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import event, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Mapper, Session

from backend.database.models import (
    ChallengeModel, ConversationMessageModel, ProjectModel, RunModel,
    SessionExecutionBindingModel, WorkspaceSessionModel,
)


class SessionBindingError(Exception):
    """Stable persistence error code; contains no payload or database details."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CreateSessionExecutionBinding:
    project_id: str
    workspace_session_id: str
    challenge_id: str
    run_id: str
    idempotency_key: str
    message_cutoff_sequence: int
    initiating_message_id: str | None = None


@dataclass(frozen=True)
class SessionExecutionBinding:
    id: str
    project_id: str
    workspace_session_id: str
    challenge_id: str
    run_id: str
    initiating_message_id: str | None
    idempotency_key: str
    message_cutoff_sequence: int
    created_at: datetime
    snapshot_json: str
    snapshot_sha256: str
    created: bool

    def snapshot_data(self) -> dict[str, Any]:
        """Decode a new copy; mutating it cannot affect the persisted value."""
        return json.loads(self.snapshot_json)


@event.listens_for(SessionExecutionBindingModel, "before_update")
def _reject_binding_update(mapper: Mapper, connection: Connection,
                           target: SessionExecutionBindingModel) -> None:
    raise SessionBindingError("immutable_binding", "Accepted execution bindings cannot be updated")


def _validate_request(request: CreateSessionExecutionBinding) -> None:
    for field in ("project_id", "workspace_session_id", "challenge_id", "run_id", "idempotency_key"):
        value = getattr(request, field)
        if not isinstance(value, str) or not value.strip():
            raise SessionBindingError("invalid_request", f"{field} must be a nonblank string")
    if len(request.idempotency_key) > 200:
        raise SessionBindingError("invalid_request", "Idempotency key exceeds 200 characters")
    if request.initiating_message_id is not None and (
        not isinstance(request.initiating_message_id, str) or not request.initiating_message_id.strip()
    ):
        raise SessionBindingError("invalid_request", "Initiating message ID must be a nonblank string or None")
    if type(request.message_cutoff_sequence) is not int or request.message_cutoff_sequence < 0:
        raise SessionBindingError("invalid_request", "Message cutoff must be a nonnegative integer")


def _snapshot(db: Session, request: CreateSessionExecutionBinding, project: ProjectModel,
              workspace_session: WorkspaceSessionModel, created_at: datetime) -> str:
    cutoff = request.message_cutoff_sequence
    if cutoff > workspace_session.last_sequence:
        raise SessionBindingError("invalid_cutoff", "Message cutoff exceeds the session sequence")
    if request.initiating_message_id is not None:
        message = db.get(ConversationMessageModel, request.initiating_message_id, populate_existing=True)
        if message is None:
            raise SessionBindingError("message_not_found", "Initiating conversation message not found")
        if message.workspace_session_id != workspace_session.id:
            raise SessionBindingError("message_ownership_mismatch", "Initiating message belongs to another session")
        if message.sequence > cutoff:
            raise SessionBindingError("invalid_cutoff", "Initiating message must be included in the snapshot")
    messages = db.scalars(select(ConversationMessageModel).where(
        ConversationMessageModel.workspace_session_id == workspace_session.id,
        ConversationMessageModel.sequence <= cutoff,
    ).order_by(ConversationMessageModel.sequence).execution_options(populate_existing=True)).all()
    if cutoff and (not messages or messages[-1].sequence != cutoff):
        raise SessionBindingError("invalid_cutoff", "Positive cutoff must identify an existing message")
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(timespec="microseconds"),
        "project": {"id": project.id, "instructions": project.instructions, "context": project.context},
        "workspace_session": {
            "id": workspace_session.id, "project_id": workspace_session.project_id, "context": workspace_session.context,
        },
        "execution": {
            "challenge_id": request.challenge_id, "run_id": request.run_id,
            "initiating_message_id": request.initiating_message_id,
        },
        "conversation": {
            "cutoff_sequence": cutoff,
            "messages": [{
                "id": message.id, "workspace_session_id": message.workspace_session_id,
                "role": message.role, "content": message.content, "sequence": message.sequence,
                "client_message_id": message.client_message_id,
                "source_trajectory_event_id": message.source_trajectory_event_id,
                "created_at": message.created_at.isoformat(timespec="microseconds"),
            } for message in messages],
        },
    }
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise SessionBindingError("invalid_snapshot", "Launch context must contain finite JSON values") from exc


def _result(row: SessionExecutionBindingModel, *, created: bool) -> SessionExecutionBinding:
    if hashlib.sha256(row.snapshot_json.encode("utf-8")).hexdigest() != row.snapshot_sha256:
        raise SessionBindingError("corrupt_binding", "Persisted launch snapshot digest does not match")
    return SessionExecutionBinding(
        id=row.id, project_id=row.project_id, workspace_session_id=row.workspace_session_id,
        challenge_id=row.challenge_id, run_id=row.run_id, initiating_message_id=row.initiating_message_id,
        idempotency_key=row.idempotency_key, message_cutoff_sequence=row.message_cutoff_sequence,
        created_at=row.created_at, snapshot_json=row.snapshot_json, snapshot_sha256=row.snapshot_sha256, created=created,
    )


def create_session_execution_binding(db: Session, request: CreateSessionExecutionBinding) -> SessionExecutionBinding:
    """Validate and atomically accept one existing run; never change its state.

    Caller authorization belongs at the future API boundary. Workspace/session
    ownership is independently checked here. Failed calls roll back only the
    service's transaction; a pre-existing caller transaction is refused untouched.
    """
    _validate_request(request)
    if db.in_transaction() or db.new or db.dirty or db.deleted:
        raise SessionBindingError("transaction_in_progress", "Binding writes require a clean database session")
    bind = db.get_bind()
    if not isinstance(bind, Engine):
        raise SessionBindingError("external_transaction", "Binding writes require an engine-bound session")
    if bind.dialect.name != "sqlite":
        raise SessionBindingError("unsupported_database", "Binding writes currently require SQLite")
    try:
        db.execute(text("BEGIN IMMEDIATE"))
        existing = db.scalar(select(SessionExecutionBindingModel).where(
            SessionExecutionBindingModel.idempotency_key == request.idempotency_key,
        ).execution_options(populate_existing=True))
        identity_fields = ("project_id", "workspace_session_id", "challenge_id", "run_id",
                           "initiating_message_id", "message_cutoff_sequence")
        if existing is not None and any(getattr(existing, field) != getattr(request, field) for field in identity_fields):
            raise SessionBindingError("idempotency_conflict", "Idempotency key already accepted a different identity")
        project = db.get(ProjectModel, request.project_id, populate_existing=True)
        if project is None:
            raise SessionBindingError("project_not_found", "Project not found")
        workspace_session = db.get(WorkspaceSessionModel, request.workspace_session_id, populate_existing=True)
        if workspace_session is None:
            raise SessionBindingError("session_not_found", "Workspace session not found")
        if workspace_session.project_id != project.id:
            raise SessionBindingError("session_ownership_mismatch", "Workspace session belongs to another project")
        if existing is None:
            if project.archived_at is not None or workspace_session.archived_at is not None:
                raise SessionBindingError("workspace_archived", "Archived workspaces are read-only")
            challenge = db.get(ChallengeModel, request.challenge_id, populate_existing=True)
            if challenge is None:
                raise SessionBindingError("challenge_not_found", "Challenge not found")
            run = db.get(RunModel, request.run_id, populate_existing=True)
            if run is None:
                raise SessionBindingError("run_not_found", "Execution run not found")
            if run.challenge_id != challenge.id:
                raise SessionBindingError("run_challenge_mismatch", "Execution run belongs to another challenge")
        created_at = existing.created_at if existing is not None else datetime.utcnow()
        snapshot_json = _snapshot(db, request, project, workspace_session, created_at)
        if existing is not None:
            if existing.snapshot_json != snapshot_json:
                raise SessionBindingError("idempotency_conflict", "Idempotency key already accepted different launch context")
            result = _result(existing, created=False)
        else:
            row = SessionExecutionBindingModel(
                project_id=project.id, workspace_session_id=workspace_session.id,
                challenge_id=request.challenge_id, run_id=request.run_id,
                initiating_message_id=request.initiating_message_id, idempotency_key=request.idempotency_key,
                message_cutoff_sequence=request.message_cutoff_sequence, created_at=created_at,
                snapshot_json=snapshot_json, snapshot_sha256=hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
            )
            db.add(row)
            db.flush()
            result = _result(row, created=True)
        db.commit()
        return result
    except IntegrityError as exc:
        db.rollback()
        raise SessionBindingError("persistence_conflict", "Execution binding uniqueness or ownership constraint failed") from exc
    except OperationalError as exc:
        db.rollback()
        code = getattr(exc.orig, "sqlite_errorcode", 0) & 0xFF
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise SessionBindingError("database_busy", "Database is busy; retry the same binding request") from exc
        raise SessionBindingError("persistence_failed", "Could not persist execution binding") from exc
    except BaseException:
        db.rollback()
        raise
