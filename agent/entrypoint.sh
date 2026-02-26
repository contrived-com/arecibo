#!/usr/bin/env sh
set -eu

# Canonical CEA launcher.
# - Starts the embedded agent as a best-effort background companion.
# - Never blocks app startup.
# - Always execs the app command so app remains PID 1.

start_cea() {
  if [ "${CEA_ENABLED:-true}" != "true" ]; then
    return 0
  fi

  CEA_NICE_LEVEL="${CEA_NICE_LEVEL:-10}"
  CEA_IONICE_CLASS="${CEA_IONICE_CLASS:-3}"
  CEA_IONICE_LEVEL="${CEA_IONICE_LEVEL:-7}"
  CEA_AGENT_BIN="${CEA_AGENT_BIN:-/opt/cea/.venv/bin/cea-agent}"
  CEA_AGENT_ARGS="${CEA_AGENT_ARGS:-}"

  # shellcheck disable=SC2086
  if command -v ionice >/dev/null 2>&1; then
    if [ "${CEA_IONICE_CLASS}" = "2" ]; then
      # Best-effort I/O class uses an additional level.
      sh -c "ionice -c ${CEA_IONICE_CLASS} -n ${CEA_IONICE_LEVEL} nice -n ${CEA_NICE_LEVEL} ${CEA_AGENT_BIN} ${CEA_AGENT_ARGS}" &
    else
      sh -c "ionice -c ${CEA_IONICE_CLASS} nice -n ${CEA_NICE_LEVEL} ${CEA_AGENT_BIN} ${CEA_AGENT_ARGS}" &
    fi
  else
    sh -c "nice -n ${CEA_NICE_LEVEL} ${CEA_AGENT_BIN} ${CEA_AGENT_ARGS}" &
  fi
}

start_cea || true
exec "$@"
