use std::collections::VecDeque;
use std::fs;
use std::io;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixDatagram;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::Value;

use crate::model::TransponderCounters;
use crate::utils::utc_now;

pub struct IngestQueue {
    max_depth: usize,
    items: Mutex<VecDeque<Value>>,
}

impl IngestQueue {
    pub fn new(max_depth: usize) -> Self {
        Self {
            max_depth,
            items: Mutex::new(VecDeque::new()),
        }
    }

    pub fn push(&self, item: Value, counters: &Mutex<TransponderCounters>) {
        let (dropped, new_len) = {
            let mut items = self.items.lock().unwrap();
            let dropped = if items.len() >= self.max_depth {
                items.pop_front();
                true
            } else {
                false
            };
            items.push_back(item);
            (dropped, items.len())
        };
        let mut c = counters.lock().unwrap();
        if dropped {
            c.events_dropped_total += 1;
            c.events_dropped_by_queue_size_since_last_heartbeat += 1;
        }
        c.events_received_total += 1;
        c.max_event_queue_depth_since_last_heartbeat =
            c.max_event_queue_depth_since_last_heartbeat.max(new_len as i64);
    }

    pub fn pop_batch(&self, limit: usize) -> Vec<Value> {
        let mut items = self.items.lock().unwrap();
        let n = limit.min(items.len());
        items.drain(..n).collect()
    }

    pub fn size(&self) -> usize {
        self.items.lock().unwrap().len()
    }
}

pub struct IngestDatagramServer {
    socket_path: String,
    buffer_bytes: usize,
    queue: Arc<IngestQueue>,
    counters: Arc<Mutex<TransponderCounters>>,
    stop: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
}

impl IngestDatagramServer {
    pub fn new(
        socket_path: String,
        buffer_bytes: usize,
        queue: Arc<IngestQueue>,
        counters: Arc<Mutex<TransponderCounters>>,
    ) -> Self {
        Self {
            socket_path,
            buffer_bytes,
            queue,
            counters,
            stop: Arc::new(AtomicBool::new(false)),
            thread: None,
        }
    }

    pub fn start(&mut self) {
        if self.thread.is_some() {
            return;
        }

        // Ensure parent directory exists.
        if let Some(parent) = Path::new(&self.socket_path).parent() {
            let _ = fs::create_dir_all(parent);
        }

        // Remove existing socket file.
        let _ = fs::remove_file(&self.socket_path);

        let sock = UnixDatagram::bind(&self.socket_path).expect("failed to bind ingest socket");
        sock.set_read_timeout(Some(Duration::from_millis(500))).ok();

        // Set socket file permissions to 0o666.
        let _ = fs::set_permissions(&self.socket_path, fs::Permissions::from_mode(0o666));

        let queue = self.queue.clone();
        let counters = self.counters.clone();
        let buffer_bytes = self.buffer_bytes;
        let stop = self.stop.clone();

        self.thread = Some(thread::spawn(move || {
            let mut buf = vec![0u8; buffer_bytes];
            while !stop.load(Ordering::Relaxed) {
                let n = match sock.recv(&mut buf) {
                    Ok(n) => n,
                    Err(ref e) if e.kind() == io::ErrorKind::WouldBlock => continue,
                    Err(_) => break,
                };

                let raw = String::from_utf8_lossy(&buf[..n]);
                let obj = match serde_json::from_str::<Value>(&raw) {
                    Ok(Value::Object(map)) => map,
                    _ => continue,
                };

                let mut event = serde_json::Map::new();
                event.insert(
                    "ts".to_string(),
                    obj.get("ts")
                        .cloned()
                        .unwrap_or_else(|| Value::String(utc_now())),
                );
                event.insert(
                    "type".to_string(),
                    Value::String(
                        obj.get("type")
                            .and_then(|v| v.as_str())
                            .unwrap_or("app.event")
                            .to_string(),
                    ),
                );
                event.insert(
                    "severity".to_string(),
                    obj.get("severity")
                        .cloned()
                        .unwrap_or_else(|| Value::String("info".to_string())),
                );
                event.insert(
                    "payload".to_string(),
                    obj.get("payload")
                        .cloned()
                        .unwrap_or_else(|| Value::Object(obj.clone())),
                );

                if let Some(Value::Object(tags)) = obj.get("tags") {
                    let clean_tags: serde_json::Map<String, Value> = tags
                        .iter()
                        .map(|(k, v)| {
                            let s = match v {
                                Value::String(s) => s.clone(),
                                other => other.to_string(),
                            };
                            (k.clone(), Value::String(s))
                        })
                        .collect();
                    event.insert("tags".to_string(), Value::Object(clean_tags));
                }

                queue.push(Value::Object(event), &counters);
            }
        }));
    }

