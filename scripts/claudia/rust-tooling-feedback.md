# Rust Tooling Feedback — Transponder Migration

Practical findings from migrating the Arecibo transponder from Python to Rust.

## Build Times

- **Debug build** (cold): ~11s on first compile, ~2s incremental.
- **Release build** (cold): ~30s due to LTO and optimizations; incremental ~3-5s.
- **CI tip**: Split `Cargo.toml`/`Cargo.lock` into an earlier Docker layer so
  dependency downloads and compilation are cached. Only the final `cargo build`
  with real source triggers a recompile.

## Crate Recommendations

| Crate | Use | Notes |
|-------|-----|-------|
| `ureq` 2.x | HTTP client | Blocking, lightweight, no async runtime needed. Perfect for a companion process. |
| `serde` + `serde_json` | JSON | Standard, zero surprises. |
| `chrono` | Timestamps | RFC 3339 formatting straightforward with `format!("%Y-%m-%dT%H:%M:%SZ")`. |
| `uuid` | Event IDs | v4 generation, `as_simple()` for compact format. |
| `ctrlc` | Signal handling | Handles SIGTERM + SIGINT via single `AtomicBool`. |
| `env_logger` | Logging | Simple, env-configurable, no overhead. |
| `libc` | Hostname | `gethostname` FFI; avoids pulling in `hostname` crate. |

## Avoided Crates

- **`reqwest`**: Pulls in `tokio` async runtime. Unnecessary for a blocking companion process.
- **`tokio`**: The transponder uses threads, not async. The 200ms tick loop with
  `std::thread::sleep` is simpler and more predictable than an async event loop.
- **`mockall`**: Not needed; manual test doubles and `#[cfg(test)]` modules are
  sufficient for the test surface here.

## Musl / Static Linking Considerations

- The current Dockerfile uses `debian:bookworm-slim` as the runtime image, which
  is simple and well-supported.
- For a scratch-based image, `x86_64-unknown-linux-musl` target would enable
  fully static binaries. This requires `musl-tools` in the builder and may need
  OpenSSL replaced with `rustls` (already in use via `ureq`'s default TLS).
- Trade-off: musl builds are slightly slower at runtime for allocation-heavy
  workloads, but the transponder is I/O-bound so impact is negligible.

## CI Caching Advice

- Cache `$CARGO_HOME/registry` and `target/` between CI runs.
- The dummy-main-rs trick in the Dockerfile effectively caches dependencies
  in Docker layer cache.
- For GitHub Actions, use `actions/cache` with key on `Cargo.lock` hash.

## Thread Safety Patterns

- `Arc<Mutex<TransponderCounters>>` shared between runtime and ingest threads.
- Key pattern: never hold the counter lock during HTTP calls or across
  `apply_directives` (which may trigger `refresh_policy` → HTTP call).
- Ingest thread uses 500ms `read_timeout` + `AtomicBool` for clean shutdown,
  avoiding the need to close the socket from another thread.

## Testing Without External Dependencies

- All 39 unit tests run without network access to collectors.
- Queue, counter, directive, and config tests use pure in-process assertions.
- Ingest socket tests use process-local Unix sockets in `/tmp`.
- HTTP client tests verify connection-refused returns `(0, None)` rather than
  panicking.
