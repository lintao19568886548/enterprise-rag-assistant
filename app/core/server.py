"""Secure defaults for running API processes."""

from __future__ import annotations

from typing import Any

import uvicorn


def run_api(app: Any, *, host: str, port: int) -> None:
    # Structured middleware records method/path/status without query parameters.
    # Uvicorn's raw request line would expose OIDC codes and sensitive query data.
    uvicorn.run(app, host=host, port=port, access_log=False)
