from __future__ import annotations

from .config import AgentConfig
from .runtime import CEARuntime
from .utils import utc_now

def main() -> None:
    startup_ts = utc_now()
    config = AgentConfig.from_env(startup_ts=startup_ts)
    runtime = CEARuntime(config)
    runtime.run()


if __name__ == "__main__":
    main()
