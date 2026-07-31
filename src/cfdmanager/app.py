"""CFD job manager API. Stateless; all state in Postgres (CNPG). On a completed
result it forwards the sample to findata (best-effort, in the background).

Env: DATABASE_URL, CFD_CLIENT_TOKEN, CFD_WORKER_TOKEN, LEASE_SECONDS (opt),
FINDATA_URL + FINDATA_WRITE_TOKEN (opt, for corpus forwarding).
Run: uvicorn cfdmanager.app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response

from . import forward
from .db import Store
from .models import HeartbeatRequest, JobRequest, LeaseRequest, ResultRequest

CLIENT_TOKEN = os.environ.get("CFD_CLIENT_TOKEN", "")
WORKER_TOKEN = os.environ.get("CFD_WORKER_TOKEN", "")
DEFAULT_LEASE = int(os.environ.get("LEASE_SECONDS", "1800"))


def _require(*accepted: str):
    accepted_set = {t for t in accepted if t}

    async def dep(authorization: str = Header(default="")) -> None:
        supplied = authorization.removeprefix("Bearer ") if \
            authorization.startswith("Bearer ") else ""
        if not accepted_set or supplied not in accepted_set:
            raise HTTPException(status_code=401, detail="unauthorized")

    return dep


client_auth = _require(CLIENT_TOKEN)
worker_auth = _require(WORKER_TOKEN)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = await Store.connect(os.environ["DATABASE_URL"])
    try:
        yield
    finally:
        await app.state.store.close()


app = FastAPI(title="fin-cfd-manager", lifespan=lifespan)


def _store(request: Request) -> Store:
    return request.app.state.store


@app.get("/healthz")
async def healthz(request: Request):
    try:
        ok = await _store(request).ping()
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=503, detail="db unavailable")
    return {"status": "ok"}


@app.post("/jobs", status_code=202, dependencies=[Depends(client_auth)])
async def create_job(body: JobRequest, request: Request):
    job_id = await _store(request).insert_job(body.model_dump())
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}", dependencies=[Depends(client_auth)])
async def get_job(job_id: str, request: Request):
    job = await _store(request).get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="not found")
    return job


@app.get("/jobs", dependencies=[Depends(client_auth)])
async def list_jobs(request: Request, status: str | None = None, limit: int = 50):
    return await _store(request).list_jobs(status, min(max(limit, 1), 200))


@app.post("/jobs/lease", dependencies=[Depends(worker_auth)])
async def lease(body: LeaseRequest, request: Request, response: Response):
    job = await _store(request).lease_job(body.worker_id,
                                          body.lease_seconds or DEFAULT_LEASE)
    if job is None:
        response.status_code = 204
        return None
    return job


@app.post("/jobs/{job_id}/heartbeat", dependencies=[Depends(worker_auth)])
async def heartbeat(job_id: str, body: HeartbeatRequest, request: Request):
    if not await _store(request).heartbeat(job_id, body.worker_id, DEFAULT_LEASE,
                                           body.progress):
        raise HTTPException(status_code=409, detail="lease lost")
    return {"ok": True}


@app.post("/jobs/{job_id}/result", dependencies=[Depends(worker_auth)])
async def submit_result(job_id: str, body: ResultRequest, request: Request,
                        background: BackgroundTasks):
    job = await _store(request).submit_result(
        job_id, body.worker_id, body.status, body.result, body.error)
    if job is None:
        raise HTTPException(status_code=409, detail="lease lost or not running")
    # Feed the corpus on success (best-effort, after the response). Optimize
    # jobs are RIDER results, not CFD samples — nothing for the corpus.
    if body.status == "done" and not (job.get("request") or {}).get("optimize"):
        background.add_task(forward.forward_sample, job)
    return {"ok": True}
