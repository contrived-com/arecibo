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
