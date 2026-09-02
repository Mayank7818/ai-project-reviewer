"""GitHub integration.

Layered deliberately so each piece is independently testable:

    url_parser.py   str -> validated (owner, repo)         no I/O
    file_filter.py  tree entries -> a bounded, ranked list  no I/O
    redaction.py    file text -> text with secrets masked   no I/O
    client.py       thin async HTTP client + error mapping  I/O
    service.py      orchestrates the four above             I/O

Only `service.py` is used by the API layer.
"""

from app.services.github.service import GitHubService, get_github_service

__all__ = ["GitHubService", "get_github_service"]
