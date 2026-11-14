"""身份合并 API 契约测试：401 / 403 / 201 / 幂等重试 / 快照返回（SQLite，无 Redis）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.config.app import create_app
from src.config.database import SessionLocal, engine
from src.models import Base
from src.models.channel import Channel
from src.models.user import User
from src.services import identity_service
from src.utils.security import hash_password


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all(
        [
            User(email="analyst@mixcredit.io", password_hash=hash_password("Demo@2026"), display_name="分析师", role="analyst"),
            User(email="viewer@mixcredit.io", password_hash=hash_password("Demo@2026"), display_name="只读", role="viewer"),
            Channel(code="a", name="渠道 A", type="content", monthly_cost=10.0, budget=10.0),
        ]
    )
    db.commit()
    db.close()
    with TestClient(create_app()) as test_client:
        yield test_client


def _token(client, email):
    response = client.post("/api/auth/login", json={"email": email, "password": "Demo@2026"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_anonymous_is_401_and_viewer_is_403(client):
    assert client.get("/api/identity-merges/summary").status_code == 401
    assert client.post("/api/identity-merges", json={"events": [{"event_key": "a1", "device_id": "D1", "member_id": "M1"}]}).status_code == 401
    viewer = _token(client, "viewer@mixcredit.io")
    response = client.post(
        "/api/identity-merges",
        json={"events": [{"event_key": "a1", "device_id": "D1", "member_id": "M1"}]},
        headers=viewer,
    )
    assert response.status_code == 403
    assert client.get("/api/identity-merges/summary", headers=viewer).status_code == 200


def test_empty_events_is_422(client):
    analyst = _token(client, "analyst@mixcredit.io")
    response = client.post("/api/identity-merges", json={"events": []}, headers=analyst)
    assert response.status_code == 422


def test_merge_returns_counts_and_snapshots(client):
    analyst = _token(client, "analyst@mixcredit.io")
    response = client.post(
        "/api/identity-merges",
        json={
            "events": [
                {"event_key": "e1", "device_id": "D1", "member_id": "M1", "arrived_at": "2026-10-01T10:00:00"},
                {"event_key": "e2", "device_id": "D1", "member_id": "M2", "arrived_at": "2026-10-01T11:00:00"},
                {"event_key": "e3", "device_id": "bad device!", "member_id": "M1", "arrived_at": "2026-10-01T12:00:00"},
            ],
            "note": "smoke",
        },
        headers=analyst,
    )
    assert response.status_code == 201
    payload = response.json()
    assert (payload["accepted_count"], payload["conflict_count"], payload["failed_count"]) == (1, 1, 1)
    assert "users" in payload["before"] and "users" in payload["after"]


def test_repeated_event_key_is_deduplicated(client):
    analyst = _token(client, "analyst@mixcredit.io")
    body = {"events": [{"event_key": "e1", "device_id": "D1", "member_id": "M1", "arrived_at": "2026-10-01T10:00:00"}]}
    first = client.post("/api/identity-merges", json=body, headers=analyst)
    second = client.post("/api/identity-merges", json=body, headers=analyst)
    assert first.status_code == second.status_code == 201
    assert second.json()["duplicate_count"] == 1
    assert second.json()["accepted_count"] == 0
    links = client.get("/api/identity-merges/links", headers=analyst).json()
    assert links["total"] == 1
    with SessionLocal() as db:
        assert identity_service.load_links(db) == {"D1": "M1"}
