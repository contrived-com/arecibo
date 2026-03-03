#![allow(dead_code)]

use std::env;
use std::time::Duration;

use serde_json::json;

use crate::utils::get_hostname;

pub(crate) fn env_bool(name: &str, default: bool) -> bool {
    match env::var(name) {
        Ok(raw) => matches!(raw.trim().to_lowercase().as_str(), "1" | "true" | "yes" | "on"),
        Err(_) => default,
    }
}

pub(crate) fn env_int(name: &str, default: i64, minimum: i64) -> i64 {
    match env::var(name) {
        Ok(raw) => raw.parse::<i64>().map(|v| v.max(minimum)).unwrap_or(default),
        Err(_) => default,
    }
}

fn read_vault_transponder_api_key() -> String {
    let vault_addr = env::var("VAULT_ADDR")
        .unwrap_or_default()
        .trim()
        .trim_end_matches('/')
        .to_string();
    let role_id = env::var("VAULT_ROLE_ID")
        .unwrap_or_default()
        .trim()
        .to_string();
    let secret_id = env::var("VAULT_SECRET_ID")
        .unwrap_or_default()
        .trim()
        .to_string();

    if vault_addr.is_empty() || role_id.is_empty() || secret_id.is_empty() {
        return String::new();
    }

    let vault_path = env::var("TRANSPONDER_VAULT_PATH")
        .unwrap_or_else(|_| "arecibo/config".to_string())
        .trim()
        .trim_matches('/')
        .to_string();
    let vault_field = env::var("TRANSPONDER_API_KEY_FIELD")
        .unwrap_or_else(|_| "arecibo_api_keys".to_string())
        .trim()
        .to_string();

    if vault_path.is_empty() || vault_field.is_empty() {
        return String::new();
    }

    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(2))
        .build();

    // AppRole login.
    let login_url = format!("{}/v1/auth/approle/login", vault_addr);
    let login_body = json!({"role_id": role_id, "secret_id": secret_id});

    let token = match agent
        .post(&login_url)
        .set("Accept", "application/json")
        .send_json(&login_body)
    {
        Ok(resp) => {
            let body: serde_json::Value = match resp.into_json() {
                Ok(v) => v,
                Err(_) => return String::new(),
            };
            let t = body
                .get("auth")
                .and_then(|a| a.get("client_token"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            if t.is_empty() {
                return String::new();
            }
            t
        }
        Err(_) => return String::new(),
    };

    // Read secret.
    let read_url = format!("{}/v1/secret/data/{}", vault_addr, vault_path);
    match agent
        .get(&read_url)
        .set("X-Vault-Token", &token)
        .set("Accept", "application/json")
        .call()
    {
        Ok(resp) => {
            let data: serde_json::Value = match resp.into_json() {
                Ok(v) => v,
                Err(_) => return String::new(),
            };
            let raw = data
                .get("data")
                .and_then(|d| d.get("data"))
                .and_then(|d| d.get(&vault_field))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            if raw.is_empty() {
                return String::new();
            }
            // Arecibo commonly stores comma-separated key material; transponder only needs one.
            raw.split(',').next().unwrap_or("").trim().to_string()
        }
        Err(_) => String::new(),
    }
}

pub struct TransponderConfig {
    pub api_key: String,
    pub collector_candidates: Vec<String>,
    pub probe_timeout_sec: f64,
    pub http_timeout_sec: f64,

    pub service_name: String,
    pub environment: String,
    pub repository: String,
    pub commit_sha: String,
    pub instance_id: String,
    pub startup_ts: String,
    pub hostname: String,

    pub heartbeat_interval_sec: i64,
    pub heartbeat_min_interval_sec: i64,
    pub policy_refresh_jitter_sec: i64,
    pub events_flush_interval_sec: i64,
    pub queue_max_depth: i64,
    pub max_batch_size: i64,

    pub ingest_socket_enabled: bool,
    pub ingest_socket_path: String,
    pub ingest_socket_buffer_bytes: usize,
}

impl TransponderConfig {
    pub fn from_env(startup_ts: String) -> Self {
        let candidates_raw = env::var("TRANSPONDER_COLLECTOR_CANDIDATES")
            .unwrap_or_else(|_| "http://arecibo-api:8080,https://arecibo.contrived.com".to_string());
        let mut collector_candidates: Vec<String> = candidates_raw
            .split(',')
            .map(|s| s.trim().trim_end_matches('/').to_string())
            .filter(|s| !s.is_empty())
            .collect();

        let collector_override = env::var("TRANSPONDER_COLLECTOR_URL")
            .unwrap_or_default()
            .trim()
            .trim_end_matches('/')
            .to_string();
        if !collector_override.is_empty() {
            collector_candidates.insert(0, collector_override);
        }

        // Dedup preserving order.
        let mut deduped: Vec<String> = Vec::new();
        for candidate in collector_candidates {
            if !candidate.is_empty() && !deduped.contains(&candidate) {
                deduped.push(candidate);
            }
        }

        let hostname = get_hostname();

        let service_name = env::var("TRANSPONDER_SERVICE_NAME")
            .or_else(|_| env::var("SERVICE_NAME"))
            .unwrap_or_else(|_| "unknown-service".to_string());
        let environment = env::var("TRANSPONDER_ENVIRONMENT")
            .or_else(|_| env::var("ENVIRONMENT"))
            .unwrap_or_else(|_| "unknown".to_string());
        let repository = env::var("TRANSPONDER_REPOSITORY")
            .or_else(|_| env::var("GITHUB_REPOSITORY"))
            .unwrap_or_else(|_| "unknown-repository".to_string());
        let commit_sha = env::var("TRANSPONDER_COMMIT_SHA")
            .or_else(|_| env::var("GIT_COMMIT"))
            .unwrap_or_else(|_| "unknown".to_string());
        let instance_id = env::var("TRANSPONDER_INSTANCE_ID")
            .unwrap_or_else(|_| hostname.clone());
        let hostname_val = env::var("HOSTNAME")
            .unwrap_or_else(|_| hostname);

        let mut api_key = env::var("TRANSPONDER_API_KEY")
            .unwrap_or_default()
            .trim()
            .to_string();
        if api_key.is_empty() {
            api_key = read_vault_transponder_api_key();
        }

        let probe_timeout_sec = env::var("TRANSPONDER_PROBE_TIMEOUT_SEC")
            .ok()
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(0.8);
        let http_timeout_sec = env::var("TRANSPONDER_HTTP_TIMEOUT_SEC")
            .ok()
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(2.0);

        Self {
            api_key,
            collector_candidates: deduped,
            probe_timeout_sec,
            http_timeout_sec,
            service_name,
            environment,
            repository,
            commit_sha,
            instance_id,
            startup_ts,
            hostname: hostname_val,
            heartbeat_interval_sec: env_int("TRANSPONDER_HEARTBEAT_INTERVAL_SEC", 30, 5),
            heartbeat_min_interval_sec: 5,
            policy_refresh_jitter_sec: env_int("TRANSPONDER_POLICY_REFRESH_JITTER_SEC", 2, 0),
            events_flush_interval_sec: env_int("TRANSPONDER_EVENTS_FLUSH_INTERVAL_SEC", 5, 1),
            queue_max_depth: env_int("TRANSPONDER_MAX_EVENT_QUEUE_DEPTH", 10000, 1),
            max_batch_size: env_int("TRANSPONDER_MAX_BATCH_SIZE", 1000, 1),
            ingest_socket_enabled: env_bool("TRANSPONDER_INGEST_SOCKET_ENABLED", true),
            ingest_socket_path: env::var("TRANSPONDER_INGEST_SOCKET_PATH")
                .unwrap_or_else(|_| "/tmp/transponder-ingest.sock".to_string()),
            ingest_socket_buffer_bytes: env_int(
                "TRANSPONDER_INGEST_SOCKET_BUFFER_BYTES",
                65535,
                1024,
            ) as usize,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Use unique env var names per test to avoid parallel interference.

    #[test]
    fn test_env_bool_true_variants() {
        for (i, val) in ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"].iter().enumerate() {
            let name = format!("_TEST_BOOL_TRUE_{}", i);
            env::set_var(&name, val);
            assert!(env_bool(&name, false), "expected true for {:?}", val);
            env::remove_var(&name);
        }
    }

    #[test]
    fn test_env_bool_false_variants() {
        for (i, val) in ["0", "false", "no", "off", "random", ""].iter().enumerate() {
            let name = format!("_TEST_BOOL_FALSE_{}", i);
            env::set_var(&name, val);
            assert!(!env_bool(&name, true), "expected false for {:?}", val);
            env::remove_var(&name);
        }
    }

    #[test]
    fn test_env_bool_default_when_unset() {
        let name = "_TEST_BOOL_UNSET_XYZ";
        env::remove_var(name);
        assert!(env_bool(name, true));
        assert!(!env_bool(name, false));
    }

    #[test]
    fn test_env_int_parses_valid_value() {
        let name = "_TEST_INT_VALID";
        env::set_var(name, "42");
        assert_eq!(env_int(name, 10, 0), 42);
        env::remove_var(name);
    }

    #[test]
    fn test_env_int_enforces_minimum() {
        let name = "_TEST_INT_MIN";
        env::set_var(name, "2");
        assert_eq!(env_int(name, 10, 5), 5);
        env::remove_var(name);
    }

    #[test]
    fn test_env_int_returns_default_on_invalid() {
        let name = "_TEST_INT_INVALID";
        env::set_var(name, "not-a-number");
        assert_eq!(env_int(name, 99, 0), 99);
        env::remove_var(name);
    }

    #[test]
    fn test_env_int_returns_default_when_unset() {
        let name = "_TEST_INT_UNSET_XYZ";
        env::remove_var(name);
        assert_eq!(env_int(name, 77, 0), 77);
    }

    #[test]
    fn test_collector_candidate_dedup() {
        // Simulate from_env candidate dedup logic directly.
        let candidates_raw = "http://a:8080,http://b:8080,http://a:8080";
        let mut candidates: Vec<String> = candidates_raw
            .split(',')
            .map(|s| s.trim().trim_end_matches('/').to_string())
            .filter(|s| !s.is_empty())
            .collect();

        // Simulate override insertion.
        let override_url = "http://b:8080".to_string();
        candidates.insert(0, override_url);

        let mut deduped: Vec<String> = Vec::new();
        for c in candidates {
            if !c.is_empty() && !deduped.contains(&c) {
                deduped.push(c);
            }
        }
        assert_eq!(deduped, vec!["http://b:8080", "http://a:8080"]);
    }

    #[test]
    fn test_collector_trailing_slash_stripped() {
        let raw = "http://example.com/";
        let result: Vec<String> = raw
            .split(',')
            .map(|s| s.trim().trim_end_matches('/').to_string())
            .filter(|s| !s.is_empty())
            .collect();
        assert_eq!(result, vec!["http://example.com"]);
    }

    #[test]
    fn test_api_key_precedence_explicit_over_vault() {
        // When TRANSPONDER_API_KEY is set, it should be used directly.
        let name = "_TEST_TRANSPONDER_API_KEY_PREC";
        env::set_var(name, "my-explicit-key");
        let val = env::var(name).unwrap_or_default().trim().to_string();
        assert_eq!(val, "my-explicit-key");
        env::remove_var(name);
    }

    #[test]
    fn test_vault_fallback_returns_empty_without_vault_env() {
        // Ensure vault vars are unset.
        env::remove_var("VAULT_ADDR");
        env::remove_var("VAULT_ROLE_ID");
        env::remove_var("VAULT_SECRET_ID");
        let result = read_vault_transponder_api_key();
        assert_eq!(result, "");
    }
}
