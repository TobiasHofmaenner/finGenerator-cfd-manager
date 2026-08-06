"""Job queue over an operator-managed Postgres (CNPG). The lease is a single
FOR UPDATE SKIP LOCKED claim so many workers + replicas never double-lease;
expired leases are auto-reclaimed so a dead worker's job re-queues."""
from __future__ import annotations

import json
from typing import Any

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status text NOT NULL DEFAULT 'queued',
    request jsonb NOT NULL,
    result jsonb,
    error text,
    worker_id text,
    attempts int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    leased_at timestamptz,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs (status, created_at);
-- Live progress (additive; predates some deployments, hence the ALTER).
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS progress jsonb;
"""

# A job may be re-leased after a lapse only this many times before it is
# QUARANTINED as failed. Without a cap, a job whose run (or whose result
# delivery) reliably kills the worker requeues forever: four such jobs burned
# six days of 12-core compute at up to 221 attempts each. Attempt 1 is the
# normal first lease, so MAX_ATTEMPTS=4 means three retries after a lapse —
# enough for worker restarts and deploys, nowhere near enough to hide a
# poison job.
MAX_ATTEMPTS = 4

_QUARANTINE_SQL = """
UPDATE jobs SET
    status='error', finished_at=now(),
    error='quarantined: ' || attempts || ' attempts without a result '
          '(lease expired each time) — likely kills the worker; not retrying'
WHERE status='running' AND lease_expires_at < now() AND attempts >= $1
"""

_LEASE_SQL = """
UPDATE jobs SET
    status='running', worker_id=$1, leased_at=now(),
    lease_expires_at=now() + ($2 || ' seconds')::interval,
    heartbeat_at=now(), attempts=attempts+1
WHERE id = (
    SELECT id FROM jobs
    WHERE status='queued'
       OR (status='running' AND lease_expires_at < now() AND attempts < $3)
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
RETURNING id, request
"""


def _j(v: Any) -> Any:
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


def _row(r: asyncpg.Record) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    d["request"] = _j(d.get("request"))
    d["result"] = _j(d.get("result"))
    d["progress"] = _j(d.get("progress"))
    for k in ("created_at", "leased_at", "lease_expires_at", "heartbeat_at",
              "finished_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


class Store:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> Store:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
        async with pool.acquire() as con:
            await con.execute(DDL)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def ping(self) -> bool:
        async with self.pool.acquire() as con:
            return (await con.fetchval("SELECT 1")) == 1

    async def insert_job(self, request: dict[str, Any]) -> str:
        async with self.pool.acquire() as con:
            return str(await con.fetchval(
                "INSERT INTO jobs (request) VALUES ($1::jsonb) RETURNING id",
                json.dumps(request)))

    async def get_job(self, job_id: str) -> dict | None:
        async with self.pool.acquire() as con:
            r = await con.fetchrow("SELECT * FROM jobs WHERE id=$1::uuid", job_id)
            return _row(r) if r else None

    async def list_jobs(self, status: str | None, limit: int) -> list[dict]:
        async with self.pool.acquire() as con:
            if status:
                rows = await con.fetch(
                    "SELECT * FROM jobs WHERE status=$1 "
                    "ORDER BY created_at DESC LIMIT $2", status, limit)
            else:
                rows = await con.fetch(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT $1", limit)
            return [_row(r) for r in rows]

    async def lease_job(self, worker_id: str, lease_seconds: int) -> dict | None:
        async with self.pool.acquire() as con:
            # Sweep exhausted jobs to a terminal state FIRST, so they are
            # visibly failed rather than invisible zombies the lease query
            # forever skips.
            await con.execute(_QUARANTINE_SQL, MAX_ATTEMPTS)
            r = await con.fetchrow(_LEASE_SQL, worker_id, str(lease_seconds),
                                   MAX_ATTEMPTS)
            return {"id": str(r["id"]), "request": _j(r["request"])} if r else None

    async def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int,
                        progress: dict | None = None) -> bool:
        async with self.pool.acquire() as con:
            res = await con.execute(
                "UPDATE jobs SET heartbeat_at=now(), "
                "lease_expires_at=now() + ($3 || ' seconds')::interval, "
                "progress=COALESCE($4::jsonb, progress) "
                "WHERE id=$1::uuid AND worker_id=$2 AND status='running'",
                job_id, worker_id, str(lease_seconds),
                json.dumps(progress) if progress is not None else None)
            return res.endswith(" 1")

    async def submit_result(self, job_id, worker_id, status, result, error) -> dict | None:
        """Store the result iff this worker owns the running job. Returns the
        full job row on success (so the caller can forward it), else None."""
        async with self.pool.acquire() as con:
            r = await con.fetchrow(
                "UPDATE jobs SET status=$3, result=$4::jsonb, error=$5, "
                "finished_at=now() WHERE id=$1::uuid AND worker_id=$2 "
                "AND status='running' RETURNING *",
                job_id, worker_id, status,
                json.dumps(result) if result is not None else None, error)
            return _row(r) if r else None
