use std::time::Duration;

use serde_json::Value;

fn url_encode(s: &str) -> String {
    let mut result = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                result.push(b as char);
            }
            _ => {
                result.push_str(&format!("%{:02X}", b));
            }
        }
    }
    result
}

pub struct CollectorClient {
    base_url: String,
    api_key: String,
    agent: ureq::Agent,
}

impl CollectorClient {
    pub fn new(base_url: &str, api_key: &str, timeout_sec: f64) -> Self {
        let agent = ureq::AgentBuilder::new()
            .timeout(Duration::from_secs_f64(timeout_sec))
            .build();
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key: api_key.to_string(),
            agent,
        }
    }

    fn request(
        &self,
        method: &str,
        path: &str,
        payload: Option<&Value>,
        query: Option<&[(&str, &str)]>,
    ) -> (u16, Option<Value>) {
        let mut url = format!("{}{}", self.base_url, path);
        if let Some(params) = query {
            let qs: Vec<String> = params
                .iter()
                .map(|(k, v)| format!("{}={}", url_encode(k), url_encode(v)))
                .collect();
            if !qs.is_empty() {
                url = format!("{}?{}", url, qs.join("&"));
            }
        }

        let req = self.agent.request(method, &url).set("Accept", "application/json");
        let req = if !self.api_key.is_empty() {
            req.set("X-API-Key", &self.api_key)
        } else {
            req
        };

        let result = if let Some(body) = payload {
            req.send_json(body)
        } else {
            req.call()
        };

        match result {
            Ok(resp) => {
                let status = resp.status();
                let body_str = resp.into_string().unwrap_or_default();
                let body = if body_str.trim().is_empty() {
                    None
                } else {
                    serde_json::from_str(&body_str).ok()
                };
                (status, body)
            }
            Err(ureq::Error::Status(code, resp)) => {
                let body_str = resp.into_string().unwrap_or_default();
                let body = if body_str.trim().is_empty() {
                    None
                } else {
                    serde_json::from_str(&body_str).ok()
                };
                (code, body)
            }
            Err(_) => (0, None),
        }
    }

    pub fn health(&self) -> (u16, Option<Value>) {
        self.request("GET", "/health", None, None)
    }

    pub fn announce(&self, payload: &Value) -> (u16, Option<Value>) {
        self.request("POST", "/announce", Some(payload), None)
    }

    pub fn policy(&self, service_name: &str, environment: &str) -> (u16, Option<Value>) {
        self.request(
            "GET",
            "/policy",
            None,
            Some(&[("serviceName", service_name), ("environment", environment)]),
        )
    }

    pub fn heartbeat(&self, payload: &Value) -> (u16, Option<Value>) {
        self.request("POST", "/heartbeat", Some(payload), None)
    }

    pub fn events_batch(&self, payload: &Value) -> (u16, Option<Value>) {
        self.request("POST", "/events:batch", Some(payload), None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_url_encode_passthrough() {
        assert_eq!(url_encode("hello"), "hello");
        assert_eq!(url_encode("abc-123_foo.bar~baz"), "abc-123_foo.bar~baz");
    }

    #[test]
    fn test_url_encode_special_chars() {
        assert_eq!(url_encode("hello world"), "hello%20world");
        assert_eq!(url_encode("a+b=c&d"), "a%2Bb%3Dc%26d");
        assert_eq!(url_encode("my-service/prod"), "my-service%2Fprod");
    }

    #[test]
    fn test_client_construction() {
        let client = CollectorClient::new("http://localhost:8080/", "my-key", 2.0);
        // Trailing slash should be stripped.
        assert_eq!(client.base_url, "http://localhost:8080");
        assert_eq!(client.api_key, "my-key");
    }

    #[test]
    fn test_client_connection_failure_returns_zero() {
        // Connecting to a port that won't respond should return (0, None).
        let client = CollectorClient::new("http://127.0.0.1:1", "key", 0.5);
        let (status, body) = client.health();
        assert_eq!(status, 0);
        assert!(body.is_none());
    }
}
