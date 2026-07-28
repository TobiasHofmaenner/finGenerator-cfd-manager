"""cfdmanager — the T-FINS CFD job dispatcher (the *manager*, not the compute).

A slim FastAPI job queue over an operator-managed Postgres (CNPG). It accepts
CFD jobs, hands them to off-cluster workers (the heavy OpenFOAM compute runs on
the EPYC, NOT here), tracks lease/heartbeat/result, and forwards every completed
result to the findata corpus. No fingen, no OpenFOAM — pure orchestration.
"""
