use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::client::CollectorClient;
use crate::config::TransponderConfig;
use crate::ingest::{IngestDatagramServer, IngestQueue};
use crate::metrics::MetricSampler;
use crate::model::{Directive, PolicyState, TransponderCounters};
use crate::utils::{new_event_id, utc_now};

pub struct TransponderRuntime {
    config: TransponderConfig,
    policy: PolicyState,
    go_dark: bool,
    selected_collector: String,
    counters: Arc<Mutex<TransponderCounters>>,
    queue: Arc<IngestQueue>,
    ingest_server: Option<IngestDatagramServer>,
    stop: Arc<AtomicBool>,
    started_monotonic: Instant,
    metric_sampler: MetricSampler,
}

impl TransponderRuntime {
    pub fn new(config: TransponderConfig) -> Self {
        let queue = Arc::new(IngestQueue::new(config.queue_max_depth as usize));
        Self {
            config,
            policy: PolicyState::default(),
            go_dark: false,
            selected_collector: String::new(),
            counters: Arc::new(Mutex::new(TransponderCounters::default())),
            queue,
            ingest_server: None,
            stop: Arc::new(AtomicBool::new(false)),
            started_monotonic: Instant::now(),
            metric_sampler: MetricSampler::new(),
        }
    }

    fn install_signal_handlers(&self) {
        let stop = self.stop.clone();
        ctrlc::set_handler(move || {
            stop.store(true, Ordering::Relaxed);
        })
        .expect("failed to set signal handler");
    }

    pub fn run(&mut self) {
        crate::utils::setup_logging();
        self.install_signal_handlers();
        self.bootstrap();

        if self.config.ingest_socket_enabled {
            let mut server = IngestDatagramServer::new(
                self.config.ingest_socket_path.clone(),
                self.config.ingest_socket_buffer_bytes,
                self.queue.clone(),
                self.counters.clone(),
            );
            server.start();
            log::info!(
                "local ingest socket listening at {}",
                self.config.ingest_socket_path
            );
            self.ingest_server = Some(server);
        }

        let mut next_heartbeat_at = Instant::now();
        let mut next_flush_at =
            Instant::now() + Duration::from_secs(self.config.events_flush_interval_sec as u64);
        let policy_refresh_delay = self
            .config
            .heartbeat_min_interval_sec
            .max(self.policy.ttl_sec - self.config.policy_refresh_jitter_sec);
        let mut next_policy_refresh_at =
            Instant::now() + Duration::from_secs(policy_refresh_delay.max(1) as u64);

        while !self.stop.load(Ordering::Relaxed) {
            let now = Instant::now();

            if now >= next_heartbeat_at {
                self.send_heartbeat();
                let interval = self
                    .config
                    .heartbeat_min_interval_sec
                    .max(self.policy.heartbeat_interval_sec);
                next_heartbeat_at = now + Duration::from_secs(interval as u64);
            }

            if now >= next_policy_refresh_at {
                self.refresh_policy();
                let delay = self
                    .config
                    .heartbeat_min_interval_sec
                    .max(self.policy.ttl_sec - self.config.policy_refresh_jitter_sec);
                next_policy_refresh_at = now + Duration::from_secs(delay.max(1) as u64);
            }

            if now >= next_flush_at {
                self.flush_events();
                next_flush_at = now + Duration::from_secs(self.config.events_flush_interval_sec as u64);
            }

            std::thread::sleep(Duration::from_millis(200));
        }

        if let Some(ref mut server) = self.ingest_server {
            server.stop();
        }
    }

    fn bootstrap(&mut self) {
        if !self.config.identity_is_explicit() {
            log::warn!(
                "transponder identity unresolved (serviceName='{}', environment='{}'); continuing with degraded identity",
                self.config.service_name,
                self.config.environment
            );
        }

        if self.config.collector_candidates.is_empty() {
            log::warn!("no collector candidates configured; transponder remains local-only");
            return;
        }
        if self.config.api_key.is_empty() {
            log::warn!("TRANSPONDER_API_KEY missing; outbound API calls likely rejected");
        }

        let candidates = self.config.collector_candidates.clone();
        for candidate in &candidates {
            let client = CollectorClient::new(
                candidate,
                &self.config.api_key,
                self.config.probe_timeout_sec,
            );
            let (status, body) = client.health();
            if status != 200 {
                continue;
            }
            let ok = match &body {
                Some(Value::Object(map)) => match map.get("ok") {
                    Some(Value::Bool(b)) => *b,
                    Some(Value::Null) | None => false,
                    Some(_) => true,
                },
                _ => false,
            };
            if !ok {
                continue;
            }
            self.selected_collector = candidate.clone();
            log::info!("selected collector={}", candidate);
            break;
        }

        if self.selected_collector.is_empty() {
            log::warn!("collector probe failed; transponder will retry opportunistically");
            return;
        }

        self.announce();
        self.refresh_policy();
    }

