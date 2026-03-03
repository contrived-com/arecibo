use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::client::CollectorClient;
use crate::config::TransponderConfig;
use crate::ingest::{IngestDatagramServer, IngestQueue};
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
        json!({
            "serviceName": self.config.service_name,
            "environment": self.config.environment,
            "repository": self.config.repository,
            "commitSha": self.config.commit_sha,
            "instanceId": self.config.instance_id,
            "startupTs": self.config.startup_ts,
            "hostname": self.config.hostname,
        })
    }

    fn announce(&mut self) {
        let client = match self.client() {
            Some(c) if !self.go_dark => c,
            _ => return,
        };
        let payload = json!({
            "schemaVersion": "1.0.0",
            "eventType": "announce",
            "eventId": new_event_id("announce"),
            "sentAt": utc_now(),
            "identity": self.identity(),
            "runtime": {
                "transponderPid": std::process::id(),
                "transponderVersion": env!("CARGO_PKG_VERSION"),
                "rustVersion": env!("RUSTC_VERSION"),
            },
        });
        let (status, body) = client.announce(&payload);
        if status == 202 {
            if let Some(ref b) = body {
                self.apply_directives(b);
            }
            log::info!("announce accepted");
        } else {
            log::warn!("announce failed status={}", status);
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
                }

                log::info!(
                    "policy loaded version={} heartbeat={}s session={}",
                    self.policy.policy_version,
                    self.policy.heartbeat_interval_sec,
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
        let payload = {
            let c = self.counters.lock().unwrap();
            json!({
                "schemaVersion": "1.0.0",
                "eventType": "heartbeat",
                "eventId": new_event_id("heartbeat"),
                "sentAt": utc_now(),
                "identity": self.identity(),
                "status": {
                    "transponderUptimeSec": uptime,
                    "maxEventQueueDepthSinceLastHeartbeat": c.max_event_queue_depth_since_last_heartbeat,
                    "eventsReceivedTotal": c.events_received_total,
                    "eventsSentTotal": c.events_sent_total,
                    "eventsDroppedTotal": c.events_dropped_total,
                    "eventsDroppedByQueueSizeSinceLastHeartbeat": c.events_dropped_by_queue_size_since_last_heartbeat,
                    "eventsDroppedByPolicySinceLastHeartbeat": c.events_dropped_by_policy_since_last_heartbeat,
                    "transponderRssBytes": 0,
                    "goDark": self.go_dark,
                    "policyVersion": &self.policy.policy_version,
                },
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
        log::warn!("heartbeat failed status={}", status);
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
