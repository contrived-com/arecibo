def _sample_policy(service_name: str = "demo-service", environment: str = "local") -> dict:
    return {
        "policyVersion": "1.0.0",
        "serviceName": service_name,
        "environment": environment,
        "enabled": True,
        "defaultSampleRate": 1.0,
        "heartbeatIntervalSec": 30,
        "maxEventQueueDepth": 10000,
        "maxBatchSize": 1000,
        "eventOverrides": {},
        "redactionRules": [],
    }


def test_upsert_then_fetch_policy(client, auth_headers):
    write_response = client.put(
        "/policy?serviceName=demo-service&environment=local",
        headers=auth_headers,
        json=_sample_policy(),
    )
    assert write_response.status_code == 200

    response = client.get(
        "/policy?serviceName=demo-service&environment=local",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "1.0.0"
    assert body["transponderSessionId"]
    assert body["policy"]["serviceName"] == "demo-service"
    assert body["policy"]["environment"] == "local"


def test_unknown_service_environment_returns_contract_error_result(client, auth_headers):
    response = client.get(
        "/policy?serviceName=unknown-service&environment=local",
        headers=auth_headers,
    )
    assert response.status_code == 404
    body = response.json()
    assert body["result"]["status"] == "rejected"
    assert body["result"]["error"]["code"] == "policy_not_found"


def test_delete_policy(client, auth_headers):
    client.put(
        "/policy?serviceName=demo-service&environment=local",
        headers=auth_headers,
        json=_sample_policy(),
    )
    delete_response = client.delete(
        "/policy?serviceName=demo-service&environment=local",
        headers=auth_headers,
    )
    assert delete_response.status_code == 200

    response = client.get(
        "/policy?serviceName=demo-service&environment=local",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_put_policy_rejects_mismatched_body_and_query(
    client,
    auth_headers,
):
    payload = _sample_policy(service_name="other-service", environment="local")
    response = client.put(
        "/policy?serviceName=demo-service&environment=local",
        headers=auth_headers,
        json=payload,
    )
    assert response.status_code == 400
    body = response.json()
    assert body["result"]["error"]["code"] == "policy_mismatch"
