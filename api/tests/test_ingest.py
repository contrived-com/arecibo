def test_invalid_timestamp_rejected_for_announce(client, auth_headers, sample_announce):
    sample_announce["sentAt"] = "2026-02-26T12:00:01+00:00"
    response = client.post("/announce", json=sample_announce, headers=auth_headers)
    assert response.status_code == 400
    body = response.json()
    assert body["result"]["status"] == "rejected"
    assert body["result"]["error"]["code"] == "validation_error"


def test_missing_required_field_rejected(client, auth_headers, sample_heartbeat):
    del sample_heartbeat["status"]["eventsSentTotal"]
    response = client.post("/heartbeat", json=sample_heartbeat, headers=auth_headers)
    assert response.status_code == 400
    body = response.json()
    assert body["result"]["status"] == "rejected"
    assert body["result"]["error"]["code"] == "validation_error"


def test_events_batch_accepts_valid_payload(client, auth_headers, sample_events_batch):
    response = client.post("/events:batch", json=sample_events_batch, headers=auth_headers)
    assert response.status_code == 202
    body = response.json()
    assert body["result"]["status"] == "ok"


def test_events_batch_rejects_oversized_payload(client, auth_headers, sample_events_batch):
    sample_events_batch["events"] = sample_events_batch["events"] * 1001
    response = client.post("/events:batch", json=sample_events_batch, headers=auth_headers)
    assert response.status_code == 413
    body = response.json()
    assert body["result"]["status"] == "rejected"
    assert body["result"]["error"]["code"] == "batch_too_large"
