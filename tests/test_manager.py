"""Manager tests: queue lease/reclaim/result, auth, and the findata sample
builder. DB tests hit a real Postgres via TEST_DATABASE_URL (skip if unset)."""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("CFD_CLIENT_TOKEN", "c-tok")
os.environ.setdefault("CFD_WORKER_TOKEN", "w-tok")

from cfdmanager import forward  # noqa: E402
from cfdmanager.app import _require  # noqa: E402
from cfdmanager.db import Store  # noqa: E402

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL not set")


# --- auth ---
async def test_worker_token_only_for_worker_endpoints():
    dep = _require("w-tok")
    assert await dep(authorization="Bearer w-tok") is None
    with pytest.raises(HTTPException):
        await dep(authorization="Bearer c-tok")


# --- findata sample builder (no DB) ---
def test_build_sample_shapes_the_corpus_record():
    job = {
        "id": "j1",
        "request": {"fin": {"outline": {"depth": 120}}, "config": "thruster",
                    "speed": 6.4, "angles": [0.0, 4.0]},
        "result": {"rows": [{"alpha": 0.0, "cl": 0.18}], "speed": 6.4,
                   "cfd_setup": {"mesh_level": 2}, "cfd_quality": {"converged": True},
                   "tier0_prediction": {"fingen_version": "0.6.0",
                                        "prediction": {"cl": 0.17}}},
    }
    s = forward.build_sample(job)
    assert s["fin_geometry"] == {"outline": {"depth": 120}}
    assert s["config"] == "thruster"
    assert s["operating_point"] == {"speed": 6.4, "angles": [0.0, 4.0]}
    assert s["cfd_result"] == {"rows": [{"alpha": 0.0, "cl": 0.18}]}
    assert s["cfd_setup"] == {"mesh_level": 2}
    assert s["tier0_prediction"]["fingen_version"] == "0.6.0"
    assert s["provenance"] == {"source": "manager", "job_id": "j1"}


# --- queue (DB) ---
@pytest.fixture
async def store():
    s = await Store.connect(TEST_DSN)
    async with s.pool.acquire() as con:
        await con.execute("TRUNCATE jobs")
    yield s
    await s.close()


@needs_db
async def test_lease_is_atomic_and_reclaims(store):
    a = await store.insert_job({"fin": {}, "n": 1})
    b = await store.insert_job({"fin": {}, "n": 2})
    j1 = await store.lease_job("w1", 1800)
    j2 = await store.lease_job("w2", 1800)
    assert {j1["id"], j2["id"]} == {a, b}
    assert await store.lease_job("w3", 1800) is None  # drained
    async with store.pool.acquire() as con:
        await con.execute(
            "UPDATE jobs SET lease_expires_at=now()-interval '10s' WHERE id=$1::uuid", a)
    assert (await store.lease_job("w3", 1800))["id"] == a  # expired -> reclaimed


@needs_db
async def test_result_requires_owner_and_returns_job(store):
    jid = await store.insert_job({"fin": {"x": 1}, "angles": [0.0]})
    await store.lease_job("w1", 1800)
    assert await store.submit_result(jid, "w2", "done", {"rows": []}, None) is None
    job = await store.submit_result(jid, "w1", "done",
                                    {"rows": [{"cl": 0.1}]}, None)
    assert job is not None and job["status"] == "done"
    assert job["request"]["fin"] == {"x": 1}   # full row returned for forwarding


def test_job_request_requires_exactly_one_geometry():
    """A job is either a single blade or a whole set — never both, never
    neither. Silently accepting both would let the worker pick and the corpus
    record a geometry the client did not intend."""
    import pydantic

    from cfdmanager.models import JobRequest

    fin = {"outline": {}, "foil": {}, "grooves": {}}
    fin_set = {"config": "thruster", "side": fin, "center": fin}

    assert JobRequest(fin=fin).fin_set is None
    assert JobRequest(fin_set=fin_set).fin is None

    with pytest.raises(pydantic.ValidationError):
        JobRequest()
    with pytest.raises(pydantic.ValidationError):
        JobRequest(fin=fin, fin_set=fin_set)


def test_build_sample_stores_the_whole_cluster_for_a_set_job():
    """findata hashes fin_geometry: a SET sample must carry the full placed
    cluster (blades + placement), or two different clusters collapse onto one
    sample. config falls back to the set's own config."""
    fin = {"outline": {}, "foil": {}, "grooves": {}}
    fin_set = {"config": "thruster", "side": fin, "center": fin,
               "toe": 3.5, "cant": 8.0}
    job = {
        "id": "job-1",
        "request": {"fin_set": fin_set, "speed": 7.0, "angles": [0.0, 4.0]},
        "result": {"kind": "set", "speed": 7.0,
                   "rows": [{"alpha": 0.0, "per_slot": {}}],
                   "cfd_setup": {"kind": "set", "placement": {"toe": 3.5}}},
    }
    sample = forward.build_sample(job)
    assert sample["fin_geometry"] == fin_set          # the CLUSTER, not a blade
    assert sample["config"] == "thruster"             # inferred from the set
    assert sample["cfd_setup"]["kind"] == "set"
    assert sample["provenance"]["job_id"] == "job-1"

    # A single-fin job is unchanged.
    single = forward.build_sample(
        {"id": "job-2", "request": {"fin": fin, "config": "single"},
         "result": {"rows": []}})
    assert single["fin_geometry"] == fin
