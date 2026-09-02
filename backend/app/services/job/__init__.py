"""Job description intelligence.

Layered so every number is deterministic and only the prose comes from a model:

    vocabulary.py  canonical skills, aliases, categories        no I/O, no model
    parser.py      description -> structured requirements       no model (enrichment optional)
    matcher.py     requirements + Step 4 evidence -> statuses   no I/O, no model
    scoring.py     statuses -> match score and readiness        pure arithmetic
    seeds.py       match -> job-specific question seeds         no I/O, no model
    prompts.py     the two small enrichment prompts
    service.py     orchestration, reusing Steps 4 and 5

Only `service.py` is used by the API layer.
"""

from app.services.job.service import JobService, get_job_service

__all__ = ["JobService", "get_job_service"]
