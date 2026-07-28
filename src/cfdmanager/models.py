"""API bodies. The client submits a JobRequest; the worker leases, heartbeats,
and posts a ResultRequest whose `result` carries the CFD polar + the tier-0
prediction the worker computed (both get forwarded to findata)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_ANGLES = [0.0, 4.0, 8.0, 12.0, 16.0]


class JobRequest(BaseModel):
    fin: dict[str, Any]                 # fingen fin dict (validated by the worker)
    config: str | None = None           # single/thruster/... (corpus context)
    speed: float | None = None
    mesh_level: int = 2
    angles: list[float] = Field(default_factory=lambda: list(DEFAULT_ANGLES))
    # Solver iteration cap, passed through to the worker (None => its recipe
    # default). A small value gives a fast coarse run for smoke tests.
    end_time: int | None = None
    smoke: bool = False


class LeaseRequest(BaseModel):
    worker_id: str
    lease_seconds: int = 1800


class HeartbeatRequest(BaseModel):
    worker_id: str


class ResultRequest(BaseModel):
    worker_id: str
    status: Literal["done", "error"]
    # For a done job: {rows, cfd_setup, cfd_quality, tier0_prediction, speed, ...}.
    result: dict[str, Any] | None = None
    error: str | None = None
