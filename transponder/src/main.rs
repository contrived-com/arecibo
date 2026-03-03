mod client;
mod config;
mod ingest;
mod model;
mod runtime;
mod utils;

use config::TransponderConfig;
use runtime::TransponderRuntime;
use utils::utc_now;

fn main() {
    let startup_ts = utc_now();
    let config = TransponderConfig::from_env(startup_ts);
    let mut runtime = TransponderRuntime::new(config);
    runtime.run();
}
