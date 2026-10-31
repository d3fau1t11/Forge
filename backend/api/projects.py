"""Authenticated project/conversation persistence. No execution dispatch."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.api.auth import require_api_token
from backend.database.models import (
    ProjectModel, WorkspaceSessionModel, ConversationMessageModel, generate_uuid,
)
from backend.database.session import get_db
from backend.utils.project_workspace import (
    ProjectWorkspaceError, allocate_project_directory, allocate_session_directory,
    validate_project_directory, validate_session_directory,
)

MAX_CONTENT_BYTES = 64_000
MAX_CONTEXT_BYTES = 16_384
MAX_REQUEST_BYTES = 262_144
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, strict=True)]
Content = Annotated[str, StringConstraints(min_length=1, max_length=MAX_CONTENT_BYTES, strict=True)]


def _error(status, code, message, headers=None):
    return HTTPException(status_code=status, detail={"code": code, "message": message}, headers=headers)


async def _bounded_request(request: Request):
    if len(await request.body()) > MAX_REQUEST_BYTES:
        raise _error(413, "request_too_large", "Request body exceeds 256 KiB")


router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(require_api_token)])


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("context", check_fields=False)
    @classmethod
    def bounded_context(cls, value):
        if value is not None:
            try:
                encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (ValueError, TypeError, RecursionError) as exc:
                raise ValueError("Context must be finite JSON-compatible data") from exc
            if len(encoded) > MAX_CONTEXT_BYTES:
                raise ValueError("Context exceeds 16 KiB")
        return value

    @field_validator("name", "title", "instructions", check_fields=False)
    @classmethod
    def non_null_metadata(cls, value):
        if value is None:
            raise ValueError("This field cannot be null")
        return value


class CreateProjectRequest(WorkspaceRequest):
    name: Name
    description: Optional[str] = Field(default=None, max_length=16_000)
    instructions: str = Field(default="", max_length=64_000)
    # User-supplied metadata only; never populated from settings or credentials.
    context: Optional[dict[str, Any]] = None


class UpdateProjectRequest(WorkspaceRequest):
    name: Optional[Name] = None
    description: Optional[str] = Field(default=None, max_length=16_000)
    instructions: Optional[str] = Field(default=None, max_length=64_000)
    context: Optional[dict[str, Any]] = None
    archived: Optional[bool] = None

    @field_validator("archived")
    @classmethod
    def archive_only(cls, value):
        if value is not True:
            raise ValueError("Only archived=true is supported")
        return value


class CreateSessionRequest(WorkspaceRequest):
    title: Name = "New session"
    context: Optional[dict[str, Any]] = None


class UpdateSessionRequest(WorkspaceRequest):
    title: Optional[Name] = None
    context: Optional[dict[str, Any]] = None
    archived: Optional[bool] = None

    @field_validator("archived")
    @classmethod
    def archive_only(cls, value):
        return UpdateProjectRequest.archive_only(value)


class AppendMessageRequest(WorkspaceRequest):
    content: Content
    client_message_id: Optional[Name] = None

    @field_validator("content")
    @classmethod
    def meaningful_content(cls, value):
        if not value.strip():
            raise ValueError("Message content must not be blank")
        if len(value.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ValueError("Message content exceeds 64000 UTF-8 bytes")
        return value


class PersistedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime


class ProjectResponse(PersistedResponse):
    name: str
    description: Optional[str]
    instructions: str
    context: Optional[dict[str, Any]]
    workspace_path: str
    updated_at: datetime
    archived_at: Optional[datetime]


class SessionResponse(PersistedResponse):
    project_id: UUID
    title: str
    context: Optional[dict[str, Any]]
    workspace_path: str
    last_sequence: int
    updated_at: datetime
    archived_at: Optional[datetime]


class MessageResponse(PersistedResponse):
    workspace_session_id: UUID
    role: str
    content: str
    sequence: int
    client_message_id: Optional[str]
    source_trajectory_event_id: Optional[str]


@contextmanager
def _write_transaction(db: Session):
    """Acquire SQLite's writer reservation BEFORE any reads (no snapshot upgrade).

    The request must supply a fresh Session. SQLite serializes database writers,
    including across backend processes; the existing busy_timeout bounds waiting.
    All ownership, archive, deduplication, sequence and insert work is one commit.
    """
    if db.in_transaction():
        raise RuntimeError("Workspace writes require a fresh database session")
    if db.get_bind().dialect.name != "sqlite":
        raise _error(503, "unsupported_database", "Workspace writes currently require SQLite")
    try:
        db.execute(text("BEGIN IMMEDIATE"))
        yield
        db.commit()
    except OperationalError as exc:
        db.rollback()
        code = getattr(exc.orig, "sqlite_errorcode", 0) & 0xFF
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise _error(503, "database_busy", "Database is busy; retry the request", {"Retry-After": "1"}) from exc
        raise
    except IntegrityError as exc:
        db.rollback()
        raise _error(409, "persistence_conflict", "Workspace persistence constraint failed") from exc
    except FileExistsError as exc:
        db.rollback()
        raise _error(409, "workspace_collision", "Workspace directory already exists") from exc
    except ProjectWorkspaceError as exc:
        db.rollback()
        raise _error(400, "invalid_workspace", "Workspace directory failed validation") from exc
    except OSError as exc:
        db.rollback()
        raise _error(500, "workspace_allocation_failed", "Could not allocate workspace directory") from exc
    except Exception:
        db.rollback()
        raise
    # On allocation/commit failure directories may remain. Deliberately do NOT
    # delete them: a later allocation must refuse to adopt an existing directory.


def _project(db, project_id):
    row = db.get(ProjectModel, str(project_id))
    if row is None:
        raise _error(404, "project_not_found", "Project not found")
    return row


def _session(db, project_id, session_id):
    project = _project(db, project_id)
    row = db.scalar(select(WorkspaceSessionModel).where(
        WorkspaceSessionModel.id == str(session_id), WorkspaceSessionModel.project_id == project.id,
    ))
    if row is None:
        raise _error(404, "session_not_found", "Workspace session not found in this project")
    return project, row


def _active(project, session=None):
    if project.archived_at is not None or (session is not None and session.archived_at is not None):
        raise _error(409, "workspace_archived", "Archived workspaces are read-only")


def _patch(row, changes):
    for key, value in changes.items():
        if key == "archived":
            row.archived_at = row.archived_at or datetime.utcnow()
        else:
            setattr(row, key, value)
    if changes:
        row.updated_at = datetime.utcnow()


@router.get("", response_model=list[ProjectResponse])
def list_projects(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  include_archived: bool = False, db: Session = Depends(get_db)):
    query = select(ProjectModel)
    if not include_archived:
        query = query.where(ProjectModel.archived_at.is_(None))
    return db.scalars(query.order_by(ProjectModel.created_at, ProjectModel.id).offset(offset).limit(limit)).all()


@router.post("", response_model=ProjectResponse, status_code=201, dependencies=[Depends(_bounded_request)])
def create_project(request: CreateProjectRequest, db: Session = Depends(get_db)):
    with _write_transaction(db):
        project_id = generate_uuid()
        row = ProjectModel(id=project_id, workspace_path=allocate_project_directory(project_id), **request.model_dump())
        db.add(row)
        db.flush()
        result = ProjectResponse.model_validate(row)
    return result


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: UUID, db: Session = Depends(get_db)):
    return _project(db, project_id)


@router.patch("/{project_id}", response_model=ProjectResponse, dependencies=[Depends(_bounded_request)])
def update_project(project_id: UUID, request: UpdateProjectRequest, db: Session = Depends(get_db)):
    with _write_transaction(db):
        row = _project(db, project_id)
        changes = request.model_dump(exclude_unset=True)
        if changes != {"archived": True}:
            _active(row)
        _patch(row, changes)
        db.flush()
        result = ProjectResponse.model_validate(row)
    return result


@router.get("/{project_id}/sessions", response_model=list[SessionResponse])
def list_sessions(project_id: UUID, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  include_archived: bool = False, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    query = select(WorkspaceSessionModel).where(WorkspaceSessionModel.project_id == project.id)
    if not include_archived:
        query = query.where(WorkspaceSessionModel.archived_at.is_(None))
    return db.scalars(query.order_by(WorkspaceSessionModel.created_at, WorkspaceSessionModel.id).offset(offset).limit(limit)).all()


@router.post("/{project_id}/sessions", response_model=SessionResponse, status_code=201,
             dependencies=[Depends(_bounded_request)])
def create_session(project_id: UUID, request: CreateSessionRequest, db: Session = Depends(get_db)):
    with _write_transaction(db):
        project = _project(db, project_id)
        _active(project)
        session_id = generate_uuid()
        path = allocate_session_directory(project.id, project.workspace_path, session_id)
        row = WorkspaceSessionModel(id=session_id, project_id=project.id, workspace_path=path, **request.model_dump())
        db.add(row)
        db.flush()
        result = SessionResponse.model_validate(row)
    return result


@router.get("/{project_id}/sessions/{session_id}", response_model=SessionResponse)
def get_session(project_id: UUID, session_id: UUID, db: Session = Depends(get_db)):
    return _session(db, project_id, session_id)[1]


@router.patch("/{project_id}/sessions/{session_id}", response_model=SessionResponse,
              dependencies=[Depends(_bounded_request)])
def update_session(project_id: UUID, session_id: UUID, request: UpdateSessionRequest, db: Session = Depends(get_db)):
    with _write_transaction(db):
        project, row = _session(db, project_id, session_id)
        changes = request.model_dump(exclude_unset=True)
        if changes != {"archived": True}:
            _active(project, row)
        _patch(row, changes)
        db.flush()
        result = SessionResponse.model_validate(row)
    return result


@router.get("/{project_id}/sessions/{session_id}/messages", response_model=list[MessageResponse])
def list_messages(project_id: UUID, session_id: UUID, limit: int = Query(50, ge=1, le=200),
                  offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    _session(db, project_id, session_id)
    return db.scalars(select(ConversationMessageModel).where(
        ConversationMessageModel.workspace_session_id == str(session_id),
    ).order_by(ConversationMessageModel.sequence).offset(offset).limit(limit)).all()


@router.post("/{project_id}/sessions/{session_id}/messages", response_model=MessageResponse, status_code=201,
             dependencies=[Depends(_bounded_request)])
def append_message(project_id: UUID, session_id: UUID, request: AppendMessageRequest,
                   response: Response, db: Session = Depends(get_db)):
    with _write_transaction(db):
        project, session = _session(db, project_id, session_id)
        existing = None
        if request.client_message_id is not None:
            existing = db.scalar(select(ConversationMessageModel).where(
                ConversationMessageModel.workspace_session_id == session.id,
                ConversationMessageModel.client_message_id == request.client_message_id,
            ))
        if existing is not None:
            # Even an archived workspace can answer a retry without mutating it.
            response.status_code = 200
            result = MessageResponse.model_validate(existing)
        else:
            _active(project, session)
            validate_project_directory(project.id, project.workspace_path)
            validate_session_directory(project.id, session.id, session.workspace_path)
            session.last_sequence += 1
            session.updated_at = datetime.utcnow()
            row = ConversationMessageModel(
                workspace_session_id=session.id, role="user", content=request.content,
                sequence=session.last_sequence, client_message_id=request.client_message_id,
            )
            db.add(row)
            db.flush()
            result = MessageResponse.model_validate(row)
    return result