    fn client(&self) -> Option<CollectorClient> {
        if self.selected_collector.is_empty() {
            return None;
        }
        Some(CollectorClient::new(
            &self.selected_collector,
            &self.config.api_key,
            self.config.http_timeout_sec,
        ))
    }

    fn identity(&self) -> Value {
        let mut identity = serde_json::Map::new();
        identity.insert(
            "serviceName".to_string(),
            Value::String(self.config.service_name.clone()),
        );
        identity.insert(
            "environment".to_string(),
            Value::String(self.config.environment.clone()),
        );
        identity.insert(
            "repository".to_string(),
            Value::String(self.config.repository.clone()),
        );
        identity.insert(
            "commitSha".to_string(),
            Value::String(self.config.commit_sha.clone()),
        );
        identity.insert(
            "instanceId".to_string(),
            Value::String(self.config.instance_id.clone()),
        );
        identity.insert(
            "startupTs".to_string(),
            Value::String(self.config.startup_ts.clone()),
        );
        identity.insert(
            "hostname".to_string(),
            Value::String(self.config.hostname.clone()),
        );
        if !self.config.commit_url.is_empty() {
            identity.insert(
                "commitUrl".to_string(),
                Value::String(self.config.commit_url.clone()),
            );
        }
        if !self.config.workflow_run_url.is_empty() {
            identity.insert(
                "workflowRunUrl".to_string(),
                Value::String(self.config.workflow_run_url.clone()),
            );
        }
        if !self.config.image_ref.is_empty() {
            identity.insert(
                "imageRef".to_string(),
                Value::String(self.config.image_ref.clone()),
            );
        }
        Value::Object(identity)
    }