    pub fn stop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(handle) = self.thread.take() {
            let _ = handle.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn make_counters() -> Mutex<TransponderCounters> {
        Mutex::new(TransponderCounters::default())
    }

    #[test]
    fn test_queue_push_and_pop() {
        let q = IngestQueue::new(10);
        let c = make_counters();
        q.push(json!({"a": 1}), &c);
        q.push(json!({"b": 2}), &c);
        assert_eq!(q.size(), 2);
        let batch = q.pop_batch(10);
        assert_eq!(batch.len(), 2);
        assert_eq!(q.size(), 0);
    }

    #[test]
    fn test_queue_pop_batch_respects_limit() {
        let q = IngestQueue::new(100);
        let c = make_counters();
        for i in 0..5 {
            q.push(json!({"i": i}), &c);
        }
        let batch = q.pop_batch(3);
        assert_eq!(batch.len(), 3);
        assert_eq!(q.size(), 2);
    }

    #[test]
    fn test_queue_overflow_drops_oldest() {
        let q = IngestQueue::new(3);
        let c = make_counters();
        q.push(json!({"i": 0}), &c);
        q.push(json!({"i": 1}), &c);
        q.push(json!({"i": 2}), &c);
        // Queue is now full (3 items). Next push should drop oldest.
        q.push(json!({"i": 3}), &c);
        assert_eq!(q.size(), 3);
        let batch = q.pop_batch(10);
        // Oldest (i=0) should have been dropped.
        assert_eq!(batch[0]["i"], 1);
        assert_eq!(batch[1]["i"], 2);
        assert_eq!(batch[2]["i"], 3);
    }

    #[test]
    fn test_queue_overflow_updates_counters() {
        let q = IngestQueue::new(2);
        let c = make_counters();
        q.push(json!({"i": 0}), &c);
        q.push(json!({"i": 1}), &c);
        // These pushes cause overflow drops.
        q.push(json!({"i": 2}), &c);
        q.push(json!({"i": 3}), &c);

        let counters = c.lock().unwrap();
        assert_eq!(counters.events_received_total, 4);
        assert_eq!(counters.events_dropped_total, 2);
        assert_eq!(counters.events_dropped_by_queue_size_since_last_heartbeat, 2);
        assert_eq!(counters.max_event_queue_depth_since_last_heartbeat, 2);
    }

    #[test]
    fn test_queue_tracks_max_depth() {
        let q = IngestQueue::new(100);
        let c = make_counters();
        for i in 0..5 {
            q.push(json!({"i": i}), &c);
        }
        // Pop some items.
        q.pop_batch(3);
        // Push one more.
        q.push(json!({"i": 5}), &c);

        let counters = c.lock().unwrap();
        // Max depth should be 5 (the peak before popping).
        assert_eq!(counters.max_event_queue_depth_since_last_heartbeat, 5);
    }

    #[test]
    fn test_queue_pop_empty() {
        let q = IngestQueue::new(10);
        let batch = q.pop_batch(10);
        assert!(batch.is_empty());
    }

    #[test]
    fn test_ingest_server_socket_lifecycle() {
        let socket_path = format!("/tmp/_test_ingest_{}.sock", std::process::id());
        let queue = Arc::new(IngestQueue::new(100));
        let counters = Arc::new(Mutex::new(TransponderCounters::default()));

        let mut server = IngestDatagramServer::new(
            socket_path.clone(),
            65535,
            queue.clone(),
            counters.clone(),
        );
        server.start();

        // Send a datagram to the socket.
        let client = UnixDatagram::unbound().unwrap();
        let msg = json!({"type": "test.event", "severity": "warn", "payload": {"key": "val"}});
        client.send_to(msg.to_string().as_bytes(), &socket_path).unwrap();

        // Give the server thread time to process.
        std::thread::sleep(Duration::from_millis(100));

        assert_eq!(queue.size(), 1);
        let batch = queue.pop_batch(1);
        assert_eq!(batch[0]["type"], "test.event");
        assert_eq!(batch[0]["severity"], "warn");

        server.stop();
        let _ = std::fs::remove_file(&socket_path);
    }

    #[test]
    fn test_ingest_server_normalizes_event_defaults() {
        let socket_path = format!("/tmp/_test_ingest_defaults_{}.sock", std::process::id());
        let queue = Arc::new(IngestQueue::new(100));
        let counters = Arc::new(Mutex::new(TransponderCounters::default()));

        let mut server = IngestDatagramServer::new(
            socket_path.clone(),
            65535,
            queue.clone(),
            counters.clone(),
        );
        server.start();

        // Send a minimal event (no type/severity/ts).
        let client = UnixDatagram::unbound().unwrap();
        let msg = json!({"foo": "bar"});
        client.send_to(msg.to_string().as_bytes(), &socket_path).unwrap();

        std::thread::sleep(Duration::from_millis(100));

        let batch = queue.pop_batch(1);
        assert_eq!(batch.len(), 1);
        // Should have defaults applied.
        assert_eq!(batch[0]["type"], "app.event");
        assert_eq!(batch[0]["severity"], "info");
        assert!(batch[0]["ts"].is_string());
        // Payload should be the original object.
        assert_eq!(batch[0]["payload"]["foo"], "bar");

        server.stop();
        let _ = std::fs::remove_file(&socket_path);
    }
}
