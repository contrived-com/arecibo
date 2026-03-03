use serde_json::Value;

#[allow(dead_code)]
pub struct Directive {
    pub directive_type: String,
    pub value: Option<Value>,
    pub ttl_sec: Option<i64>,
}

pub struct PolicyState {
    pub session_id: String,
    pub policy_version: String,
    pub enabled: bool,
    pub heartbeat_interval_sec: i64,
    pub max_batch_size: i64,
    pub ttl_sec: i64,
}

impl Default for PolicyState {
    fn default() -> Self {
        Self {
            session_id: String::new(),
            policy_version: String::new(),
            enabled: true,
            heartbeat_interval_sec: 30,
            max_batch_size: 1000,
            ttl_sec: 60,
        }
    }
}

#[derive(Default)]
pub struct TransponderCounters {
    pub events_received_total: i64,
    pub events_sent_total: i64,
    pub events_dropped_total: i64,
    pub events_dropped_by_queue_size_since_last_heartbeat: i64,
    pub events_dropped_by_policy_since_last_heartbeat: i64,
    pub max_event_queue_depth_since_last_heartbeat: i64,
}

impl TransponderCounters {
    pub fn reset_heartbeat_window(&mut self) {
        self.events_dropped_by_queue_size_since_last_heartbeat = 0;
        self.events_dropped_by_policy_since_last_heartbeat = 0;
        self.max_event_queue_depth_since_last_heartbeat = 0;
    }
}
