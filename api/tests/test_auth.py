def test_missing_api_key_rejected(client, sample_announce):
    response = client.post("/announce", json=sample_announce)
    assert response.status_code == 401
    body = response.json()
    assert body["result"]["status"] == "rejected"
    assert body["result"]["error"]["code"] == "unauthorized"


def test_invalid_api_key_rejected(client, sample_announce):
    response = client.post("/announce", json=sample_announce, headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401
    body = response.json()
    assert body["result"]["status"] == "rejected"
    assert body["result"]["error"]["code"] == "unauthorized"


def test_valid_api_key_accepts_request(client, sample_announce, auth_headers):
    response = client.post("/announce", json=sample_announce, headers=auth_headers)
    assert response.status_code == 202
    body = response.json()
    assert body["result"]["status"] == "ok"
