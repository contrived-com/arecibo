def test_known_service_environment_returns_policy(client, auth_headers):
    response = client.get("/policy?serviceName=demo-service&environment=local", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "1.0.0"
    assert body["agentSessionId"]
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