    fn announce(&mut self) {
        let client = match self.client() {
            Some(c) if !self.go_dark => c,
            _ => return,
        };
        let software_version = format!(
            "arecibo-transponder/{} (rust {})",
            env!("CARGO_PKG_VERSION"),
            env!("RUSTC_VERSION")
        );
        let payload = json!({
            "schemaVersion": "1.0.0",
            "eventType": "announce",
            "eventId": new_event_id("announce"),
            "sentAt": utc_now(),
            "identity": self.identity(),
            "runtime": {
                "transponderPid": std::process::id(),
                "softwareVersion": software_version,
                "transponderVersion": env!("CARGO_PKG_VERSION"),
            },
        });
        let (status, body) = client.announce(&payload);
        if status == 202 {
            if let Some(ref b) = body {
                self.apply_directives(b);
            }
            log::info!("announce accepted");
        } else {
            log::warn!(
                "announce failed status={} response={}",
                status,
                body.as_ref()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| "<empty>".to_string())
            );
        }
    }

    fn refresh_policy(&mut self) {
        let client = match self.client() {
            Some(c) if !self.go_dark => c,
            _ => return,
        };
        let (status, body) = client.policy(&self.config.service_name, &self.config.environment);
        if status == 200 {
            if let Some(Value::Object(map)) = body {
                let policy_obj = map.get("policy").and_then(|v| v.as_object());

                self.policy.session_id = map
                    .get("transponderSessionId")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                self.policy.ttl_sec = map
                    .get("ttlSec")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(self.policy.ttl_sec);

                if let Some(policy) = policy_obj {
                    self.policy.policy_version = policy
                        .get("policyVersion")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    self.policy.enabled = policy
                        .get("enabled")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(true);
                    self.policy.heartbeat_interval_sec = policy
                        .get("heartbeatIntervalSec")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(self.policy.heartbeat_interval_sec);
                    self.policy.max_batch_size = policy
                        .get("maxBatchSize")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(self.config.max_batch_size);
                    self.policy.max_transponder_silence_sec = policy
                        .get("maxTransponderSilenceSec")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(self.policy.max_transponder_silence_sec)
                        .max(0);
                }

                log::info!(
                    "policy loaded version={} heartbeat={}s max_silence={}s session={}",
                    self.policy.policy_version,
                    self.policy.heartbeat_interval_sec,
                    self.policy.max_transponder_silence_sec,
                    self.policy.session_id,
                );
            }
            return;
        }
        if status == 404 {
            log::warn!(
                "policy not found for {}/{}",
                self.config.service_name,
                self.config.environment
            );
            return;
        }
        log::warn!("policy fetch failed status={}", status);
    }

    fn send_heartbeat(&mut self) {
        let client = match self.client() {
            Some(c) if !self.go_dark => c,
            _ => return,
        };
        let uptime = self.started_monotonic.elapsed().as_secs();
        let sampled = self.metric_sampler.sample();
        let payload = {
            let c = self.counters.lock().unwrap();
            let mut status = serde_json::Map::new();
            status.insert(
                "transponderUptimeSec".to_string(),
                json!(uptime),
            );
            status.insert(
                "maxEventQueueDepthSinceLastHeartbeat".to_string(),
                json!(c.max_event_queue_depth_since_last_heartbeat),
            );
            status.insert(
                "eventsReceivedTotal".to_string(),
                json!(c.events_received_total),
            );
            status.insert(
                "eventsSentTotal".to_string(),
                json!(c.events_sent_total),
            );
            status.insert(
                "eventsDroppedTotal".to_string(),
                json!(c.events_dropped_total),
            );
            status.insert(
                "eventsDroppedByQueueSizeSinceLastHeartbeat".to_string(),
                json!(c.events_dropped_by_queue_size_since_last_heartbeat),
            );
            status.insert(
                "eventsDroppedByPolicySinceLastHeartbeat".to_string(),
                json!(c.events_dropped_by_policy_since_last_heartbeat),
            );
            status.insert(
                "transponderRssBytes".to_string(),
                json!(sampled.transponder_rss_bytes.unwrap_or(0)),
            );
            status.insert(
                "goDark".to_string(),
                json!(self.go_dark),
            );
            status.insert(
                "policyVersion".to_string(),
                json!(&self.policy.policy_version),
            );

            if let Some(v) = sampled.transponder_cpu_user_sec {
                status.insert("transponderCpuUserSec".to_string(), json!(v));
            }
            if let Some(v) = sampled.transponder_cpu_system_sec {
                status.insert("transponderCpuSystemSec".to_string(), json!(v));
            }
            if let Some(v) = sampled.primary_app_pid {
                status.insert("primaryAppPid".to_string(), json!(v));
            }
            if let Some(v) = sampled.primary_app_rss_bytes {
                status.insert("primaryAppRssBytes".to_string(), json!(v));
            }
            if let Some(v) = sampled.container_memory_current_bytes {
                status.insert("containerMemoryCurrentBytes".to_string(), json!(v));
            }
            if let Some(v) = sampled.container_memory_max_bytes {
                status.insert("containerMemoryMaxBytes".to_string(), json!(v));
            }
            if let Some(v) = sampled.container_rx_bytes_since_last_heartbeat {
                status.insert("containerRxBytesSinceLastHeartbeat".to_string(), json!(v));
            }
            if let Some(v) = sampled.container_tx_bytes_since_last_heartbeat {
                status.insert("containerTxBytesSinceLastHeartbeat".to_string(), json!(v));
            }

            json!({
                "schemaVersion": "1.0.0",
                "eventType": "heartbeat",
                "eventId": new_event_id("heartbeat"),
                "sentAt": utc_now(),
                "identity": self.identity(),
                "status": status,
            })
        };

        let (status, body) = client.heartbeat(&payload);

        {
            let mut c = self.counters.lock().unwrap();
            c.reset_heartbeat_window();
        }

        if status == 202 {
            if let Some(ref b) = body {
                self.apply_directives(b);
            }
            return;
        }
        log::warn!(
            "heartbeat failed status={} response={}",
            status,
            body.as_ref()
                .map(|v| v.to_string())
                .unwrap_or_else(|| "<empty>".to_string())
        );
    }

    fn flush_events(&mut self) {
        if self.go_dark {
            return;
        }
        if !self.policy.enabled {
            let size = self.queue.size();
            if size > 0 {
                {
                    let mut c = self.counters.lock().unwrap();
                    c.events_dropped_total += size as i64;
                    c.events_dropped_by_policy_since_last_heartbeat += size as i64;
                }
                let _ = self.queue.pop_batch(size);
            }
            return;
        }

        let client = match self.client() {
            Some(c) => c,
            None => return,
        };

        let limit = 1.max(self.policy.max_batch_size.min(self.config.max_batch_size)) as usize;
        let queue_size = self.queue.size();
        if queue_size == 0 {
            return;
        }
        if self.policy.max_transponder_silence_sec > 0 && queue_size < limit {
            let oldest_age = self.queue.oldest_age_sec().unwrap_or(0);
            if oldest_age < self.policy.max_transponder_silence_sec as u64 {
                return;
            }
        }
        let batch = self.queue.pop_batch(limit);
        if batch.is_empty() {
            return;
        }
        if self.policy.session_id.is_empty() {
            log::warn!("no session id; dropping {} events", batch.len());
            let mut c = self.counters.lock().unwrap();
            c.events_dropped_total += batch.len() as i64;
            c.events_dropped_by_policy_since_last_heartbeat += batch.len() as i64;
            return;
        }

        let batch_len = batch.len();
        let payload = json!({
            "schemaVersion": "1.0.0",
            "batchId": new_event_id("batch"),
            "transponderSessionId": self.policy.session_id,
            "sentAt": utc_now(),
            "events": batch,
        });
        let (status, body) = client.events_batch(&payload);
        if status == 202 {
            let mut c = self.counters.lock().unwrap();
            c.events_sent_total += batch_len as i64;
            if let Some(ref b) = body {
                drop(c);
                self.apply_directives(b);
            }
            return;
        }
        log::warn!(
            "events batch failed status={} count={}",
            status,
            batch_len
        );
        // Re-queue events on failure. Extract events from the payload.
        if let Value::Object(map) = payload {
            if let Some(Value::Array(events)) = map.into_iter().find(|(k, _)| k == "events").map(|(_, v)| v) {
                for event in events {
                    self.queue.push(event, &self.counters);
                }
            }
        }
    }

    fn parse_directives(body: &Value) -> Vec<Directive> {
        let result = match body.get("result") {
            Some(r) => r,
            None => return vec![],
        };
        let directives_raw = match result.get("directives") {
            Some(d) => d,
            None => return vec![],
        };
        let arr = match directives_raw.as_array() {
            Some(a) => a,
            None => return vec![],
        };

        let mut parsed = Vec::new();
        for item in arr {
            let obj = match item.as_object() {
                Some(o) => o,
                None => continue,
            };
            let directive_type = obj
                .get("type")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            if directive_type.is_empty() {
                continue;
            }
            parsed.push(Directive {
                directive_type,
                value: obj.get("value").cloned(),
                ttl_sec: obj.get("ttlSec").and_then(|v| v.as_i64()),
            });
        }
        parsed
    }

    fn apply_directives(&mut self, body: &Value) {
        let directives = Self::parse_directives(body);
        for directive in directives {
            match directive.directive_type.as_str() {
                "GO_DARK" => {
                    log::warn!("received GO_DARK directive; suppressing outbound sends");
                    self.go_dark = true;
                }
                "RESUME" => {
                    log::info!("received RESUME directive");
                    self.go_dark = false;
                }
                "REFRESH_POLICY" => {
                    log::info!("received REFRESH_POLICY directive");
                    self.refresh_policy();
                }
                "SET_HEARTBEAT_INTERVAL" => {
                    if let Some(ref val) = directive.value {
                        match val.as_i64().or_else(|| {
                            val.as_str().and_then(|s| s.parse::<i64>().ok())
                        }) {
                            Some(interval) => {
                                self.policy.heartbeat_interval_sec =
                                    interval.max(self.config.heartbeat_min_interval_sec);
                                log::info!(
                                    "heartbeat interval set to {}s",
                                    self.policy.heartbeat_interval_sec
                                );
                            }
                            None => {
                                log::warn!(
                                    "invalid SET_HEARTBEAT_INTERVAL value: {:?}",
                                    directive.value
                                );
                            }
                        }
                    } else {
                        log::warn!(
                            "invalid SET_HEARTBEAT_INTERVAL value: {:?}",
                            directive.value
                        );
                    }
                }
                "FLUSH_STATS" => {
                    let c = self.counters.lock().unwrap();
                    log::info!(
                        "FLUSH_STATS requested received={} sent={} dropped={} queue={}",
                        c.events_received_total,
                        c.events_sent_total,
                        c.events_dropped_total,
                        self.queue.size(),
                    );
                }
                other => {
                    log::info!("ignoring unsupported directive type={}", other);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_directives_valid() {
        let body = json!({
            "result": {
                "directives": [
                    {"type": "GO_DARK"},
                    {"type": "RESUME", "value": null},
                    {"type": "SET_HEARTBEAT_INTERVAL", "value": 15, "ttlSec": 300}
                ]
            }
        });
        let directives = TransponderRuntime::parse_directives(&body);
        assert_eq!(directives.len(), 3);
        assert_eq!(directives[0].directive_type, "GO_DARK");
        assert!(directives[0].value.is_none());
        assert_eq!(directives[1].directive_type, "RESUME");
        assert_eq!(directives[2].directive_type, "SET_HEARTBEAT_INTERVAL");
        assert_eq!(directives[2].value.as_ref().unwrap().as_i64(), Some(15));
        assert_eq!(directives[2].ttl_sec, Some(300));
    }

    #[test]
    fn test_parse_directives_empty_result() {
        let body = json!({"result": {}});
        let directives = TransponderRuntime::parse_directives(&body);
        assert!(directives.is_empty());
    }

    #[test]
    fn test_parse_directives_no_result_key() {
        let body = json!({"status": "ok"});
        let directives = TransponderRuntime::parse_directives(&body);
        assert!(directives.is_empty());
    }

    #[test]
    fn test_parse_directives_skips_missing_type() {
        let body = json!({
            "result": {
                "directives": [
                    {"value": "no-type-field"},
                    {"type": "", "value": "empty-type"},
                    {"type": "VALID"}
                ]
            }
        });
        let directives = TransponderRuntime::parse_directives(&body);
        assert_eq!(directives.len(), 1);
        assert_eq!(directives[0].directive_type, "VALID");
    }

    #[test]
    fn test_parse_directives_non_array() {
        let body = json!({"result": {"directives": "not-an-array"}});
        let directives = TransponderRuntime::parse_directives(&body);
        assert!(directives.is_empty());
    }

    fn make_test_config() -> TransponderConfig {
        TransponderConfig {
            api_key: "test-key".to_string(),
            collector_candidates: vec!["http://localhost:8080".to_string()],
            probe_timeout_sec: 0.5,
            http_timeout_sec: 1.0,
            service_name: "test-svc".to_string(),
            environment: "test".to_string(),
            repository: "test/repo".to_string(),
            commit_sha: "abc123".to_string(),
            commit_url: "https://github.com/test/repo/commit/abc123".to_string(),
            workflow_run_url: "https://github.com/test/repo/actions/runs/1".to_string(),
            image_ref: "ghcr.io/contrived-com/arecibo-transponder:abc123".to_string(),
            instance_id: "inst-1".to_string(),
            startup_ts: "2026-01-01T00:00:00Z".to_string(),
            hostname: "test-host".to_string(),
            heartbeat_interval_sec: 30,
            heartbeat_min_interval_sec: 5,
            policy_refresh_jitter_sec: 2,
            events_flush_interval_sec: 5,
            queue_max_depth: 100,
            max_batch_size: 50,
            ingest_socket_enabled: false,
            ingest_socket_path: "/tmp/test.sock".to_string(),
            ingest_socket_buffer_bytes: 65535,
        }
    }

    #[test]
    fn test_apply_go_dark_and_resume() {
        let config = make_test_config();
        let mut rt = TransponderRuntime::new(config);
        assert!(!rt.go_dark);

        // Apply GO_DARK.
        let body = json!({"result": {"directives": [{"type": "GO_DARK"}]}});
        rt.apply_directives(&body);
        assert!(rt.go_dark);

        // Apply RESUME.
        let body = json!({"result": {"directives": [{"type": "RESUME"}]}});
        rt.apply_directives(&body);
        assert!(!rt.go_dark);
    }

    #[test]
    fn test_apply_set_heartbeat_interval() {
        let config = make_test_config();
        let mut rt = TransponderRuntime::new(config);
        assert_eq!(rt.policy.heartbeat_interval_sec, 30);

        // SET_HEARTBEAT_INTERVAL with value above minimum.
        let body = json!({"result": {"directives": [{"type": "SET_HEARTBEAT_INTERVAL", "value": 60}]}});
        rt.apply_directives(&body);
        assert_eq!(rt.policy.heartbeat_interval_sec, 60);
    }

    #[test]
    fn test_apply_set_heartbeat_interval_enforces_minimum() {
        let config = make_test_config();
        let mut rt = TransponderRuntime::new(config);

        // SET_HEARTBEAT_INTERVAL with value below minimum (5).
        let body = json!({"result": {"directives": [{"type": "SET_HEARTBEAT_INTERVAL", "value": 1}]}});
        rt.apply_directives(&body);
        assert_eq!(rt.policy.heartbeat_interval_sec, 5);
    }

    #[test]
    fn test_apply_set_heartbeat_interval_string_value() {
        let config = make_test_config();
        let mut rt = TransponderRuntime::new(config);

        // SET_HEARTBEAT_INTERVAL with string value.
        let body = json!({"result": {"directives": [{"type": "SET_HEARTBEAT_INTERVAL", "value": "45"}]}});
        rt.apply_directives(&body);
        assert_eq!(rt.policy.heartbeat_interval_sec, 45);
    }

    #[test]
    fn test_flush_events_policy_disabled_drops_and_counts() {
        let config = make_test_config();
        let mut rt = TransponderRuntime::new(config);
        rt.policy.enabled = false;

        // Push some events.
        rt.queue.push(json!({"i": 0}), &rt.counters);
        rt.queue.push(json!({"i": 1}), &rt.counters);
        assert_eq!(rt.queue.size(), 2);

        rt.flush_events();

        // Queue should be empty, events dropped by policy.
        assert_eq!(rt.queue.size(), 0);
        let c = rt.counters.lock().unwrap();
        assert_eq!(c.events_dropped_total, 2);
        assert_eq!(c.events_dropped_by_policy_since_last_heartbeat, 2);
    }

    #[test]
    fn test_flush_events_go_dark_skips_flush() {
        let config = make_test_config();
        let mut rt = TransponderRuntime::new(config);
        rt.go_dark = true;

        // Push events.
        rt.queue.push(json!({"i": 0}), &rt.counters);
        rt.flush_events();

        // Events should remain in queue (go_dark skips the whole method).
        assert_eq!(rt.queue.size(), 1);
    }

    #[test]
    fn test_flush_events_no_session_id_drops() {
        let config = make_test_config();
        let mut rt = TransponderRuntime::new(config);
        rt.selected_collector = "http://localhost:8080".to_string();
        rt.policy.enabled = true;
        rt.policy.session_id = String::new(); // No session.

        rt.queue.push(json!({"i": 0}), &rt.counters);
        rt.flush_events();

        // Events should be dropped because no session_id.
        assert_eq!(rt.queue.size(), 0);
        let c = rt.counters.lock().unwrap();
        assert_eq!(c.events_dropped_total, 1);
        assert_eq!(c.events_dropped_by_policy_since_last_heartbeat, 1);
    }

    #[test]
    fn test_bootstrap_no_candidates_remains_local() {
        let mut config = make_test_config();
        config.collector_candidates = vec![];
        let mut rt = TransponderRuntime::new(config);
        rt.bootstrap();
        assert_eq!(rt.selected_collector, "");
    }

    #[test]
    fn test_flush_events_waits_for_silence_threshold() {
        let config = make_test_config();
        let mut rt = TransponderRuntime::new(config);
        rt.selected_collector = "http://localhost:8080".to_string();
        rt.policy.enabled = true;
        rt.policy.session_id = "session-123".to_string();
        rt.policy.max_batch_size = 1000;
        rt.policy.max_transponder_silence_sec = 300;

        rt.queue.push(json!({"i": 0}), &rt.counters);
        rt.flush_events();

        // Queue remains because max silence threshold not reached yet.
        assert_eq!(rt.queue.size(), 1);
    }
}
