"""Run the task console: `uv run python -m orchestrator` from the catalog root."""
import logging
import os

import uvicorn

from orchestrator.config import settings_from_env
from orchestrator.webapp import create_app

if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("ORCH_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = settings_from_env()
    uvicorn.run(
        create_app(settings),
        host=os.environ.get("ORCH_HOST", "127.0.0.1"),
        port=int(os.environ.get("ORCH_PORT", "8010")),
        timeout_keep_alive=settings.sse_timeout_s,
    )
