import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.config import settings
from backend.database.models import Base

# Database engine initialization (Supports SQLite out-of-the-box and PostgreSQL)
def get_engine():
    current_url = os.getenv("DATABASE_URL", settings.DATABASE_URL)
    kwargs = {}
    if current_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(current_url, **kwargs)

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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
