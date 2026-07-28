# finGenerator-cfd-manager

The T-FINS CFD **job manager** — a slim FastAPI dispatcher. It queues CFD jobs,
hands them to off-cluster workers, tracks lease/heartbeat/result, and forwards
every completed result to the `findata` corpus.

**This is the manager, not the compute.** The heavy OpenFOAM run happens on the
**EPYC worker** (private `finGenerator-cfd` image); this service holds only a
queue in Postgres and orchestrates. Hence the tiny image, and hence **public**
(non-sensitive plumbing — the data is in CNPG, the CFD code is private).

## Architecture

```
web app --POST /jobs--> fin-cfd-manager (k8s, CNPG queue)
                              ^   |  forward completed result
   lease / heartbeat / result|   v
   EPYC worker (finGenerator-cfd) --> findata corpus
   runs OpenFOAM (the compute)
```

- **Postgres** = operator-managed CNPG (`fin-cfd-manager-cnpg`), via `DATABASE_URL`.
- **Corpus forwarding** = `FINDATA_URL` + `FINDATA_WRITE_TOKEN` (best-effort;
  the job row retains everything for a backfill if a forward fails).

## API

Bearer auth: `CFD_CLIENT_TOKEN` (web app) / `CFD_WORKER_TOKEN` (workers).

| Method | Path | Token | Purpose |
|---|---|---|---|
| POST | `/jobs` | client | Submit a CFD job → `202 {job_id}`. |
| GET | `/jobs/{id}` · `/jobs` | client | Status / list. |
| POST | `/jobs/lease` | worker | Claim a job (`FOR UPDATE SKIP LOCKED`; reclaims expired leases). |
| POST | `/jobs/{id}/heartbeat` · `/result` | worker | Extend lease / finish (a `done` result is forwarded to findata). |
| GET | `/healthz` | — | DB liveness. |

## Local dev

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
uv sync --extra dev
TEST_DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres uv run pytest -q
```

Deployment (CNPG + LoadBalancer VIP for the EPYC worker) lives in `thf-infra`
(`apps/internal/fin-cfd-manager/`). Release: `git tag v0.1.0 && git push origin v0.1.0`.
