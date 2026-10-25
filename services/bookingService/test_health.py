
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import main

SERVICE = "booking-service"


def test_health_is_ok_when_database_answers():
    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": SERVICE, "database": "up"}


def test_health_is_503_when_database_is_down(monkeypatch):
    # Nothing listens on port 1: the connection is refused, as when PostgreSQL is stopped.
    monkeypatch.setattr(main, "engine", create_engine("postgresql+psycopg://u:p@127.0.0.1:1/none"))

    response = TestClient(main.app).get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "service": SERVICE, "database": "down"}
