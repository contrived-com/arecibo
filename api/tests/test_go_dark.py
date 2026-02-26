def test_heartbeat_returns_go_dark_directive_when_forced(monkeypatch, sample_heartbeat):
    monkeypatch.setenv("ARECIBO_FORCE_GO_DARK", "true")
    monkeypatch.setenv("ARECIBO_API_KEYS", "test-key")

    import os
    import sys

    from fastapi.testclient import TestClient
    api_root = os.path.dirname(os.path.dirname(__file__))
    if api_root not in sys.path:
        sys.path.insert(0, api_root)
    from src.app import create_app

    with TestClient(create_app()) as client:
        response = client.post("/heartbeat", json=sample_heartbeat, headers={"X-API-Key": "test-key"})
    assert response.status_code == 202
    body = response.json()
    assert body["result"]["status"] == "directive"
    assert body["result"]["directives"][0]["type"] == "GO_DARK"


def test_events_batch_returns_go_dark_directive_when_endpoint_forced(
    monkeypatch, sample_events_batch
):
    monkeypatch.setenv("ARECIBO_FORCE_GO_DARK_ON", "events")
    monkeypatch.setenv("ARECIBO_API_KEYS", "test-key")

    import os
    import sys

    from fastapi.testclient import TestClient
    api_root = os.path.dirname(os.path.dirname(__file__))
    if api_root not in sys.path:
        sys.path.insert(0, api_root)
    from src.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/events:batch",
            json=sample_events_batch,
            headers={"X-API-Key": "test-key"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["result"]["status"] == "directive"
    assert body["result"]["directives"][0]["type"] == "GO_DARK"
