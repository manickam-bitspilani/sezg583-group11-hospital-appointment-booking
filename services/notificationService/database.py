import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

# Values from a local .env file are used only when not already set in the
# environment (so Docker / Kubernetes env vars always win).
load_dotenv()


def _database_url():
    # DATABASE_URL overrides the individual settings (used by unit tests to
    # point at an in-memory SQLite database).
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    return URL.create(
        drivername="postgresql+psycopg",
        username=os.getenv("DB_USER", "notification_svc"),
        password=os.environ["DB_PASSWORD"],
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "notification_db"),
    )


DATABASE_URL = _database_url()

if str(DATABASE_URL).startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
