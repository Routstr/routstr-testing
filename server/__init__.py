"""FastAPI backend that serves the routstr-testing UI.

Endpoints under /api/scenarios (CRUD against scenarios/*.yaml on disk) and
/api/runs (read against runs.db, write via subprocess into runner/orchestrate).

The cashu token submitted with POST /api/runs is *write-only*: it is passed
to the orchestrator via the E2E_CASHU_TOKEN environment variable and never
stored on the runs row, never logged, never echoed back.
"""

from .main import app, create_app

__all__ = ["app", "create_app"]
