import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AST_DB_PATH", str(tmp_path / "ast.sqlite"))
    from main import app
    with TestClient(app) as c:
        yield c


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_symbols_endpoint_returns_list(client):
    resp = client.get("/symbols?repo=missing&limit=10")
    assert resp.status_code == 200
    assert resp.json() == {"symbols": []}


def test_calls_endpoint_returns_list(client):
    resp = client.get("/calls?repo=missing&callee_name=foo&limit=10")
    assert resp.status_code == 200
    assert resp.json() == {"calls": []}


def test_imports_endpoint_returns_list(client):
    resp = client.get("/imports?repo=missing&module=fastapi&limit=10")
    assert resp.status_code == 200
    assert resp.json() == {"imports": []}


def test_status_endpoint_returns_latest_runs(client):
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "latest_runs" in resp.json()


def test_runs_endpoint_returns_list(client):
    resp = client.get("/runs?repo=missing&limit=10")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_search_requires_bounded_limit(client):
    resp = client.post(
        "/search",
        json={
            "repo": "repo",
            "language": "Python",
            "pattern": "$A($$$ARGS)",
            "limit": 1000,
        },
    )
    assert resp.status_code == 422


def test_search_returns_501_when_deferred(client):
    resp = client.post(
        "/search",
        json={
            "repo": "repo",
            "language": "Python",
            "pattern": "$A($$$ARGS)",
            "limit": 10,
        },
    )
    assert resp.status_code == 501


def test_scip_debug_endpoints_return_lists(client):
    assert client.get("/scip/documents?repo=missing").json() == {"documents": []}
    assert client.get("/scip/symbols?repo=missing").json() == {"symbols": []}
    assert client.get("/scip/occurrences?repo=missing").json() == {"occurrences": []}


def test_scip_export_json_returns_index_shape(client):
    resp = client.get("/scip/export.json?repo=missing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["metadata"]["tool_info"]["name"] == "repo-bot ast-service"
    assert data["documents"] == []


def test_graph_health_returns_disabled_by_default(client):
    resp = client.get("/graph/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["status"] == "disabled"


def test_graph_sync_returns_400_when_disabled(client):
    resp = client.post("/graph/sync")
    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"].lower()


def test_graph_impact_returns_400_when_disabled(client):
    resp = client.get("/graph/impact?repo=r&symbol=s")
    assert resp.status_code == 400


def test_graph_call_paths_returns_400_when_disabled(client):
    resp = client.get("/graph/call-paths?repo=r&from_symbol=a&to_symbol=b")
    assert resp.status_code == 400
