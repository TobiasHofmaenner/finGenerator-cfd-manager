"""API bodies. The client submits a JobRequest; the worker leases, heartbeats,
and posts a ResultRequest whose `result` carries the CFD polar + the tier-0
prediction the worker computed (both get forwarded to findata)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

DEFAULT_ANGLES = [0.0, 4.0, 8.0, 12.0, 16.0]


class JobRequest(BaseModel):
    # Exactly one of `fin` (single blade) or `fin_set` (a whole placed cluster —
    # the interference-resolving multi-fin case). Both are fingen dicts, checked
    # by the worker; the manager stays free of a fingen dependency.
    fin: dict[str, Any] | None = None
    fin_set: dict[str, Any] | None = None
    # Rider-profile optimization (the web quiz): {"rider": {...}, "seeds"?,
    # "budget_evals"?}. Shape checked by the worker; the manager stays free of
    # a fingen dependency, exactly as with fin/fin_set.
    optimize: dict[str, Any] | None = None
    config: str | None = None           # single/thruster/... (corpus context)
    speed: float | None = None
    mesh_level: int = 2
    angles: list[float] = Field(default_factory=lambda: list(DEFAULT_ANGLES))
    # Solver iteration cap, passed through to the worker (None => its recipe
    # default). A small value gives a fast coarse run for smoke tests.
    end_time: int | None = None
    smoke: bool = False

    @model_validator(mode="after")
    def _exactly_one_geometry(self) -> JobRequest:
        if sum(map(bool, (self.fin, self.fin_set, self.optimize))) != 1:
            raise ValueError(
                "provide exactly one of 'fin', 'fin_set' or 'optimize'")
        return self


class LeaseRequest(BaseModel):
    worker_id: str
    lease_seconds: int = 1800


class HeartbeatRequest(BaseModel):
    worker_id: str
    # Live job progress (the optimize worker posts phase/fraction/best-so-far
    # every few seconds). Stored on the job row and returned by GET /jobs/{id}
    # so a browser can poll one endpoint for state + progress together.
    progress: dict[str, Any] | None = None


class ResultRequest(BaseModel):
    worker_id: str
    status: Literal["done", "error"]
    # For a done job: {rows, cfd_setup, cfd_quality, tier0_prediction, speed, ...}.
    result: dict[str, Any] | None = None
    error: str | None = None
