import os

# Unit tests run against an in-memory SQLite database: no PostgreSQL and no
# other services needed. Must be set before `database` is imported.
os.environ["DATABASE_URL"] = "sqlite://"

import pytest

from database import Base, SessionLocal, engine


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
