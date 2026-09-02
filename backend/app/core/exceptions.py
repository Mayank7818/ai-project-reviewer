"""Application error types and the handlers that turn them into responses.

Goal: every failure leaving this API - expected or not - is a JSON body with
the same shape, so the frontend has exactly one error contract to parse.

    {"error": {"code": "REPOSITORY_NOT_FOUND", "message": "...", "details": {}}}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for every error this application raises deliberately.

    Subclasses set a machine-readable `code` and an HTTP `status_code`; the
    handler below does the serialising. Raising `AppError` (rather than
    `HTTPException`) keeps service-layer code free of web-framework details.
    """

    code: str = "INTERNAL_ERROR"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


class InvalidRepositoryUrlError(AppError):
    """The submitted string is not a usable public GitHub repository URL."""

    code = "INVALID_REPOSITORY_URL"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    message = "The provided URL is not a valid public GitHub repository URL."


class RepositoryNotFoundError(AppError):
    """The repository does not exist, or is private/inaccessible."""

    code = "REPOSITORY_NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Repository not found or not publicly accessible."


class ExternalServiceError(AppError):
    """An upstream dependency (GitHub, LLM provider) failed or timed out."""

    code = "EXTERNAL_SERVICE_ERROR"
    status_code = status.HTTP_502_BAD_GATEWAY
    message = "An upstream service is unavailable. Please try again."


class GitHubRateLimitError(AppError):
    """GitHub's rate limit is exhausted for this token (or this IP)."""

    code = "GITHUB_RATE_LIMIT"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = (
        "GitHub API rate limit exceeded. Set a GITHUB_TOKEN to raise the limit "
        "from 60 to 5000 requests per hour, or wait for the limit to reset."
    )


class GitHubAuthError(AppError):
    """The configured GITHUB_TOKEN was rejected by GitHub."""

    code = "GITHUB_AUTH_ERROR"
    status_code = status.HTTP_502_BAD_GATEWAY
    message = (
        "GitHub rejected the configured token. Check GITHUB_TOKEN, or unset it "
        "to fall back to unauthenticated public access."
    )


class LLMUnavailableError(AppError):
    """The local LLM server (Ollama) is not running, or the model is missing."""

    code = "LLM_UNAVAILABLE"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = (
        "The local LLM service is unavailable. Make sure Ollama is running "
        "and the configured model has been pulled."
    )


class LLMModelNotFoundError(AppError):
    """Ollama is running, but the configured model has not been pulled."""

    code = "LLM_MODEL_NOT_FOUND"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = (
        "The configured model is not available in Ollama. Pull it with "
        "`ollama pull <model>`, or set OLLAMA_MODEL to a model you already have."
    )


class LLMInvalidResponseError(AppError):
    """The model replied, but not with usable structured output."""

    code = "LLM_INVALID_RESPONSE"
    status_code = status.HTTP_502_BAD_GATEWAY
    message = (
        "The AI model returned a response that could not be parsed as a valid "
        "analysis. Try again, or use a larger model."
    )


class SessionNotFoundError(AppError):
    """The interview session does not exist, expired, or is in the wrong state."""

    code = "SESSION_NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND
    message = (
        "That interview session does not exist, or it has expired. Sessions are "
        "held in memory and are lost when the backend restarts."
    )


class InsufficientEvidenceError(AppError):
    """The repository does not support the requested operation.

    Raised rather than falling back to generic questions: an interview that is
    not grounded in the candidate's repository is the one thing this product
    must never produce.
    """

    code = "INSUFFICIENT_EVIDENCE"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    message = "Insufficient repository evidence."


class InvalidJobDescriptionError(AppError):
    """The submitted job description cannot be analysed."""

    code = "INVALID_JOB_DESCRIPTION"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    message = "That job description could not be analysed."


def _error_body(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


#: Keys of a Pydantic error that describe the *problem*. Everything else -
#: `input` above all - describes the submitted value.
_SAFE_ERROR_KEYS = ("loc", "msg", "type")


def _safe_validation_errors(exc: RequestValidationError) -> list[dict]:
    """Report which field failed and why, never what was in it.

    Pydantic includes the offending value under `input`, and FastAPI would
    return it verbatim. For most fields that is harmless; for
    `POST /job/match` it means echoing the candidate's entire job description
    back in an error body that may well be logged by whatever sits in front of
    this service. Where a field failed and why is all a caller needs.
    """
    return [
        {key: error[key] for key in _SAFE_ERROR_KEYS if key in error}
        for error in jsonable_encoder(exc.errors())
    ]


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers so all four failure classes share one response shape."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        logger.warning("AppError [%s]: %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body("HTTP_ERROR", str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body(
                "VALIDATION_ERROR",
                "Request payload failed validation.",
                {"errors": _safe_validation_errors(exc)},
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        # Log the full traceback, but never leak internals to the client.
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("INTERNAL_ERROR", "An unexpected error occurred."),
        )
