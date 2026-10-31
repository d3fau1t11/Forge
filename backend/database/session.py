import os
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker
from backend.config import settings
from backend.database.models import Base

_engine_cache = {}

# Engine creation; schema upgrades below are SQLite-only.
def get_engine():
    current_url = os.getenv("DATABASE_URL", settings.DATABASE_URL)
    if current_url in _engine_cache:
        return _engine_cache[current_url]
    kwargs = {}
    if current_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(current_url, **kwargs)
    if current_url.startswith("sqlite"):
        # WAL lets concurrent swarm writers and API readers coexist; busy_timeout
        # makes a writer wait instead of failing with "database is locked".
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    _engine_cache[current_url] = engine
    return engine

def SessionLocal():
    eng = get_engine()
    return sessionmaker(autocommit=False, autoflush=False, bind=eng)()

def init_db():
    eng = get_engine()
    Base.metadata.create_all(bind=eng)
    current_url = os.getenv("DATABASE_URL", settings.DATABASE_URL)
    # Lightweight SQLite column migration
    if current_url.startswith("sqlite"):
        # Preserve the existing additive upgrades, but inspect before ALTER so
        # duplicate columns are safe and genuine migration failures propagate.
        # The three independent workspace tables are added by create_all above;
        # no legacy table or execution relationship needs a workspace migration.
        upgrades = {
            "challenges": {
                "progress": "INTEGER DEFAULT 0",
                "flag_status": "VARCHAR DEFAULT 'UNFOUND'",
                "working_directory": "VARCHAR DEFAULT ''",
                "platform_name": "VARCHAR DEFAULT ''",
                "difficulty": "VARCHAR DEFAULT 'MEDIUM'",
                "started_at": "TIMESTAMP",
                "completed_at": "TIMESTAMP",
                "duration_seconds": "INTEGER DEFAULT 0",
                "mission_plan": "JSON",
                "requires_root": "BOOLEAN DEFAULT 0",
            },
            "findings": {"severity": "VARCHAR DEFAULT 'HIGH'", "endpoint": "VARCHAR DEFAULT ''"},
            "experiences": {
                "required_os": "VARCHAR DEFAULT 'any'", "required_tools": "JSON", "required_python_libs": "JSON",
            },
            "swarm_tasks": {"required_capabilities": "JSON", "target_type": "VARCHAR DEFAULT ''"},
        }
        with eng.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            for table, columns in upgrades.items():
                existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
                for column, definition in columns.items():
                    if column not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))

    # Phase 2: warm the in-memory FTS indexes AFTER create_all + migration. The
    # experience/trajectory singletons build their indexes at import time, which in
    # the app happens BEFORE this function runs (routes are imported before startup
    # calls init_db). On a pre-existing DB whose `experiences` table predates the
    # environment-aware columns, that first import-time index build fails on the
    # missing column and leaves an EMPTY index for the process lifetime. Reloading
    # here — once the schema is correct — repairs it. Lazy-imported + non-fatal so
    # this never affects fresh installs or non-app entrypoints.
    try:
        from backend.knowledge.experience_memory import experience_memory as _em
        _em.reload_index()
    except Exception:
        pass
    try:
        from backend.agent_runtime.trajectory import trajectory_search as _ts
        _ts.reload_index()
    except Exception:
        pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
