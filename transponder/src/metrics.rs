use std::fs;

#[derive(Default, Clone, Copy, Debug)]
struct NetCounters {
    rx_bytes: u64,
    tx_bytes: u64,
}

#[derive(Default, Clone, Copy, Debug)]
pub struct HeartbeatMetrics {
    pub transponder_rss_bytes: Option<i64>,
    pub transponder_cpu_user_sec: Option<f64>,
    pub transponder_cpu_system_sec: Option<f64>,
    pub primary_app_pid: Option<i64>,
    pub primary_app_rss_bytes: Option<i64>,
    pub container_memory_current_bytes: Option<i64>,
    pub container_memory_max_bytes: Option<i64>,
    pub container_rx_bytes_since_last_heartbeat: Option<i64>,
    pub container_tx_bytes_since_last_heartbeat: Option<i64>,
}

pub struct MetricSampler {
    last_net: Option<NetCounters>,
    clk_tck: f64,
}

impl MetricSampler {
    pub fn new() -> Self {
        let clk_tck = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
        let clk_tck = if clk_tck > 0 { clk_tck as f64 } else { 100.0 };
        Self {
            last_net: None,
            clk_tck,
        }
    }

    pub fn sample(&mut self) -> HeartbeatMetrics {
        let mut metrics = HeartbeatMetrics::default();

        let self_pid = std::process::id() as i64;
        metrics.transponder_rss_bytes = read_proc_rss_bytes(self_pid);

        if let Some((user_ticks, system_ticks)) = read_proc_cpu_ticks(self_pid) {
            metrics.transponder_cpu_user_sec = Some((user_ticks as f64) / self.clk_tck);
            metrics.transponder_cpu_system_sec = Some((system_ticks as f64) / self.clk_tck);
        }

        // In containers, the primary application is expected to be PID 1.
        let app_pid = if read_proc_exists(1) { Some(1) } else { None };
        metrics.primary_app_pid = app_pid;
        metrics.primary_app_rss_bytes = app_pid.and_then(read_proc_rss_bytes);

        metrics.container_memory_current_bytes = read_first_i64(&[
            "/sys/fs/cgroup/memory.current",
            "/sys/fs/cgroup/memory/memory.usage_in_bytes",
        ]);
        metrics.container_memory_max_bytes = read_first_i64(&[
            "/sys/fs/cgroup/memory.max",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
        ]);

        if let Some(now) = read_net_counters() {
            if let Some(prev) = self.last_net {
                metrics.container_rx_bytes_since_last_heartbeat =
                    Some(now.rx_bytes.saturating_sub(prev.rx_bytes) as i64);
                metrics.container_tx_bytes_since_last_heartbeat =
                    Some(now.tx_bytes.saturating_sub(prev.tx_bytes) as i64);
            }
            self.last_net = Some(now);
        }

        metrics
    }
}

fn read_proc_exists(pid: i64) -> bool {
    fs::metadata(format!("/proc/{}", pid)).is_ok()
}

fn read_proc_rss_bytes(pid: i64) -> Option<i64> {
    let raw = fs::read_to_string(format!("/proc/{}/status", pid)).ok()?;
    parse_rss_bytes_from_status(&raw)
}

fn parse_rss_bytes_from_status(raw: &str) -> Option<i64> {
    for line in raw.lines() {
        if let Some(rest) = line.strip_prefix("VmRSS:") {
            let kb = rest
                .split_whitespace()
                .next()
                .and_then(|v| v.parse::<i64>().ok())?;
            return Some(kb.saturating_mul(1024));
        }
    }
    None
}

fn read_proc_cpu_ticks(pid: i64) -> Option<(u64, u64)> {
    let raw = fs::read_to_string(format!("/proc/{}/stat", pid)).ok()?;
    parse_proc_stat_ticks(&raw)
}

fn parse_proc_stat_ticks(raw: &str) -> Option<(u64, u64)> {
    let close_idx = raw.rfind(')')?;
    let tail = raw.get(close_idx + 2..)?;
    let fields: Vec<&str> = tail.split_whitespace().collect();
    // /proc/<pid>/stat fields 14 and 15 (utime, stime) map to indexes 11 and 12 in tail.
    let user = fields.get(11)?.parse::<u64>().ok()?;
    let system = fields.get(12)?.parse::<u64>().ok()?;
    Some((user, system))
}

fn read_net_counters() -> Option<NetCounters> {
    let raw = fs::read_to_string("/proc/net/dev").ok()?;
    parse_net_dev_totals(&raw)
}

fn parse_net_dev_totals(raw: &str) -> Option<NetCounters> {
    let mut rx_total: u64 = 0;
    let mut tx_total: u64 = 0;
    let mut found = false;

    for line in raw.lines().skip(2) {
        let (iface, values) = line.split_once(':')?;
        let iface = iface.trim();
        if iface.is_empty() || iface == "lo" {
            continue;
        }
        let parts: Vec<&str> = values.split_whitespace().collect();
        if parts.len() < 16 {
            continue;
        }
        let rx = match parts[0].parse::<u64>() {
            Ok(v) => v,
            Err(_) => continue,
        };
        let tx = match parts[8].parse::<u64>() {
            Ok(v) => v,
            Err(_) => continue,
        };
        rx_total = rx_total.saturating_add(rx);
        tx_total = tx_total.saturating_add(tx);
        found = true;
    }

    if found {
        Some(NetCounters {
            rx_bytes: rx_total,
            tx_bytes: tx_total,
        })
    } else {
        None
    }
}

fn read_first_i64(paths: &[&str]) -> Option<i64> {
    for path in paths {
        let raw = match fs::read_to_string(path) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let value = raw.trim();
        if value.is_empty() || value == "max" {
            continue;
        }
        if let Ok(parsed) = value.parse::<u64>() {
            let clamped = parsed.min(i64::MAX as u64) as i64;
            return Some(clamped);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_proc_ticks() {
        let sample = "1 (transponder) S 0 1 1 0 -1 0 0 0 0 0 41 9 0 0 20 0 1 0";
        let ticks = parse_proc_stat_ticks(sample).unwrap();
        assert_eq!(ticks.0, 41);
        assert_eq!(ticks.1, 9);
    }

    #[test]
    fn parses_net_totals_without_loopback() {
        let sample = "Inter-|   Receive                                                |  Transmit\n\
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n\
 lo: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0\n\
 eth0: 1000 10 0 0 0 0 0 0 2000 20 0 0 0 0 0 0\n\
 eth1: 300 3 0 0 0 0 0 0 700 7 0 0 0 0 0 0\n";
        let parsed = parse_net_dev_totals(sample).unwrap();
        assert_eq!(parsed.rx_bytes, 1300);
        assert_eq!(parsed.tx_bytes, 2700);
    }

    #[test]
    fn parses_rss_from_status() {
        let sample = "Name:\ttest\nVmRSS:\t   512 kB\n";
        assert_eq!(parse_rss_bytes_from_status(sample), Some(524288));
    }
}
