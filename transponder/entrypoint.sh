#!/usr/bin/env sh
set -eu

# Canonical transponder launcher.
# - Starts the embedded transponder as a best-effort background companion.
# - Never blocks app startup.
# - Always execs the app command so app remains PID 1.

start_transponder() {
  if [ "${TRANSPONDER_ENABLED:-true}" != "true" ]; then
    return 0
  fi

  TRANSPONDER_NICE_LEVEL="${TRANSPONDER_NICE_LEVEL:-10}"
  TRANSPONDER_IONICE_CLASS="${TRANSPONDER_IONICE_CLASS:-3}"
  TRANSPONDER_IONICE_LEVEL="${TRANSPONDER_IONICE_LEVEL:-7}"
  TRANSPONDER_BIN="${TRANSPONDER_BIN:-/opt/transponder/.venv/bin/transponder}"
  TRANSPONDER_ARGS="${TRANSPONDER_ARGS:-}"

  # shellcheck disable=SC2086
  if command -v ionice >/dev/null 2>&1; then
    if [ "${TRANSPONDER_IONICE_CLASS}" = "2" ]; then
      # Best-effort I/O class uses an additional level.
      sh -c "ionice -c ${TRANSPONDER_IONICE_CLASS} -n ${TRANSPONDER_IONICE_LEVEL} nice -n ${TRANSPONDER_NICE_LEVEL} ${TRANSPONDER_BIN} ${TRANSPONDER_ARGS}" &
    else
      sh -c "ionice -c ${TRANSPONDER_IONICE_CLASS} nice -n ${TRANSPONDER_NICE_LEVEL} ${TRANSPONDER_BIN} ${TRANSPONDER_ARGS}" &
    fi
  else
    sh -c "nice -n ${TRANSPONDER_NICE_LEVEL} ${TRANSPONDER_BIN} ${TRANSPONDER_ARGS}" &
  fi
}

start_transponder || true
exec "$@"
