"""Business logic layer.

Services hold the actual work - GitHub retrieval, code analysis, LLM prompting -
and are deliberately free of FastAPI imports so they stay unit-testable and
reusable, and so a service can be swapped without touching the HTTP layer.

Planned modules:
    github_service.py    fetch repository metadata, file tree and file contents
    analysis_service.py  orchestrate the review pipeline
    llm/                 provider-agnostic local LLM access (Ollama today)
"""
