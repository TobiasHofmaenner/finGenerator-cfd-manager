"""Forward a completed job to the findata corpus. Best-effort: the durable CFD
truth is already in the job row, so a failed forward is recoverable (backfill
later); it must never fail the worker's result post."""
from __future__ import annotations

import os

import httpx


def findata_configured() -> bool:
    return bool(os.environ.get("FINDATA_URL") and
                os.environ.get("FINDATA_WRITE_TOKEN"))


def build_sample(job: dict) -> dict:
    """Assemble a findata sample from a completed job (request) + result."""
    req = job.get("request") or {}
    res = job.get("result") or {}
    sample = {
        "fin_geometry": req.get("fin"),
        "config": req.get("config"),
        "operating_point": {
            "speed": res.get("speed") or req.get("speed"),
            "angles": req.get("angles"),
        },
        "cfd_result": {"rows": res.get("rows")},
        "cfd_setup": res.get("cfd_setup") or {},
        "cfd_quality": res.get("cfd_quality"),
        "provenance": {"source": "manager", "job_id": job.get("id")},
    }
    t0 = res.get("tier0_prediction")
    if t0:
        sample["tier0_prediction"] = t0
    return sample


async def forward_sample(job: dict) -> None:
    if not findata_configured():
        return
    url = os.environ["FINDATA_URL"].rstrip("/")
    token = os.environ["FINDATA_WRITE_TOKEN"]
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            await c.post(f"{url}/samples", json=build_sample(job),
                         headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        pass  # best-effort; the job row retains everything for a later backfill
