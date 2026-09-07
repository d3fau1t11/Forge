import os
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker
from backend.config import settings
from backend.database.models import Base

_engine_cache = {}

# Database engine initialization (Supports SQLite out-of-the-box and PostgreSQL)
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
        with eng.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN progress INTEGER DEFAULT 0"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN flag_status VARCHAR DEFAULT 'UNFOUND'"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN working_directory VARCHAR DEFAULT ''"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN platform_name VARCHAR DEFAULT ''"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN difficulty VARCHAR DEFAULT 'MEDIUM'"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN started_at TIMESTAMP"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN completed_at TIMESTAMP"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN duration_seconds INTEGER DEFAULT 0"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN mission_plan JSON"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN requires_root BOOLEAN DEFAULT 0"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE findings ADD COLUMN severity VARCHAR DEFAULT 'HIGH'"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE findings ADD COLUMN endpoint VARCHAR DEFAULT ''"))
                conn.commit()
            except Exception:
                pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
