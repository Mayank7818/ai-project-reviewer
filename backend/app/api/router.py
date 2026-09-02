"""Aggregates every v1 endpoint router into a single router.

`main.py` mounts this one object, so adding a new resource later means adding
one `include_router` line here and nothing else.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import analysis, health, interview, job, llm, repository

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(llm.router)
api_router.include_router(repository.router)
api_router.include_router(analysis.router)
api_router.include_router(interview.router, prefix="/interview")
api_router.include_router(job.router, prefix="/job")
