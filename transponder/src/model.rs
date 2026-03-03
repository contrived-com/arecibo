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
    pub max_transponder_silence_sec: i64,
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
            max_transponder_silence_sec: 0,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_counters_default() {
        let c = TransponderCounters::default();
        assert_eq!(c.events_received_total, 0);
        assert_eq!(c.events_sent_total, 0);
        assert_eq!(c.events_dropped_total, 0);
        assert_eq!(c.events_dropped_by_queue_size_since_last_heartbeat, 0);
        assert_eq!(c.events_dropped_by_policy_since_last_heartbeat, 0);
        assert_eq!(c.max_event_queue_depth_since_last_heartbeat, 0);
    }

    #[test]
    fn test_counters_reset_heartbeat_window() {
        let mut c = TransponderCounters::default();
        c.events_received_total = 100;
        c.events_sent_total = 50;
        c.events_dropped_total = 10;
        c.events_dropped_by_queue_size_since_last_heartbeat = 5;
        c.events_dropped_by_policy_since_last_heartbeat = 3;
        c.max_event_queue_depth_since_last_heartbeat = 200;

        c.reset_heartbeat_window();

        // Totals should be preserved.
        assert_eq!(c.events_received_total, 100);
        assert_eq!(c.events_sent_total, 50);
        assert_eq!(c.events_dropped_total, 10);
        // Window counters should be reset.
        assert_eq!(c.events_dropped_by_queue_size_since_last_heartbeat, 0);
        assert_eq!(c.events_dropped_by_policy_since_last_heartbeat, 0);
        assert_eq!(c.max_event_queue_depth_since_last_heartbeat, 0);
    }

    #[test]
    fn test_policy_state_defaults() {
        let p = PolicyState::default();
        assert_eq!(p.session_id, "");
        assert_eq!(p.policy_version, "");
        assert!(p.enabled);
        assert_eq!(p.heartbeat_interval_sec, 30);
        assert_eq!(p.max_batch_size, 1000);
        assert_eq!(p.max_transponder_silence_sec, 0);
        assert_eq!(p.ttl_sec, 60);
    }
}
