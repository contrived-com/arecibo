# Arecibo Transponder

Rust implementation of the Arecibo embedded-transponder runtime.

## Source layout

```
transponder/
├── Cargo.toml          # Package manifest and dependencies
├── Cargo.lock          # Locked dependency versions
├── build.rs            # Captures RUSTC_VERSION at compile time
├── entrypoint.sh       # Canonical launcher script
└── src/
    ├── main.rs         # Entry point: config → runtime → run
    ├── config.rs       # TRANSPONDER_* env parsing, Vault fallback
    ├── client.rs       # HTTP collector client (ureq)
    ├── model.rs        # Directive, PolicyState, TransponderCounters
    ├── ingest.rs       # Unix datagram socket + bounded event queue
    ├── runtime.rs      # Bootstrap, main loop, directives, shutdown
    └── utils.rs        # Timestamps, UUIDs, logging setup
```

## Local workflow

```bash
cd transponder
cargo build            # debug build
cargo build --release  # optimized build
cargo test             # run all tests
cargo run              # run the transponder locally
```

## Docker build

```bash
docker build -f Dockerfile.transponder -t arecibo-transponder .
```

The multi-stage Dockerfile compiles the binary in a Rust builder image and
copies only the binary into a minimal `debian:bookworm-slim` runtime image.

## Downstream integration

```dockerfile
COPY --from=ghcr.io/contrived-com/arecibo-transponder /opt/transponder /opt/transponder
ENTRYPOINT ["/opt/transponder/entrypoint.sh"]
CMD ["your-app-command"]
```

The binary lives at `/opt/transponder/bin/transponder` and the launcher
script at `/opt/transponder/entrypoint.sh`.

## Runtime behavior

- Collector discovery order:
  1. `TRANSPONDER_COLLECTOR_URL` (explicit override)
  2. `TRANSPONDER_COLLECTOR_CANDIDATES` (default internal first, then external)
- API key discovery order:
  1. `TRANSPONDER_API_KEY` (explicit override)
  2. Vault fallback via AppRole env (`VAULT_ADDR`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID`)
     reading `secret/${TRANSPONDER_VAULT_PATH:-arecibo/config}` field
     `${TRANSPONDER_API_KEY_FIELD:-arecibo_api_keys}`
- If `GO_DARK` is active, outbound sends stop while local ingest remains available.
- Local ingest socket defaults to `unixgram` at `/tmp/transponder-ingest.sock`.
