from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.config import settings
from backend.database.models import Base

# Database engine initialization (Supports SQLite out-of-the-box and PostgreSQL)
engine_kwargs = {}
if settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
    # Lightweight SQLite column migration
    if settings.DATABASE_URL.startswith("sqlite"):
        with engine.connect() as conn:
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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
