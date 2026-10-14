import secrets

import pytest
from fastapi.testclient import TestClient

from jev_inbox.api import create_app
from jev_inbox.classifier import Decision
from jev_inbox.gmail import Message
from jev_inbox.store import Store


@pytest.fixture
def api(tmp_path):
    path = tmp_path / "test.sqlite3"
    store = Store(path)
    store.record(
        Message("m1", "t1", "Synthetic application", "never exposed", 1000),
        Decision("applied", "synthetic test"),
    )
    store.set("last_sync", "2026-01-01T00:00:00+00:00")
    store.close()
    token = secrets.token_urlsafe(32)
    with TestClient(create_app(path, token)) as client:
        yield client, {"Authorization": f"Bearer {token}"}, path


@pytest.mark.parametrize("route", ["/health", "/v1/summary", "/v1/applications", "/openapi.json"])
def test_every_read_requires_authentication(api, route):
    client, headers, _ = api
    assert client.get(route).status_code == 401
    assert client.get(route, headers={"Authorization": "Bearer incorrect"}).status_code == 401
    assert client.get(route, headers=headers).status_code == 200


def test_summaries_filters_and_details(api):
    client, headers, _ = api
    assert client.get("/v1/summary", headers=headers).json()["by_status"] == {"applied": 1}
    response = client.get("/v1/applications?status=applied&limit=1", headers=headers)
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "t1"
    assert "body" not in response.text and "never exposed" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/v1/applications?status=rejected", headers=headers).json()["items"] == []
    assert client.get("/v1/applications/t1", headers=headers).status_code == 200
    assert client.get("/v1/applications/missing", headers=headers).status_code == 404


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "status=garbage"])
def test_query_bounds(api, query):
    client, headers, _ = api
    assert client.get(f"/v1/applications?{query}", headers=headers).status_code == 422


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_writes_rejected_without_changing_database(api, method):
    client, headers, _ = api
    assert getattr(client, method)("/v1/applications", headers=headers).status_code == 405
    assert client.get("/v1/summary", headers=headers).json()["total"] == 1


def test_schema_describes_bearer_and_only_get_operations(api):
    client, headers, _ = api
    schema = client.get("/openapi.json", headers=headers).json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert all(set(route) == {"get"} for route in schema["paths"].values())
    assert client.get("/docs").status_code == 404


def test_missing_db_is_not_created_and_error_is_redacted(tmp_path):
    token = secrets.token_urlsafe(32)
    path = tmp_path / "missing.sqlite3"
    with TestClient(create_app(path, token)) as client:
        response = client.get("/v1/summary", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503 and not path.exists()
    assert str(path) not in response.text


def test_token_required(tmp_path):
    with pytest.raises(ValueError):
        create_app(tmp_path / "missing.sqlite3", "")
